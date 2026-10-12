import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from openai import OpenAIError

from app.knowledge.answer import answer_from_knowledge
from app.knowledge.documents import add_knowledge_file
from app.knowledge.keyword import _query_terms
from app.knowledge.query import rewrite_queries
from app.knowledge.retrieval import reciprocal_rank_fusion, search_knowledge, with_neighbor_context
from test_rag_flow import FakeClient, FakeEmbedder, FakeStore


class RewriteTests(unittest.TestCase):
    def client(self, query: str) -> Mock:
        client = Mock()
        client.with_options.return_value = client
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=query))])
        return client

    def test_original_and_rewrite_are_both_kept(self) -> None:
        question = "能给我解释一下星河产品44吗？"
        rewritten = "星河产品44的用途和操作流程是什么？"
        with patch.dict("os.environ", {"RAG_QUERY_REWRITE_ENABLED": "true"}):
            self.assertEqual(rewrite_queries(self.client(json.dumps({"queries": [rewritten]})), question), [question, rewritten])

    def test_rewrite_cannot_change_identifiers_dates_or_negation(self) -> None:
        cases = [
            ("产品44怎么用", "产品42怎么用"),
            ("2026-11-01之后不能报销吗", "2026-11-02之后不能报销吗"),
            ("哪些费用不能报销", "哪些费用可以报销"),
            ("年假怎么申请", "年假提前3天怎么申请"),
            ("V44.16是什么", "V44.1是什么"),
        ]
        with patch.dict("os.environ", {"RAG_QUERY_REWRITE_ENABLED": "true"}):
            for question, rewritten in cases:
                with self.subTest(question=question):
                    self.assertEqual(rewrite_queries(self.client(json.dumps({"queries": [rewritten]})), question), [question])

    def test_failure_or_invalid_format_falls_back_to_original(self) -> None:
        with patch.dict("os.environ", {"RAG_QUERY_REWRITE_ENABLED": "true"}):
            for payload in ("invalid", "null", '{"queries":[1]}', '{"queries":[]}'):
                self.assertEqual(rewrite_queries(self.client(payload), "问题原文"), ["问题原文"])
            client = self.client("{}")
            client.chat.completions.create.side_effect = OpenAIError("test failure")
            self.assertEqual(rewrite_queries(client, "问题原文"), ["问题原文"])
            self.assertEqual(client.chat.completions.create.call_count, 1)


class RecallTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("CREATE TABLE users(username TEXT PRIMARY KEY)")
        self.db.execute("INSERT INTO users VALUES ('admin')")
        self.admin = {"username": "admin", "role": "admin"}
        environment = patch.dict("os.environ", {"RAG_QUERY_REWRITE_ENABLED": "false", "RAG_NEIGHBOR_CONTEXT_ENABLED": "true"})
        environment.start()
        self.addCleanup(environment.stop)

    def add_document(self, title: str, text: str, scope: str = "general") -> int:
        path = self.root / f"{title}.md"
        path.write_text(text, encoding="utf-8")
        return add_knowledge_file(self.db, self.admin, path, scope, self.root)

    def test_rrf_does_not_double_count_duplicates_within_one_list(self) -> None:
        scores = reciprocal_rank_fusion([[1, 1, 2], [2, 3]])
        self.assertGreater(scores[2], scores[1])
        self.assertAlmostEqual(scores[1], 1 / 61)

    def test_keyword_only_evidence_reaches_generation_despite_low_vector_score(self) -> None:
        self.add_document("操作规则", "审批专用码 AX-742 对应纸质发票审核。")
        store = Mock()
        store.search.return_value = []
        client = FakeClient({"answer": "AX-742 对应纸质发票审核。", "evidence": [{"id": 1, "quote": "AX-742 对应纸质发票审核"}], "needs_human": False})
        answer = answer_from_knowledge(client, self.db, self.admin, "AX-742是什么", FakeEmbedder(), store)
        self.assertIn("纸质发票", answer)
        self.assertEqual(len(client.prompts), 1)
        self.assertIn("v44.16", _query_terms("V44.16的交付记录"))

    def test_rewrite_does_not_replace_named_document_constraint(self) -> None:
        doc = self.add_document("星河产品44操作手册", "产品44用于项目交付。", "finance")
        self.add_document("星河产品42操作手册", "产品42用于项目交付。")
        store = FakeStore(self.db)  # 故意忽略向量权限；SQLite检查仍应拦住。
        hits = search_knowledge(self.db, self.admin, "星河产品44怎么用", FakeEmbedder(), store, queries=["星河产品42怎么用"])
        self.assertEqual({hit["document_id"] for hit in hits}, {doc})
        employee = {"username": "employee", "role": "employee"}
        self.assertEqual(search_knowledge(self.db, employee, "星河产品44怎么用", FakeEmbedder(), store), [])

    def test_neighbor_context_stays_in_section_and_permission(self) -> None:
        doc = self.add_document("办理流程", "# 报销\n所需材料如下：")
        first = self.db.execute("SELECT id FROM knowledge_chunks WHERE document_id=?", (doc,)).fetchone()[0]
        self.db.execute("INSERT INTO knowledge_chunks(document_id,chunk_order,text,source_format,kind,heading_path_json,locations_json,image_refs_json) SELECT document_id,chunk_order+1,?,source_format,kind,heading_path_json,locations_json,image_refs_json FROM knowledge_chunks WHERE id=?", ("发票与支付凭证。", first))
        neighbor = self.db.execute("SELECT max(id) FROM knowledge_chunks").fetchone()[0]
        hits = search_knowledge(self.db, self.admin, "所需材料", FakeEmbedder(), FakeStore(self.db), limit=4)
        primary = [hit for hit in hits if hit["chunk_id"] == first]
        expanded = with_neighbor_context(self.db, self.admin, primary)
        self.assertEqual([hit["chunk_id"] for hit in expanded], [first, neighbor])
        self.db.execute("UPDATE knowledge_chunks SET heading_path_json='[\"其他章节\"]' WHERE id=?", (neighbor,))
        self.assertEqual(with_neighbor_context(self.db, self.admin, primary), primary)
        self.db.execute("UPDATE knowledge_documents SET scope='finance' WHERE id=?", (doc,))
        self.assertEqual(with_neighbor_context(self.db, {"username": "employee", "role": "employee"}, primary), primary)


if __name__ == "__main__":
    unittest.main()
