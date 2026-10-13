import getpass
import os
import sqlite3
import sys
import traceback
from contextlib import closing
from functools import partial
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from zipfile import BadZipFile

from app.auth import (
    ROLE_PERMISSIONS,
    User,
    authenticate,
    connect,
    create_first_admin,
    create_user,
    has_permission,
)
from app.cli_input import read_input
from app.config import configured_database_path, load_config, model_data_root
from app.errors import ServiceUnavailableError
from app.ingestion.jobs import JobConflictError, create_reindex_job, ensure_ingestion_schema, publish_job

from openai import OpenAI, OpenAIError
from pymilvus.exceptions import MilvusException

from app.agent.graph import AssistantInput, build_graph
from app.agent.operation_dialogue import OperationKind, OperationUpdate, interpret_operation
from app.agent.meeting_dialogue import MeetingRequest, interpret_meeting
from app.agent.router import route_question
from app.tools.meeting import book_meeting_room
from app.knowledge.answer import answer_from_knowledge
from app.knowledge.documents import add_knowledge_file, ensure_knowledge_schema
from app.knowledge.embeddings import LocalEmbedder, index_document
from app.knowledge.lifecycle import delete_knowledge_document, replace_knowledge_file
from app.knowledge.feedback import (
    ensure_feedback_schema,
    list_recent_feedback,
    record_feedback,
)
from app.knowledge.usage import (
    ensure_usage_schema,
    list_usage_summary,
    record_usage,
)
from app.knowledge.reranker import LocalReranker
from app.knowledge.query import expand_followup, is_followup
from app.knowledge.vector_store import MilvusVectorStore

from app.tools.ticket_workflow import query_own_tickets
from app.tools.it import create_it_ticket, ensure_it_schema
from app.agent.leave_dialogue import interpret_leave
from app.tools.leave_workflow import create_leave_request, preview_leave, query_own_leaves, leave_balance_summary
from app.tools.leave import (
    ensure_leave_schema,
    get_own_leave_balance,
    set_leave_balance,
)
from app.tools.handoff import (
    create_handoff,
    ensure_handoff_schema,
    list_pending_handoffs,
    resolve_handoff,
)
from app.tools.expense import create_expense_request, ensure_expense_schema
from app.tools.notifications import backfill_notifications, query_notifications
from app.tools.work_center import query_todos
from app.tools.meeting import (
    MeetingRoom,
    add_meeting_room,
    ensure_meeting_schema,
    list_meeting_rooms,
    add_meeting_booking,
    list_available_meeting_rooms,
)

def _database_root(db: sqlite3.Connection) -> Path:
    """从实际连接推导文件根；内存连接仅用于测试时回退配置。"""
    for _sequence, name, filename in db.execute("PRAGMA database_list"):
        if name == "main" and filename:
            return Path(filename).resolve().parent
    return configured_database_path().parent


def prompt_login(db: sqlite3.Connection) -> User | None:
    username = read_input("账号：").strip()
    password = getpass.getpass("密码：")
    return authenticate(db, username, password)


def init_admin_cli(db: sqlite3.Connection) -> None:
    username = read_input("首位管理员账号：").strip()
    password = getpass.getpass("设置密码（至少 8 位）：")
    create_first_admin(db, username, password)
    print("管理员已创建")


def add_user_cli(db: sqlite3.Connection) -> None:
    actor = prompt_login(db)
    if actor is None:
        print("登录失败")
        return

    if not has_permission(actor, "user:create"):
        print("当前账号无权创建用户")
        return

    username = read_input("新账号：").strip()
    role = read_input("角色（employee/hr/it/finance/admin）：").strip()
    password = getpass.getpass("设置密码（至少 8 位）：")
    create_user(db, actor, username, password, role)
    print("账号已创建")

def set_leave_cli(db: sqlite3.Connection) -> None:
    actor = prompt_login(db)
    if actor is None:
        print("登录失败")
        return
    if not has_permission(actor, "leave:write_all"):
        print("当前账号无权维护请假余额")
        return

    username = read_input("员工账号：").strip()
    try:
        days = float(read_input("请假余额（天）：").strip())
    except ValueError:
        print("请输入数字")
        return

    ensure_leave_schema(db)
    set_leave_balance(db, actor, username, days)
    print(f"已将 {username} 的请假余额设为 {days:g} 天")

