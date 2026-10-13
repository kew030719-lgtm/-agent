"""用合成资料和临时数据库验收真实 DeepSeek + CLI + LangGraph 链路。"""

import io
import os
import sqlite3
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.auth import DB_PATH, User
from app.config import load_config
from app.knowledge.documents import add_knowledge_file
from app.knowledge.embeddings import LocalEmbedder, index_document
from app.knowledge.vector_store import MilvusVectorStore
from main import run_cli
from scripts.eval_rag import create_sample_files


def run_session(
    db: sqlite3.Connection, user: User, questions: list[str],
) -> list[str]:
    """仅模拟登录身份和键盘输入；路由、检索、生成均运行真实实现。"""
    output = io.StringIO()
    results: list[str] = []
    offset = 0
    current = 0

    def read_question(prompt: str) -> str:
        nonlocal current, offset
        text = output.getvalue()
        if current:
            results.append(text[offset:])
        offset = len(text)
        if current == len(questions):
            return "exit"
        question = questions[current]
        current += 1
        return question

    with (
        patch("main.prompt_login", return_value=user),
        patch("main.read_input", side_effect=read_question),
        redirect_stdout(output),
    ):
        run_cli(db)
    return results


def main() -> None:
    load_config()
    if not os.getenv("DEEPSEEK_API_KEY"):
        raise RuntimeError("请先在项目 .env 或环境变量中配置 DEEPSEEK_API_KEY")
    embedder = LocalEmbedder(DB_PATH.parent)
    with TemporaryDirectory() as temporary:
        root = Path(temporary)
        with patch.dict(os.environ, {
            "MILVUS_URI": str(root / "vectors.db"),
            "MILVUS_TOKEN": "",
            "MILVUS_COLLECTION": "live_eval_knowledge",
        }), sqlite3.connect(":memory:") as db:
            db.execute("PRAGMA foreign_keys = ON")
            db.execute("CREATE TABLE users(username TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'employee', enabled INTEGER NOT NULL DEFAULT 1, department_id INTEGER, job_level TEXT NOT NULL DEFAULT 'employee')")
            db.executemany("INSERT INTO users(username) VALUES (?)", [("admin",), ("employee",)])
            db.execute("UPDATE users SET role='admin' WHERE username='admin'")
            admin: User = {"username": "admin", "role": "admin"}
            employee: User = {"username": "employee", "role": "employee"}
            with MilvusVectorStore() as store:
                for path, scope in create_sample_files(root):
                    document_id = add_knowledge_file(db, admin, path, scope, root)
                    index_document(db, document_id, embedder, store)

            employee_questions = [
                "你是谁？", "现在有什么权限？", "公司的年假申请流程是什么？",
                "那需要谁审批？", "公司婚假一共有几天？", "报销申请需要什么材料？",
            ]
            employee_results = run_session(db, employee, employee_questions)
            admin_questions = ["报销申请需要什么材料？"]
            admin_results = run_session(db, admin, admin_questions)
            for role, questions, results in (
                ("employee", employee_questions, employee_results),
                ("admin", admin_questions, admin_results),
            ):
                for question, result in zip(questions, results):
                    print(f"[{role}] {question}\n{result.strip()}\n", flush=True)

            if len(employee_results) != 6 or len(admin_results) != 1:
                raise RuntimeError("对话未正常完成")
            checks = {
                "助手身份": "[identity]" in employee_results[0],
                "当前账号权限": "角色：employee" in employee_results[1]
                    and "kb:finance" not in employee_results[1],
                "知识回答与来源": all(word in employee_results[2] for word in ("来源：年假", "请假单", "主管")),
                "多轮追问": "来源：年假" in employee_results[3] and "主管" in employee_results[3],
                '无资料先说明': '回复“转人工”' in employee_results[4],
                '员工无法获取财务资料': '回复“转人工”' in employee_results[5]
                    and "发票" not in employee_results[5],
                "管理员可获取财务资料": "来源：报销" in admin_results[0]
                    and "发票" in admin_results[0],
                "未确认不生成待办": db.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0] == 0,
            }
            for label, passed in checks.items():
                print(f"{'通过' if passed else '失败'}：{label}")
            print(f"真实 API 验收：{sum(checks.values())}/{len(checks)}")
            if not all(checks.values()):
                raise RuntimeError("真实问答验收未全部通过，请检查上方具体结果")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # SDK 异常可能包含请求信息，不在验收输出中回显底层异常正文。
        print(f"验收未完成（{type(exc).__name__}），请检查配置及上方结果。")
        raise SystemExit(1) from None
