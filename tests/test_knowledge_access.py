import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import Mock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.agent.service import TurnResult
from app.api.app import create_app
from app.auth import connect, insert_user
from app.knowledge.access import AccessPolicy, visible_document_ids, write_policy
from app.knowledge.answer import answer_from_knowledge
from app.knowledge.documents import add_knowledge_file, ensure_knowledge_schema, get_visible_asset_path, list_visible_assets, list_visible_chunks, list_visible_documents
from app.knowledge.keyword import search_keywords
from app.knowledge.lifecycle import replace_knowledge_file
from app.knowledge.retrieval import search_knowledge
from app.knowledge.vector_store import MilvusVectorStore
from test_rag_flow import FakeClient, FakeEmbedder, FakeStore


class KnowledgeAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "company.db"
        self.db = connect(self.path)
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.db.close)
        people = [("owner", "admin", 1, "employee"), ("other_admin", "admin", 2, "employee"),
                  ("staff", "employee", 1, "employee"), ("outsider", "employee", 2, "employee"),
                  ("manager", "employee", 1, "department_manager"), ("gm", "employee", 8, "general_manager"),
                  ("chair", "employee", 8, "chairman")]
        for name, role, dep, level in people:
            insert_user(self.db, name, "test-password", role, {"display_name": name, "department_id": dep, "job_level": level, "manager_username": None})
        self.owner = {"username": "owner", "role": "admin"}
        source = self.root / "restricted.md"
        source.write_text("# 限定资料\n机密专用码 SECRET-742 只用于内部审批。", encoding="utf-8")
        self.doc = add_knowledge_file(self.db, self.owner, source, "finance", self.root)
        self.app = create_app(self.path)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.addCleanup(lambda: self.client.__exit__(None, None, None))
        self.client.headers["X-Requested-With"] = "employee-assistant"
        self.login("owner")
        self.environment = patch.dict("os.environ", {"RAG_QUERY_REWRITE_ENABLED": "false"})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def login(self, username: str) -> None:
        response = self.client.post("/api/auth/login", json={"username": username, "password": "test-password"})
        self.assertEqual(response.status_code, 200, response.text)

    def policy(self, groups: list[dict[str, object]], mode: str = "restricted") -> None:
        with self.db:
            write_policy(self.db, self.owner, self.doc, AccessPolicy.model_validate({"mode": mode, "groups": groups}))

    def readers(self) -> set[str]:
        return {row[0] for row in self.db.execute("SELECT username FROM users")
                if self.doc in visible_document_ids(self.db, {"username": row[0], "role": "admin"})}

    def test_level_department_person_intersection_union_and_deny_default(self) -> None:
        self.assertEqual(self.readers(), {"owner", "other_admin"})  # 旧 scope 保持原角色权限。
        self.policy([{"min_job_level": "general_manager"}])
        self.assertEqual(self.readers(), {"gm", "chair"})
        self.policy([{"department_ids": [1]}])
        self.assertEqual(self.readers(), {"owner", "staff", "manager"})
        self.policy([{"usernames": ["staff"]}])
        self.assertEqual(self.readers(), {"staff"})
        self.policy([{"department_ids": [1], "min_job_level": "department_manager"}, {"usernames": ["outsider"]}])
        self.assertEqual(self.readers(), {"manager", "outsider"})
        self.policy([])
        self.assertEqual(self.readers(), set())
        self.policy([], "all")
        self.assertEqual(len(self.readers()), 7)

    def test_revocation_department_change_disabled_account_and_forged_attributes(self) -> None:
        self.policy([{"department_ids": [1], "min_job_level": "department_manager"}])
        forged = {"username": "staff", "role": "admin", "job_level": "chairman", "department_id": 1}
        self.assertEqual(visible_document_ids(self.db, forged), ())
        with self.db:
            self.db.execute("UPDATE users SET department_id=2 WHERE username='manager'")
        self.assertEqual(self.readers(), set())
        self.policy([{"usernames": ["staff"]}])
        with self.db:
            self.db.execute("UPDATE users SET enabled=0 WHERE username='staff'")
        self.assertEqual(self.readers(), set())

    def test_owner_management_does_not_grant_read_or_allow_other_admin_to_self_grant(self) -> None:
        self.policy([{"usernames": ["staff"]}])
        self.assertEqual(self.client.get("/api/knowledge").json(), [])
        managed = self.client.get("/api/knowledge?managed=true").json()
        self.assertEqual(len(managed), 1)
        self.assertFalse(managed[0]["can_read"])
        self.assertTrue(managed[0]["can_manage"])
        self.login("other_admin")
        self.assertEqual(self.client.post(f"/api/knowledge/{self.doc}/access", json={"mode": "all"}).status_code, 403)
        self.assertEqual(self.client.get(f"/api/knowledge/{self.doc}/access").status_code, 403)
        self.assertEqual(self.client.get("/api/knowledge?managed=true").json(), [])
        for suffix in ("reindex",):
            self.assertEqual(self.client.post(f"/api/knowledge/{self.doc}/{suffix}").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/knowledge/{self.doc}").status_code, 403)

    def test_preview_download_images_and_list_enforce_same_policy(self) -> None:
        image_dir = self.root / "knowledge" / "images" / str(self.doc)
        image_dir.mkdir(parents=True)
        (image_dir / "test.png").write_bytes(b"test-image")
        with self.db:
            asset = self.db.execute("INSERT INTO knowledge_assets(document_id,block_order,image_ref,location_json) VALUES (?,0,?,'{}')",
                                    (self.doc, (image_dir / "test.png").relative_to(self.root).as_posix())).lastrowid
        self.policy([{"usernames": ["staff"]}])
        for username, status in (("staff", 200), ("outsider", 404), ("other_admin", 404)):
            self.login(username)
            for url in (f"/api/knowledge/{self.doc}/preview", f"/api/knowledge/{self.doc}/download", f"/api/knowledge/assets/{asset}"):
                self.assertEqual(self.client.get(url).status_code, status, url)
            actor = {"username": username, "role": "admin"}
            self.assertEqual(bool(list_visible_chunks(self.db, actor)), status == 200)
            self.assertEqual(bool(list_visible_documents(self.db, actor)), status == 200)
            self.assertEqual(bool(list_visible_assets(self.db, actor)), status == 200)
            self.assertEqual(get_visible_asset_path(self.db, actor, asset, self.root) is not None, status == 200)

    def test_authorized_ids_sent_to_both_searches_and_sql_rechecks_malicious_store(self) -> None:
        self.policy([{"usernames": ["staff"]}])
        spy = Mock(wraps=FakeStore(self.db))
        with patch("app.knowledge.retrieval.search_keywords", wraps=search_keywords) as keyword:
            hits = search_knowledge(self.db, {"username": "staff", "role": "employee"}, "SECRET-742", FakeEmbedder(), spy)
            self.assertEqual({hit["document_id"] for hit in hits}, {self.doc})
            self.assertEqual(spy.search.call_args.kwargs["document_ids"], (self.doc,))
            self.assertEqual(keyword.call_args.kwargs["document_ids"], (self.doc,))
        spy.reset_mock()
        hits = search_knowledge(self.db, {"username": "outsider", "role": "admin"}, "SECRET-742", FakeEmbedder(), spy)
        self.assertEqual(hits, [])
        spy.search.assert_not_called()
        # 搜索过程中被撤权，候选返回后应被 SQLite 拦截。
        def revoke(*args: object, **kwargs: object) -> list[dict[str, object]]:
            self.policy([])
            return FakeStore(self.db).search([], (), 10)
        spy.search.side_effect = revoke
        self.assertEqual(search_knowledge(self.db, {"username": "staff", "role": "employee"}, "SECRET-742", FakeEmbedder(), spy), [])

    def test_empty_id_filter_never_means_all_documents(self) -> None:
        store = MilvusVectorStore.__new__(MilvusVectorStore)
        store.client = Mock()
        self.assertEqual(store.search([0.0] * 512, ("general",), 5, document_ids=()), [])
        store.client.search.assert_not_called()
        self.assertEqual(search_keywords(self.db, "SECRET-742", ("finance",), 5, document_ids=()), [])

    def test_model_generation_checks_revocation_and_tracks_all_context_sources(self) -> None:
        self.policy([{"usernames": ["staff"]}])
        actor = {"username": "staff", "role": "employee"}
        client = FakeClient({"answer": "SECRET-742 只用于内部审批。", "evidence": [{"id": 1, "quote": "SECRET-742 只用于内部审批"}], "needs_human": False})
        sources: list[int] = []
        self.assertIsNotNone(answer_from_knowledge(client, self.db, actor, "专用码是什么", FakeEmbedder(), FakeStore(self.db), source_ids=sources))
        self.assertEqual(sources, [self.doc])
        original = client.chat.completions.create
        def revoke(**kwargs: object) -> object:
            response = original(**kwargs)
            self.policy([])
            return response
        client.chat.completions.create = revoke
        self.assertIsNone(answer_from_knowledge(client, self.db, actor, "专用码是什么", FakeEmbedder(), FakeStore(self.db), source_ids=sources))
        self.assertEqual(sources, [])

    def test_history_retry_feedback_and_followup_after_revocation(self) -> None:
        self.policy([{"usernames": ["staff"]}])
        self.login("staff")
        cid = self.client.post("/api/conversations").json()["id"]
        url = f"/api/conversations/{cid}/messages"
        body = {"question": "专用码是什么", "request_id": str(uuid4())}
        response = TurnResult("knowledge", "SECRET-742", knowledge_question=body["question"], feedback_allowed=True, source_document_ids=(self.doc,))
        with patch.object(self.app.state.assistant, "respond", return_value=response):
            answer = self.client.post(url, json=body).json()
        self.assertEqual(answer["answer"], "SECRET-742")
        feedback_url = f"/api/messages/{answer['id']}/feedback"
        self.assertEqual(self.client.post(feedback_url, json={"rating": "有用"}).status_code, 200)
        self.login("owner")
        self.assertNotIn("SECRET-742", self.client.get("/api/knowledge-feedback").text)
        self.login("staff")
        self.policy([])
        self.assertNotIn("SECRET-742", self.client.get(url).text)
        self.assertNotIn("SECRET-742", self.client.post(url, json=body).text)
        self.assertEqual(self.client.post(feedback_url, json={"rating": "有用"}).status_code, 403)
        with patch.object(self.app.state.assistant, "respond", return_value=TurnResult("identity", "请重新提问")) as respond:
            self.client.post(url, json={"question": "具体呢", "request_id": str(uuid4())})
            self.assertEqual(respond.call_args.args[4], "")

    def test_policy_validation_audit_and_atomic_upload(self) -> None:
        url = f"/api/knowledge/{self.doc}/access"
        for invalid in ({"mode": "restricted", "groups": [{}]}, {"groups": [{"department_ids": [999]}]},
                        {"groups": [{"usernames": ["missing"]}]}, {"mode": "all", "groups": [{"usernames": ["staff"]}]}):
            self.assertIn(self.client.post(url, json=invalid).status_code, {400, 422})
        changed = self.client.post(url, json={"mode": "restricted", "groups": [{"usernames": ["staff"]}]})
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(self.client.get(f"/api/knowledge/{self.doc}/access-audit").json()[0]["actor"], "owner")
        def verify_before_index(db: sqlite3.Connection, document_id: int, *args: object) -> int:
            self.assertNotIn(document_id, visible_document_ids(db, {"username": "owner", "role": "admin"}))
            self.assertIn(document_id, visible_document_ids(db, {"username": "staff", "role": "employee"}))
            raise RuntimeError("模拟索引失败")
        with patch.object(self.app.state.assistant, "get_store"), patch.object(self.app.state.assistant, "get_embedder"), patch("app.api.knowledge_routes.index_document", side_effect=verify_before_index):
            result = self.client.post("/api/knowledge", data={"scope": "general", "access_policy": changed.text}, files={"file": ("private.md", b"private details", "text/markdown")})
        self.assertEqual(result.status_code, 503)
        self.assertEqual(visible_document_ids(self.db, self.owner), ())

    def test_replacement_preserves_policy(self) -> None:
        self.policy([{"usernames": ["staff"]}])
        source = self.root / "new.md"
        source.write_text("新版本的私密内容", encoding="utf-8")
        with patch("app.knowledge.lifecycle.index_document", return_value=1):
            new_id, _ = replace_knowledge_file(self.db, self.owner, self.doc, source, self.root, Mock(), Mock())
        self.assertNotIn(new_id, visible_document_ids(self.db, self.owner))
        self.assertIn(new_id, visible_document_ids(self.db, {"username": "staff", "role": "employee"}))


if __name__ == "__main__":
    unittest.main()