def list_handoffs_cli(db: sqlite3.Connection) -> None:
    actor = prompt_login(db)
    if actor is None:
        print("登录失败")
        return
    if not has_permission(actor, "handoff:manage"):
        print("当前账号无权查看转人工待办")
        return

    ensure_handoff_schema(db)
    items = list_pending_handoffs(db, actor)
    if not items:
        print("暂无待处理的转人工问题")
        return

    for item in items:
        print(
            f"#{item['id']} | 提交人：{item['created_by']} | "
            f"时间：{item['created_at']}"
        )
        print(f"问题：{item['question']}")
        print(f"原因：{item['reason']}")

def resolve_handoff_cli(db: sqlite3.Connection) -> None:
    actor = prompt_login(db)
    if actor is None:
        print("登录失败")
        return
    if not has_permission(actor, "handoff:manage"):
        print("当前账号无权处理转人工待办")
        return

    try:
        handoff_id = int(read_input("待办编号：").strip())
    except ValueError:
        print("待办编号必须是整数")
        return
    if handoff_id <= 0:
        print("待办编号必须大于 0")
        return

    ensure_handoff_schema(db)
    reply = read_input("给员工的处理回复（必填）：").strip()
    if resolve_handoff(db, actor, handoff_id, reply):
        print(f"待办 #{handoff_id} 已回复并处理完成")
    else:
        print("待办不存在或已处理完成")

def add_room_cli(db: sqlite3.Connection) -> None:
    actor = prompt_login(db)
    if actor is None:
        print("登录失败")
        return
    if not has_permission(actor, "meeting:write"):
        print("当前账号无权维护会议室")
        return

    name = read_input("会议室名称：").strip()
    location = read_input("位置：").strip()
    try:
        capacity = int(read_input("容纳人数：").strip())
    except ValueError:
        print("容纳人数必须是整数")
        return

    ensure_meeting_schema(db)
    room_id = add_meeting_room(db, actor, name, location, capacity)
    print(f"会议室 #{room_id} 已录入")

def add_booking_cli(db: sqlite3.Connection) -> None:
    actor = prompt_login(db)
    if actor is None:
        print("登录失败")
        return
    if not has_permission(actor, "meeting:write"):
        print("当前账号无权维护会议室预订")
        return

    try:
        room_id = int(read_input("会议室编号：").strip())
    except ValueError:
        print("会议室编号必须是整数")
        return

    start_at = read_input("开始时间（YYYY-MM-DD HH:MM）：").strip()
    end_at = read_input("结束时间（YYYY-MM-DD HH:MM）：").strip()

    ensure_meeting_schema(db)
    booking_id = add_meeting_booking(
        db, actor, room_id, start_at, end_at
    )
    print(f"会议室预订记录 #{booking_id} 已录入")


def add_knowledge_cli(db: sqlite3.Connection) -> None:
    actor = prompt_login(db)
    if actor is None:
        print("登录失败")
        return
    if not has_permission(actor, "kb:write"):
        print("当前账号无权导入知识文件")
        return
    path = Path(read_input("文件路径：").strip()).expanduser()
    scope = read_input("可见范围（general/hr/it/finance）：").strip()
    document_id = add_knowledge_file(db, actor, path, scope, _database_root(db))
    try:
        with MilvusVectorStore() as store:
            count = index_document(
                db, document_id, LocalEmbedder(model_data_root(_database_root(db))), store
            )
    except ValueError as exc:
        print(f"文件 #{document_id} 已导入，但切片无法索引：{exc}")
        print("请整理过长内容后重新导入该文件")
        return
    except (RuntimeError, OSError, MilvusException) as exc:
        print(f"文件 #{document_id} 已导入，但向量索引失败：{exc}")
        print("检查模型和 Milvus 服务后运行：python main.py reindex-knowledge")
        return
    print(f"知识文件 #{document_id} 已导入，已建立 {count} 个向量切片")


def prepare_model_cli() -> None:
    embedder = LocalEmbedder(model_data_root(configured_database_path().parent), download=True)
    embedder.embed(["知识库模型准备完成"])
    print("本地中文向量模型已准备好")


def prepare_reranker_cli() -> None:
    reranker = LocalReranker(model_data_root(configured_database_path().parent), download=True)
    reranker.scores("请假制度", ["员工请假制度说明"])
    print("本地重排模型已准备好")


