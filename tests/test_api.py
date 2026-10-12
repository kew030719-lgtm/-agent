import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.agent.service import TurnResult
from app.agent.meeting_dialogue import MeetingRequest
from app.tools.meeting import add_meeting_room
from app.api.app import create_app
from app.auth import connect, insert_user
from app.errors import ServiceUnavailableError
from app.knowledge.documents import add_knowledge_document


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "test.db"
        with closing(connect(self.database)) as db:
            for name, role in (("employee", "employee"), ("another", "employee"), ("admin", "admin")):
                insert_user(db, name, "test-password", role)
        self.app = create_app(self.database)
        self.client = TestClient(self.app)
        self.client.__enter__()
        self.client.headers["X-Requested-With"] = "employee-assistant"

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temporary.cleanup()

    def login(self, username: str = "employee") -> None:
        response = self.client.post("/api/auth/login", json={"username": username, "password": "test-password"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("HttpOnly", response.headers["set-cookie"])

    def conversation(self) -> str:
        response = self.client.post("/api/conversations")
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def send(self, conversation_id: str, question: str, request_id: str | None = None) -> dict[str, object]:
        response = self.client.post(f"/api/conversations/{conversation_id}/messages", json={
            "question": question, "request_id": request_id or str(uuid4()),
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_authentication_csrf_and_logout(self) -> None:
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        bad = self.client.post("/api/auth/login", json={"username": "employee", "password": "wrong"})
        self.assertEqual(bad.status_code, 401)
        self.login()
        profile = self.client.get("/api/auth/me").json()
        self.assertIn("kb:general", profile["permissions"])
        self.assertNotIn("kb:finance", profile["permissions"])
        blocked = self.client.post("/api/conversations", headers={"Origin": "https://untrusted.example"})
        self.assertEqual(blocked.status_code, 403)
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 204)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_current_account_cannot_be_impersonated_and_disabled_session_is_revoked(self) -> None:
        self.login()
        conversation = self.conversation()
        response = self.client.post(f"/api/conversations/{conversation}/messages", json={
            "question": "我的权限", "request_id": str(uuid4()), "role": "admin",
        })
        self.assertEqual(response.status_code, 422)
        with sqlite3.connect(self.database) as db:
            db.execute("UPDATE users SET enabled=0 WHERE username='employee'")
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_conversation_ownership(self) -> None:
        self.login()
        conversation = self.conversation()
        self.send(conversation, "你是谁")
        self.login("another")
        self.assertEqual(self.client.get(f"/api/conversations/{conversation}/messages").status_code, 404)
        self.assertEqual(self.client.get("/api/conversations").json(), [])
        response = self.client.post(f"/api/conversations/{conversation}/messages", json={"question": "我的权限", "request_id": str(uuid4())})
        self.assertEqual(response.status_code, 404)

    def test_meeting_conditions_persist_only_in_their_conversation(self) -> None:
        self.login()
        cid = self.conversation()
        other = self.conversation()
        with patch("app.agent.service.route_question", return_value="meeting"), patch("app.agent.service.interpret_meeting", side_effect=[
            MeetingRequest(mode="period", capacity=8, start_at="2026-10-13 15:00"),
            MeetingRequest(mode="period", capacity=8, start_at="2026-10-13 15:00", end_at="2026-10-13 16:00"),
            MeetingRequest(mode="list", capacity=8),
        ]) as parse:
            self.assertIn("准备开多久", self.send(cid, "明天下午三点，八个人开会")["answer"])
            with sqlite3.connect(self.database) as db:
                context = db.execute("SELECT meeting_context FROM conversations WHERE id=?", (cid,)).fetchone()[0]
            self.send(cid, "一个小时")
            self.assertEqual(parse.call_args.args[2], context)
            self.send(other, "八个人的会议室")
            self.assertEqual(parse.call_args.args[2], "")

    def test_employee_booking_preview_confirmation_and_retry(self) -> None:
        self.login()
        cid = self.conversation()
        with sqlite3.connect(self.database) as db:
            room = add_meeting_room(db, {"username": "admin", "role": "admin"}, "竹影", "1楼", 10)
        with patch("app.agent.service.route_question", return_value="meeting"), patch("app.agent.service.interpret_meeting", return_value=MeetingRequest(
            mode="book", room_id=room, capacity=8, start_at="2099-10-13 15:00", end_at="2099-10-13 16:00",
        )):
            self.assertIn("请确认预订", self.send(cid, "订竹影，明天下午三点到四点")["answer"])
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM meeting_bookings").fetchone()[0], 0)
        request_id = str(uuid4())
        self.assertIn("已为 employee 预订", self.send(cid, "确认预订", request_id)["answer"])
        self.send(cid, "确认预订", request_id)
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT created_by,COUNT(*) FROM meeting_bookings").fetchone(), ("employee", 1))

    def test_history_pagination_keeps_older_conversations_accessible(self) -> None:
        self.login()
        with sqlite3.connect(self.database) as db:
            for index in range(125):
                db.execute("INSERT INTO conversations(id,username,role,title) VALUES (?,?,?,?)",
                           (str(uuid4()), "employee", "employee", f"历史{index}"))
        first = self.client.get("/api/conversations?limit=100&offset=0").json()
        second = self.client.get("/api/conversations?limit=100&offset=100").json()
        self.assertEqual((len(first), len(second)), (100, 25))
        self.assertFalse({r["id"] for r in first} & {r["id"] for r in second})
        self.assertEqual(self.client.get("/api/conversations?offset=-1").status_code, 422)
        self.assertEqual(self.client.get("/api/conversations?offset=" + "9" * 80).status_code, 422)
        self.assertEqual(self.client.get("/api/conversations?limit=101").status_code, 422)
        self.login("another")
        self.assertEqual(self.client.get("/api/conversations?offset=100").json(), [])

    def test_large_meeting_capacity_is_a_validation_message(self) -> None:
        self.login()
        conversation = self.conversation()
        huge = "9" * 80
        for question in (f"会议室：{huge}人", f"空闲会议室：{huge}人；开始：2026-11-01 09:00；结束：2026-11-01 10:00"):
            response = self.send(conversation, question)
            self.assertEqual(response["status"], "completed")
            self.assertIn("支持范围", response["answer"])

    def test_ticket_confirmation_persists_and_request_retry_does_not_duplicate(self) -> None:
        self.login()
        conversation = self.conversation()
        with patch("app.agent.service.route_question", return_value="it"):
            first = self.send(conversation, "电脑无法联网")
        self.assertIn("确认报修", first["answer"])
        self.login()  # 新登录仍可继续自己原来的会话。
        request_id = str(uuid4())
        second = self.send(conversation, "确认报修", request_id)
        repeated = self.send(conversation, "确认报修", request_id)
        self.assertEqual(second["id"], repeated["id"])
        self.assertIn("已创建", second["answer"])
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM it_tickets").fetchone()[0], 1)

    def test_service_error_is_saved_without_handoff_and_conversation_recovers(self) -> None:
        self.login()
        conversation = self.conversation()
        with patch.object(self.app.state.assistant, "respond", side_effect=ServiceUnavailableError("模拟服务失败")):
            response = self.client.post(f"/api/conversations/{conversation}/messages", json={"question": "知识问题", "request_id": str(uuid4())})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.client.get(f"/api/conversations/{conversation}/messages").json()[0]["status"], "failed")
        self.assertEqual(self.send(conversation, "你是谁")["domain"], "identity")
        with sqlite3.connect(self.database) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM handoffs").fetchone()[0], 0)

    def test_feedback_ownership_and_duplicate_guard(self) -> None:
        self.login()
        conversation = self.conversation()
        with patch.object(self.app.state.assistant, "respond", return_value=TurnResult("knowledge", "原文回答\n来源：制度", feedback_allowed=True)):
            turn = self.send(conversation, "制度问题")
        url = f"/api/messages/{turn['id']}/feedback"
        self.login("another")
        self.assertEqual(self.client.post(url, json={"rating": "有用"}).status_code, 404)
        self.login()
        self.assertEqual(self.client.post(url, json={"rating": "有用"}).status_code, 200)
        self.assertEqual(self.client.post(url, json={"rating": "有用"}).status_code, 409)

    def test_admin_permissions_and_visible_knowledge(self) -> None:
        with closing(connect(self.database)) as db:
            admin = {"username": "admin", "role": "admin"}
            add_knowledge_document(db, admin, "普通制度", "公开内容", "general")
            add_knowledge_document(db, admin, "财务资料", "财务内容", "finance")
        self.login()
        self.assertEqual(len(self.client.get("/api/knowledge").json()), 1)
        for url in ("/api/handoffs", "/api/knowledge-feedback", "/api/knowledge-stats"):
            self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.delete("/api/knowledge/1").status_code, 403)
        upload = self.client.post("/api/knowledge", data={"scope": "general"}, files={"file": ("test.md", b"test", "text/markdown")})
        self.assertEqual(upload.status_code, 403)
        self.login("admin")
        self.assertEqual(len(self.client.get("/api/knowledge").json()), 2)
        self.assertEqual(self.client.get("/api/handoffs").status_code, 200)

    def test_upload_indexes_document_and_delete_removes_metadata(self) -> None:
        self.login("admin")
        with (
            patch.object(self.app.state.assistant, "get_store"),
            patch.object(self.app.state.assistant, "get_embedder"),
            patch("app.api.knowledge_routes.index_document", return_value=1),
        ):
            response = self.client.post("/api/knowledge", data={"scope": "general"}, files={"file": ("rules.md", "# 公司制度\n年假需主管审批。".encode(), "text/markdown")})
            self.assertEqual(response.status_code, 201, response.text)
            document_id = response.json()["id"]
            self.assertEqual(self.client.delete(f"/api/knowledge/{document_id}").status_code, 204)
        self.assertEqual(self.client.get("/api/knowledge").json(), [])

    def test_only_admin_can_manage_users(self) -> None:
        self.assertEqual(self.client.get("/api/users").status_code, 401)
        self.login()
        for path in ("/api/users", "/api/users/roles"):
            self.assertEqual(self.client.get(path).status_code, 403)
        response = self.client.post("/api/users", json={
            "username": "unauthorized", "password": "test-password", "role": "admin",
        })
        self.assertEqual(response.status_code, 403)
        with sqlite3.connect(self.database) as db:
            self.assertIsNone(db.execute("SELECT username FROM users WHERE username='unauthorized'").fetchone())

    def test_admin_creates_user_who_can_login(self) -> None:
        self.login("admin")
        response = self.client.post("/api/users", json={
            "username": " new-hr ", "password": " initial-password ", "role": "hr",
        })
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json(), {"username": "new-hr", "role": "hr", "enabled": True})
        listed = self.client.get("/api/users").json()
        self.assertTrue(any(item["username"] == "new-hr" for item in listed))
        self.assertTrue(all(set(item) == {"username", "role", "enabled"} for item in listed))
        duplicate = self.client.post("/api/users", json={
            "username": "new-hr", "password": "different-password", "role": "admin",
        })
        self.assertEqual(duplicate.status_code, 409)
        login = self.client.post("/api/auth/login", json={"username": "new-hr", "password": " initial-password "})
        self.assertEqual(login.status_code, 200)
        self.assertIn("kb:hr", login.json()["permissions"])
        self.assertNotIn("user:create", login.json()["permissions"])

    def test_user_creation_validation_and_role_options(self) -> None:
        self.login("admin")
        self.assertEqual({item["role"] for item in self.client.get("/api/users/roles").json()},
                         {"employee", "hr", "it", "finance", "admin"})
        for change in ({"username": "   "}, {"password": "short"}, {"role": "root"}):
            body = {"username": "invalid-user", "password": "private-test-password", "role": "employee"} | change
            response = self.client.post("/api/users", json=body)
            self.assertEqual(response.status_code, 422)
            self.assertNotIn(body["password"], response.text)


if __name__ == "__main__":
    unittest.main()
