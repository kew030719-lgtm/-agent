import json
import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from openai import OpenAIError

from app.errors import ServiceUnavailableError

from app.knowledge.answer import _build_evidence, _citation_retry_instruction, answer_from_knowledge
from app.knowledge.documents import add_knowledge_file
from app.knowledge.feedback import list_recent_feedback, record_feedback
from app.knowledge.retrieval import SearchHit, mentioned_documents, search_knowledge
from app.knowledge.usage import list_usage_summary
from main import run_cli


class FakeEmbedder:
    def embed_query(self, question: str) -> np.ndarray:
        return np.ones(512, dtype=np.float32)

    def embed(self, texts: list[str]) -> list[bytes]:
        return [np.ones(512, dtype="<f4").tobytes() for _ in texts]


class FakeStore:
    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db

    def search(
        self, vector: list[float], scopes: tuple[str, ...], limit: int,
        document_ids: tuple[int, ...] = (),
    ) -> list[dict[str, int | float]]:
        rows = self.db.execute("SELECT id FROM knowledge_chunks ORDER BY id DESC")
        return [{"id": row[0], "score": 0.8} for row in rows]


class FakeReranker:
    def scores(self, question: str, passages: list[str]) -> list[float]:
        return [1.0 if "年假" in text else 0.0 for text in passages]