def update_knowledge_cli(db: sqlite3.Connection) -> None:
    actor = prompt_login(db)
    if actor is None:
        print("登录失败")
        return
    if not has_permission(actor, "kb:write"):
        print("当前账号无权维护知识库")
        return
    document_id = int(read_input("要替换的知识文件编号：").strip())
    path = Path(read_input("新版文件路径：").strip()).expanduser()
    with MilvusVectorStore() as store:
        new_id, count = replace_knowledge_file(
            db, actor, document_id, path, _database_root(db),
            LocalEmbedder(model_data_root(_database_root(db))), store,
        )
    print(f"知识文件 #{document_id} 已替换为 #{new_id}，已索引 {count} 个切片")


def delete_knowledge_cli(db: sqlite3.Connection) -> None:
    actor = prompt_login(db)
    if actor is None:
        print("登录失败")
        return
    if not has_permission(actor, "kb:write"):
        print("当前账号无权维护知识库")
        return
    document_id = int(read_input("要删除的知识文件编号：").strip())
    with MilvusVectorStore() as store:
        delete_knowledge_document(db, actor, document_id, _database_root(db), store)
    print(f"知识文件 #{document_id} 及其向量已删除")


def reindex_knowledge_cli(db: sqlite3.Connection) -> None:
    actor = prompt_login(db)
    if actor is None:
        print("登录失败")
        return
    if not has_permission(actor, "kb:write"):
        print("当前账号无权维护知识索引")
        return
    ensure_knowledge_schema(db)
    ensure_ingestion_schema(db)
    db.commit()
    document_ids = [row[0] for row in db.execute(
        "SELECT d.id FROM knowledge_documents d WHERE d.created_by=? AND "
        "EXISTS(SELECT 1 FROM knowledge_chunks c WHERE c.document_id=d.id) ORDER BY d.id",
        (actor["username"],),
    )]
    if not document_ids:
        print("当前账号没有可重建的自有知识文档")
        return
    for document_id in document_ids:
        try:
            job = create_reindex_job(db, actor, document_id, str(uuid4()))
        except JobConflictError as exc:
            print(f"文档 #{document_id} 暂未提交：{exc}")
            continue
        delivered = publish_job(db, str(job["id"]))
        print(f"文档 #{document_id} 的后台重建任务已保存：{job['id']}")
        if not delivered:
            print("队列投递未完成，请在知识库建库任务中查看状态并重试")


def knowledge_feedback_cli(db: sqlite3.Connection) -> None:
    actor = prompt_login(db)
    if actor is None:
        print("登录失败")
        return
    items = list_recent_feedback(db, actor)
    if not items:
        print("暂无知识回答反馈")
        return
    for item in items:
        print(
            f"#{item['id']} | {item['username']} | {item['rating']} | "
            f"{item['created_at']}"
        )
        print(f"问题：{item['question']}")
        print(f"回答：{item['answer']}")


def knowledge_stats_cli(db: sqlite3.Connection) -> None:
    actor = prompt_login(db)
    if actor is None:
        print("登录失败")
        return
    items = list_usage_summary(db, actor)
    if not items:
        print("暂无知识问答统计")
        return
    for item in items:
        label = "已回答" if item["outcome"] == "answered" else "转人工"
        print(
            f"{label}：{item['count']} 次，平均耗时 "
            f"{item['average_ms']:.0f} 毫秒"
        )


