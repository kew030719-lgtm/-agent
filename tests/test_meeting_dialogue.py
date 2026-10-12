import json
import sqlite3
import unittest
from datetime import datetime
from functools import partial
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

from openai import OpenAIError

from app.agent.graph import build_graph
from app.agent.meeting_dialogue import MeetingRequest, MeetingUpdate, answer_meeting, interpret_meeting, merge_conditions
from app.api.database import initialize
from app.errors import ServiceUnavailableError
from app.tools.meeting import add_meeting_booking, add_meeting_room, list_available_meeting_rooms, list_meeting_rooms


class MeetingDialogueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user = {"username": "employee", "role": "employee"}

    def test_now_excludes_current_booking_but_includes_ended_booking(self) -> None:
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE users(username TEXT PRIMARY KEY)")
            db.execute("INSERT INTO users VALUES ('admin')")
            initialize(db)
            admin = {"username": "admin", "role": "admin"}
            busy = add_meeting_room(db, admin, "忙碌会议室", "1楼", 10)
            ended = add_meeting_room(db, admin, "可用会议室", "2楼", 10)
            add_meeting_booking(db, admin, busy, "2026-10-12 09:00", "2026-10-12 11:00")
            add_meeting_booking(db, admin, ended, "2026-10-12 09:00", "2026-10-12 10:00")
            with patch("app.agent.meeting_dialogue.local_now", return_value=datetime(2026, 10, 12, 10, 0, 30, tzinfo=ZoneInfo("Asia/Shanghai"))):
                reply = answer_meeting(MeetingRequest(mode="now", capacity=8), self.user, partial(list_meeting_rooms, db), partial(list_available_meeting_rooms, db))
            self.assertIn("可用会议室", reply["answer"])
            self.assertNotIn("忙碌会议室", reply["answer"])
            self.assertIn("2026-10-12 10:00", reply["answer"])

    def test_incomplete_period_asks_for_duration_without_assuming_one_hour(self) -> None:
        available = Mock()
        reply = answer_meeting(MeetingRequest(mode="period", capacity=8, start_at="2026-10-13 15:00"), self.user, Mock(), available)
        self.assertIn("准备开多久", reply["answer"])
        self.assertIsNone(MeetingRequest.model_validate_json(reply["meeting_context"]).end_at)
        available.assert_not_called()

    def test_delta_merge_keeps_current_query_then_resolves_duration_and_date_change(self) -> None:
        now = datetime(2026, 10, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        capacity = merge_conditions(MeetingUpdate(followup=True, capacity=8), MeetingRequest(mode="now"), now)
        self.assertEqual((capacity.mode, capacity.capacity), ("now", 8))
        duration = merge_conditions(MeetingUpdate(followup=True, duration_minutes=60), capacity, now)
        self.assertEqual((duration.start_at, duration.end_at), ("2026-10-12 10:00", "2026-10-12 11:00"))
        shifted = merge_conditions(MeetingUpdate(followup=True, mode="period", start_at="2026-10-13 15:00"), duration, now)
        self.assertEqual((shifted.capacity, shifted.end_at), (8, "2026-10-13 16:00"))
        fresh = merge_conditions(MeetingUpdate(mode="period", start_at="2026-10-14 15:00"), shifted, now)
        self.assertIsNone(fresh.end_at)
        self.assertIsNone(fresh.capacity)

    def test_clarification_null_is_valid_and_explicit_date_overrides_now(self) -> None:
        now = datetime(2026, 10, 12, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        update = MeetingUpdate.model_validate_json('{"mode":"period","followup":true,"start_at":"2026-10-13 15:00","duration_minutes":60,"clarification":null}')
        result = merge_conditions(update, MeetingRequest(mode="now"), now)
        self.assertEqual(result.end_at, "2026-10-13 16:00")

    def test_invalid_period_capacity_and_write_request_do_not_execute_query(self) -> None:
        available, rooms = Mock(), Mock()
        for request in (MeetingRequest(mode="period", start_at="2026-10-13 16:00", end_at="2026-10-13 15:00"),
                        MeetingRequest(mode="now", capacity=0), MeetingRequest(mode="unsupported")):
            reply = answer_meeting(request, self.user, rooms, available)
            self.assertTrue(reply["answer"])
        available.assert_not_called()
        rooms.assert_not_called()

    def test_graph_keeps_meeting_conditions_for_followup_and_clears_on_topic_change(self) -> None:
        parse = Mock(side_effect=[MeetingRequest(mode="period", capacity=8, start_at="2026-10-13 15:00"),
                                  MeetingRequest(mode="period", capacity=8, start_at="2026-10-13 15:00", end_at="2026-10-13 16:00")])
        available = Mock(return_value=[])
        graph = build_graph(Mock(return_value="meeting"), Mock(), Mock(), Mock(), Mock(), Mock(), available, None, parse)
        config = {"configurable": {"thread_id": "meeting"}}
        first = graph.invoke({"user": self.user, "question": "明天下午三点，八个人"}, config=config)
        self.assertIn("准备开多久", first["answer"])
        graph.invoke({"user": self.user, "question": "一个小时"}, config=config)
        self.assertEqual(parse.call_args.args[1], first["meeting_context"])
        available.assert_called_once_with(self.user, 8, "2026-10-13 15:00", "2026-10-13 16:00")
        final = graph.invoke({"user": self.user, "question": "你是谁"}, config=config)
        self.assertEqual(final["meeting_context"], "")

    def test_other_topic_returns_to_domain_router(self) -> None:
        parse = Mock(return_value=MeetingRequest(mode="other"))
        graph = build_graph(Mock(return_value="knowledge"), Mock(), Mock(), Mock(), Mock(), Mock(), Mock(), Mock(return_value="制度回答"), parse)
        result = graph.invoke({"user": self.user, "question": "年假多少天", "meeting_context": MeetingRequest(mode="now").model_dump_json()},
                              config={"configurable": {"thread_id": "topic"}})
        self.assertEqual(result["domain"], "knowledge")
        self.assertEqual(result["meeting_context"], "")

    def test_model_output_is_strict_and_errors_do_not_become_handoffs(self) -> None:
        client = Mock()
        for payload in ({"mode": "now", "capacity": True}, {"mode": "now", "role": "admin"}, {"mode": "execute_sql"}):
            client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(payload)))])
            with self.assertRaises(ServiceUnavailableError):
                interpret_meeting(client, "查会议室", "")
        client.chat.completions.create.side_effect = OpenAIError("test")
        with self.assertRaises(ServiceUnavailableError):
            interpret_meeting(client, "查会议室", "")

    def test_database_migration_preserves_existing_conversation(self) -> None:
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE users(username TEXT PRIMARY KEY)")
            initialize(db)
            db.execute("ALTER TABLE conversations DROP COLUMN meeting_context")
            db.execute("INSERT INTO conversations(id,username,role,title) VALUES ('existing','employee','employee','原对话')")
            initialize(db)
            initialize(db)
            self.assertEqual(db.execute("SELECT title,meeting_context FROM conversations WHERE id='existing'").fetchone(), ("原对话", ""))