class FakeClient:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload
        self.prompts: list[str] = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, **kwargs: object) -> SimpleNamespace:
        messages = kwargs["messages"]
        self.prompts.append(messages[1]["content"])
        message = SimpleNamespace(
            content=json.dumps(self.payload, ensure_ascii=False)
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class RagFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        environment = patch.dict("os.environ", {"RAG_QUERY_REWRITE_ENABLED": "false"})
        environment.start()
        self.addCleanup(environment.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = sqlite3.connect(":memory:")
        self.db.execute("PRAGMA foreign_keys = ON")
        self.db.execute("CREATE TABLE users(username TEXT PRIMARY KEY, role TEXT NOT NULL DEFAULT 'employee', enabled INTEGER NOT NULL DEFAULT 1, department_id INTEGER, job_level TEXT NOT NULL DEFAULT 'employee')")
        self.db.execute("INSERT INTO users(username) VALUES ('admin')")
        self.db.execute("UPDATE users SET role='admin' WHERE username='admin'")
        self.db.execute("INSERT INTO users(username) VALUES ('employee')")
        admin = {"username": "admin", "role": "admin"}
        general = self.root / "年假.md"
        general.write_text("# 年假制度\n员工每年有 10 天年假。", encoding="utf-8")
        finance = self.root / "财务.md"
        finance.write_text("# 财务数据\n秘密财务数字 9999。", encoding="utf-8")
        add_knowledge_file(self.db, admin, general, "general", self.root)
        add_knowledge_file(self.db, admin, finance, "finance", self.root)
        self.employee = {"username": "employee", "role": "employee"}

    def tearDown(self) -> None:
        self.db.close()
        self.temp.cleanup()

    def test_valid_quote_and_permission(self) -> None:
        client = FakeClient({
            "answer": "员工每年有 10 天年假。",
            "evidence": [{"id": 1, "quote": "员工每年有 10 天年假"}],
            "needs_human": False,
        })
        answer = answer_from_knowledge(
            client, self.db, self.employee, "年假制度是什么？",
            FakeEmbedder(), FakeStore(self.db), FakeReranker(),
        )
        self.assertIn("10 天年假", answer)
        self.assertIn("来源：年假", answer)
        self.assertNotIn("秘密财务数字", client.prompts[0])

    def test_partial_evidence_preserves_verified_facts_and_marks_missing_part(self) -> None:
        client = FakeClient({
            "answer": "员工每年有 10 天年假；当前检索资料未说明具体审批人。",
            "evidence": [{"id": 1, "quote": "员工每年有 10 天年假"}],
            "needs_human": False, "incomplete": True,
        })
        status: dict[str, bool] = {}
        sources: list[int] = []
        answer = answer_from_knowledge(client, self.db, self.employee, "有几天年假，由谁审批？",
                                      FakeEmbedder(), FakeStore(self.db), FakeReranker(),
                                      source_ids=sources, answer_status=status)
        self.assertIn("10 天年假", answer)
        self.assertIn("未说明", answer)
        self.assertEqual(status, {"incomplete": True})
        self.assertTrue(sources)

    def test_invented_quote_is_rejected(self) -> None:
        client = FakeClient({
            "answer": "员工每年有 30 天年假。",
            "evidence": [{"id": 1, "quote": "员工每年有 30 天年假"}],
            "needs_human": False,
        })
        answer = answer_from_knowledge(
            client, self.db, self.employee, "年假制度是什么？",
            FakeEmbedder(), FakeStore(self.db), FakeReranker(),
        )
        self.assertIsNone(answer)

    def test_answer_api_failure_is_not_missing_evidence(self) -> None:
        client = FakeClient({})
        with patch.object(client.chat.completions, "create", side_effect=OpenAIError("test failure")):
            with self.assertRaises(ServiceUnavailableError):
                answer_from_knowledge(
                    client, self.db, self.employee, "年假制度是什么？",
                    FakeEmbedder(), FakeStore(self.db), FakeReranker(),
                )

    def test_number_not_supported_by_valid_quote_is_rejected(self) -> None:
        client = FakeClient({
            "answer": "员工每年有 30 天年假。",
            "evidence": [{"id": 1, "quote": "员工每年有 10 天年假"}],
            "needs_human": False,
        })
        answer = answer_from_knowledge(
            client, self.db, self.employee, "年假制度是什么？",
            FakeEmbedder(), FakeStore(self.db), FakeReranker(),
        )
        self.assertIsNone(answer)

    def test_context_budget_skips_oversized_chunk(self) -> None:
        def hit(text: str) -> SearchHit:
            return {
                "chunk_id": 1, "document_id": 1, "title": "测试文档",
                "text": text, "score": 0.8, "heading_path": (),
                "locations": [], "image_refs": (),
            }

        evidence, selected = _build_evidence([
            hit("长" * 4100), hit("员工请假制度说明"),
        ])
        self.assertEqual(len(selected), 1)
        self.assertIn("[1]", evidence)
        self.assertLessEqual(len(evidence), 4000)

    def test_named_product_filters_similar_documents_and_preserves_permissions(self) -> None:
        admin = {"username": "admin", "role": "admin"}
        ids = {}
        for number in (42, 44, 440):
            path = self.root / f"星河产品{number}操作手册.md"
            path.write_text(f"产品{number}用于管理项目交付。", encoding="utf-8")
            ids[number] = add_knowledge_file(self.db, admin, path, "finance", self.root)
        question = "能给我解释一下星河产品44吗？"
        self.assertEqual(mentioned_documents(self.db, question), (ids[44],))
        hits = search_knowledge(self.db, admin, question, FakeEmbedder(), FakeStore(self.db), limit=12)
        self.assertTrue(hits)
        self.assertEqual({h["document_id"] for h in hits}, {ids[44]})
        self.assertEqual(search_knowledge(self.db, self.employee, question, FakeEmbedder(), FakeStore(self.db)), [])
        self.assertEqual(mentioned_documents(self.db, "星河产品440是什么？"), (ids[440],))

    def test_invalid_citation_is_corrected_once_without_relaxing_validation(self) -> None:
        bad = FakeClient({"answer": "员工每年有 30 天年假。", "evidence": [{"id": 1, "quote": "员工每年有 10 天年假"}], "needs_human": False})
        good = FakeClient({"answer": "员工每年有 10 天年假。", "evidence": [{"id": 1, "quote": "员工每年有 10 天年假"}], "needs_human": False})
        client = FakeClient({})
        with patch.object(client.chat.completions, "create", side_effect=[bad.create(messages=[{}, {"content": ""}]), good.create(messages=[{}, {"content": ""}])]) as create:
            answer = answer_from_knowledge(client, self.db, self.employee, "年假制度是什么？", FakeEmbedder(), FakeStore(self.db))
        self.assertIn("10 天年假", answer)
        self.assertNotIn("30", answer)
        self.assertEqual(create.call_count, 2)
        self.assertIn("未通过引用校验", create.call_args.kwargs["messages"][-1]["content"])
        self.assertIn("缺少引文支持的数字是：30", create.call_args.kwargs["messages"][-1]["content"])

    def test_retry_identifies_unquoted_product_number(self) -> None:
        instruction = _citation_retry_instruction({
            "answer": "产品10汇总销售数据，每个工作日08:00刷新。",
            "evidence": [{"id": 1, "quote": "每个工作日08:00刷新"}],
        })
        self.assertIn("缺少引文支持的数字是：10。", instruction)
        self.assertNotIn("数字是：08", instruction)
        self.assertIn("未通过引用校验", _citation_retry_instruction({"answer": "错误", "evidence": [None]}))

    def test_missing_fact_is_not_retried_and_invalid_answers_remain_rejected(self) -> None:
        for payload, expected_calls in [
            ({"needs_human": True}, 1),
            ({"needs_human": False, "answer": "每年30天", "evidence": [{"id": 1, "quote": "员工每年有 10 天年假"}]}, 2),
        ]:
            client = FakeClient(payload)
            answer = answer_from_knowledge(client, self.db, self.employee, "年假制度是什么？", FakeEmbedder(), FakeStore(self.db))
            self.assertIsNone(answer)
            self.assertEqual(len(client.prompts), expected_calls)

    def test_feedback_is_saved_and_admin_only(self) -> None:
        feedback_id = record_feedback(
            self.db, self.employee, "年假怎么申请？", "请提交申请。", "无用"
        )
        self.assertEqual(feedback_id, 1)
        items = list_recent_feedback(
            self.db, {"username": "admin", "role": "admin"}
        )
        self.assertEqual(items[0]["rating"], "无用")
        with self.assertRaises(PermissionError):
            list_recent_feedback(self.db, self.employee)
        with self.assertRaises(ValueError):
            record_feedback(
                self.db, self.employee, "问题", "回答", "不确定"
            )

    def test_cli_feedback_does_not_enter_agent_graph(self) -> None:
        class Graph:
            def __init__(self, answer_fn: object) -> None:
                self.answer_fn = answer_fn
                self.calls = 0

            def invoke(self, state: dict[str, object], config: object) -> dict[str, str]:
                self.calls += 1
                return {
                    "domain": "knowledge",
                    "answer": self.answer_fn(state["user"], state["question"]),
                }

        class Client:
            def __enter__(self) -> "Client":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        graphs: list[Graph] = []

        def build_graph(*args: object) -> Graph:
            graph = Graph(args[7])
            graphs.append(graph)
            return graph

        with (
            patch("main.prompt_login", return_value=self.employee),
            patch("main.read_input", side_effect=[
                "年假怎么申请？", "反馈：有用", "exit",
            ]),
            patch("main.OpenAI", return_value=Client()),
            patch("main.LocalEmbedder", return_value=object()),
            patch("main.MilvusVectorStore") as store,
            patch("main.answer_from_knowledge", return_value="请提交申请。\n来源：年假"),
            patch("main.build_graph", side_effect=build_graph),
            patch.dict("main.os.environ", {"DEEPSEEK_API_KEY": "test-key"}),
            redirect_stdout(io.StringIO()),
        ):
            run_cli(self.db)
        self.assertEqual(graphs[0].calls, 1)
        self.assertEqual(store.return_value.close.call_count, 1)
        items = list_recent_feedback(
            self.db, {"username": "admin", "role": "admin"}
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["rating"], "有用")
        self.assertEqual(items[0]["answer"], "正文已隐藏")  # mock 回答没有提供可验证来源。
        self.assertEqual(self.db.execute("SELECT question FROM knowledge_feedback").fetchone()[0], "年假怎么申请？")
        summary = list_usage_summary(
            self.db, {"username": "admin", "role": "admin"}
        )
        self.assertEqual(summary[0]["outcome"], "answered")
        self.assertEqual(summary[0]["count"], 1)
        with self.assertRaises(PermissionError):
            list_usage_summary(self.db, self.employee)

    def test_short_followup_uses_previous_knowledge_question(self) -> None:
        class Graph:
            def __init__(self, route_fn: object, answer_fn: object) -> None:
                self.route_fn = route_fn
                self.answer_fn = answer_fn

            def invoke(self, state: dict[str, object], config: object) -> dict[str, str]:
                domain = self.route_fn(state["question"])
                return {
                    "domain": domain,
                    "answer": self.answer_fn(state["user"], state["question"]),
                }

        class Client:
            def __enter__(self) -> "Client":
                return self

            def __exit__(self, *_args: object) -> None:
                return None

        with (
            patch("main.prompt_login", return_value=self.employee),
            patch("main.read_input", side_effect=[
                "公司的年假制度是什么？", "那怎么申请？", "exit",
            ]),
            patch("main.OpenAI", return_value=Client()),
            patch("main.LocalEmbedder", return_value=object()),
            patch("main.MilvusVectorStore"),
            patch("main.LocalReranker", return_value=object()),
            patch("main.route_question", return_value="knowledge") as route,
            patch("main.answer_from_knowledge", side_effect=[
                "年假制度说明。\n来源：年假",
                "请提交申请。\n来源：年假",
            ]) as answer,
            patch("main.build_graph", side_effect=lambda *args: Graph(
                args[0], args[7]
            )),
            patch.dict("main.os.environ", {"DEEPSEEK_API_KEY": "test-key"}),
            redirect_stdout(io.StringIO()),
        ):
            run_cli(self.db)
        self.assertEqual(route.call_count, 1)
        self.assertEqual(answer.call_count, 2)
        expanded = answer.call_args_list[1].args[3]
        self.assertIn("公司的年假制度是什么？", expanded)
        self.assertIn("那怎么申请？", expanded)


if __name__ == "__main__":
    unittest.main()