def run_cli(db: sqlite3.Connection) -> None:
    load_config()
    user = prompt_login(db)
    if user is None:
        print("登陆失败")
        return

    print(f"登录成功：{user['username']}，角色：{user['role']}")
    print("权限：", ", ".join(sorted(ROLE_PERMISSIONS[user["role"]])))

    ensure_it_schema(db)
    ensure_leave_schema(db)
    ensure_handoff_schema(db)
    ensure_expense_schema(db)
    ensure_meeting_schema(db)
    ensure_knowledge_schema(db)
    ensure_feedback_schema(db)
    ensure_usage_schema(db)
    backfill_notifications(db)

    api_key = os.getenv("DEEPSEEK_API_KEY") or getpass.getpass("DeepSeek API Key:")
    if not api_key:
        print("缺少 DeepSeek API Key")
        return

    with OpenAI(
            api_key=api_key,
            base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            timeout=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "30")),
            max_retries=int(os.getenv("DEEPSEEK_MAX_RETRIES", "1")),
    ) as client:
        embedder: LocalEmbedder | None = None
        vector_store: MilvusVectorStore | None = None
        reranker: LocalReranker | None = None
        reranker_checked = False
        source_ids: list[int] = []
        last_knowledge_answer: str | None = None
        last_knowledge_question: str | None = None

        def answer_knowledge(user: User, question: str) -> str | None:
            nonlocal embedder, vector_store, reranker, reranker_checked
            nonlocal last_knowledge_answer, last_knowledge_question
            try:
                if embedder is None:
                    embedder = LocalEmbedder(model_data_root(_database_root(db)))
                if vector_store is None:
                    vector_store = MilvusVectorStore()
                if not reranker_checked:
                    reranker_checked = True
                    try:
                        reranker = LocalReranker(model_data_root(_database_root(db)))
                    except (RuntimeError, ValueError, OSError):
                        print("本地重排模型未准备，按向量相似度排序")
                followup = (
                    last_knowledge_question is not None and is_followup(question)
                )
                effective_question = (
                    expand_followup(last_knowledge_question, question)
                    if followup and last_knowledge_question is not None
                    else question
                )
                answer = answer_from_knowledge(
                    client, db, user, effective_question,
                    embedder, vector_store, reranker, source_ids=source_ids,
                )
                last_knowledge_answer = answer
                if answer and not followup:
                    last_knowledge_question = question
                return answer
            except ServiceUnavailableError:
                raise
            except (RuntimeError, OSError, MilvusException, sqlite3.Error) as exc:
                raise ServiceUnavailableError(
                    "知识检索服务不可用，请检查本地模型和 Milvus 后重试。"
                ) from exc

        def route(question: str) -> str:
            if last_knowledge_question is not None and is_followup(question):
                return "knowledge"
            try:
                return route_question(client, question)
            except UnicodeError as exc:
                raise ServiceUnavailableError("DeepSeek 路由响应解码失败，请重试。") from exc

        def create_ticket(user: User, description: str, *, request_key: str | None = None) -> int:
            return create_it_ticket(db, user, description, request_key=request_key)

        def get_balance(user: User) -> float | None:
            return get_own_leave_balance(db, user)
        def create_handoff_record(
            user: User,
            question: str,
            reason: str,
        ) -> int:
            return create_handoff(db, user, question, reason)
        def create_expense(
            user: User,
            amount: str,
            description: str,
            *, request_key: str | None = None,
        ) -> int:
            return create_expense_request(db, user, amount, description, request_key=request_key)
        def get_rooms(
            user: User,
            min_capacity: int,
        ) -> list[MeetingRoom]:
            return list_meeting_rooms(db, user, min_capacity)
        def get_available_rooms(
            user: User,
            min_capacity: int,
            start_at: str,
            end_at: str,
        ) -> list[MeetingRoom]:
            return list_available_meeting_rooms(
                db, user, min_capacity, start_at, end_at
            )

        def parse_meeting(question: str, context: str) -> MeetingRequest:
            return interpret_meeting(client, question, context)

        def parse_operation(question: str, context: str, kind: OperationKind | None) -> OperationUpdate:
            return interpret_operation(client, question, context, kind)

        def book_room(user: User, room_id: int, start_at: str, end_at: str, capacity: int) -> int:
            return book_meeting_room(db, user, room_id, start_at, end_at, capacity)

        def query_tickets(user: User, ticket_id: int | None) -> str:
            return query_own_tickets(db, user, ticket_id)

        graph = build_graph(
            route,
            create_ticket,
            get_balance,
            create_handoff_record,
            create_expense,
            get_rooms,
            get_available_rooms,
            answer_knowledge,
            parse_meeting,
            book_room,
            parse_operation,
            query_tickets,
            lambda text, context: interpret_leave(client, text, context),
            partial(preview_leave, db), partial(create_leave_request, db),
            partial(query_own_leaves, db), partial(leave_balance_summary, db),
            partial(query_todos, db), partial(query_notifications, db),
        )

        config = {"configurable": {"thread_id": uuid4().hex}}
        pending_feedback: tuple[str, str] | None = None

        try:
            while True:
                question = read_input("员工问题（exit 退出）：").strip()
                if question.lower() == "exit":
                    break
                if not question:
                    continue
                if question.startswith(("反馈：", "反馈:")):
                    rating = question[3:].strip()
                    if pending_feedback is None:
                        print("请先获取一条知识回答，再输入反馈：有用或反馈：无用")
                    elif rating not in {"有用", "无用"}:
                        print("反馈只能是：反馈：有用 或 反馈：无用")
                    else:
                        feedback_id = record_feedback(
                            db, user, *pending_feedback, rating, source_ids or None
                        )
                        pending_feedback = None
                        print(f"反馈 #{feedback_id} 已记录")
                    continue

                pending_feedback = None
                last_knowledge_answer = None

                state: AssistantInput = {
                    "user": user,
                    "question": question,
                }

                started = perf_counter()
                try:
                    result = graph.invoke(state, config=config)
                except (ServiceUnavailableError, OpenAIError) as exc:
                    last_knowledge_question = None
                    message = str(exc) if isinstance(exc, ServiceUnavailableError) else "模型服务调用失败，请稍后重试。"
                    print(f"服务异常：{message} 本次未创建人工待办。")
                    continue
                except ValueError as exc:
                    last_knowledge_question = None
                    print(f"问题处理失败：{exc}")
                    continue

                print(f"[{result['domain']}] {result['answer']}")
                if result["domain"] == "human" or (result["domain"] == "knowledge" and last_knowledge_answer is not None):
                    try:
                        record_usage(
                            db, user,
                            "answered" if last_knowledge_answer is not None
                            else "handoff",
                            int((perf_counter() - started) * 1000),
                        )
                    except sqlite3.Error as exc:
                        print(f"知识问答统计记录失败：{exc}")
                if result["domain"] != "knowledge" or last_knowledge_answer is None:
                    last_knowledge_question = None
                if last_knowledge_answer is not None:
                    pending_feedback = (question, last_knowledge_answer)
                    print("可输入：反馈：有用 或 反馈：无用")
        finally:
            if vector_store is not None:
                vector_store.close()


def main() -> None:
    load_config()
    if len(sys.argv) != 2 or sys.argv[1] not in {
        "init-admin", "add-user", "set-leave", "run",
        "handoffs", "resolve-handoff","add-room","add-booking",
        "add-knowledge", "update-knowledge", "delete-knowledge",
        "prepare-model", "prepare-reranker", "reindex-knowledge",
        "knowledge-feedback",
        "knowledge-stats",
    }:
        raise SystemExit(
            "用法：python main.py init-admin | add-user | set-leave | "
            "run | handoffs | resolve-handoff | add-room | add-booking | "
            "add-knowledge | update-knowledge | delete-knowledge | "
            "prepare-model | prepare-reranker | reindex-knowledge | "
            "knowledge-feedback | knowledge-stats"
        )

    try:
        with closing(connect(configured_database_path())) as db:
            if sys.argv[1] == "init-admin":
                init_admin_cli(db)
            elif sys.argv[1] == "add-user":
                add_user_cli(db)
            elif sys.argv[1] == "set-leave":
                set_leave_cli(db)
            elif sys.argv[1] == "handoffs":
                list_handoffs_cli(db)
            elif sys.argv[1] == "resolve-handoff":
                resolve_handoff_cli(db)
            elif sys.argv[1] == "add-room":
                add_room_cli(db)
            elif sys.argv[1] == "add-booking":
                add_booking_cli(db)
            elif sys.argv[1] == "add-knowledge":
                add_knowledge_cli(db)
            elif sys.argv[1] == "update-knowledge":
                update_knowledge_cli(db)
            elif sys.argv[1] == "delete-knowledge":
                delete_knowledge_cli(db)
            elif sys.argv[1] == "prepare-model":
                prepare_model_cli()
            elif sys.argv[1] == "prepare-reranker":
                prepare_reranker_cli()
            elif sys.argv[1] == "reindex-knowledge":
                reindex_knowledge_cli(db)
            elif sys.argv[1] == "knowledge-feedback":
                knowledge_feedback_cli(db)
            elif sys.argv[1] == "knowledge-stats":
                knowledge_stats_cli(db)
            else:
                run_cli(db)
    except (
        ValueError, PermissionError, RuntimeError, OSError, BadZipFile,
        MilvusException,
        sqlite3.IntegrityError,
    ) as exc:
        print(f"操作失败：{exc}")
        if os.getenv("EMPLOYEE_ASSISTANT_DEBUG") == "1":
            traceback.print_exception(exc)


if __name__ == "__main__":
    main()
