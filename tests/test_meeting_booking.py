import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from functools import partial
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.agent.meeting_dialogue import MeetingRequest, MeetingUpdate, answer_meeting, merge_conditions
from app.auth import ROLE_PERMISSIONS
from app.tools.meeting import add_meeting_room, book_meeting_room, ensure_meeting_schema, list_available_meeting_rooms, list_meeting_rooms


class MeetingBookingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("CREATE TABLE users(username TEXT PRIMARY KEY)")
        self.db.executemany("INSERT INTO users VALUES (?)", [("admin",), ("employee",), ("another",)])
        self.db.commit()
        ensure_meeting_schema(self.db)
        self.admin = {"username": "admin", "role": "admin"}
        self.employee = {"username": "employee", "role": "employee"}
        self.room = add_meeting_room(self.db, self.admin, "竹影会议室", "1楼", 10)

    def test_all_employees_can_book_but_only_admin_can_maintain_rooms(self) -> None:
        for role in ROLE_PERMISSIONS:
            self.assertIn("meeting:book", ROLE_PERMISSIONS[role])
            self.assertEqual("meeting:write" in ROLE_PERMISSIONS[role], role == "admin")
        with self.assertRaises(PermissionError):
            add_meeting_room(self.db, self.employee, "不应创建", "1楼", 8)
        bid = book_meeting_room(self.db, self.employee, self.room, "2099-10-13 15:00", "2099-10-13 16:00", 8)
        self.assertEqual(self.db.execute("SELECT created_by FROM meeting_bookings WHERE id=?", (bid,)).fetchone()[0], "employee")

    def test_confirmation_is_required_and_changes_require_a_new_preview(self) -> None:
        book = Mock(return_value=5)
        request = MeetingRequest(mode="book", capacity=8, room_name="竹影", start_at="2099-10-13 15:00", end_at="2099-10-13 16:00", duration_minutes=60)
        reply = answer_meeting(request, self.employee, partial(list_meeting_rooms, self.db), partial(list_available_meeting_rooms, self.db), book)
        book.assert_not_called()
        pending = MeetingRequest.model_validate_json(reply["meeting_context"])
        self.assertIsNotNone(pending.pending_booking)
        now = datetime(2099, 10, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
        changed = merge_conditions(MeetingUpdate(mode="confirm", start_at="2099-10-13 16:00"), pending, now)
        self.assertEqual(changed.mode, "book")
        self.assertIsNone(changed.pending_booking)
        self.assertEqual(changed.end_at, "2099-10-13 17:00")
        confirmed = merge_conditions(MeetingUpdate(mode="confirm"), pending, now)
        answer = answer_meeting(confirmed, self.employee, Mock(), Mock(), book)
        book.assert_called_once_with(self.employee, self.room, "2099-10-13 15:00", "2099-10-13 16:00", 8)
        self.assertEqual(answer["meeting_context"], "")

    def test_model_cannot_forge_pending_booking_or_confirm_without_preview(self) -> None:
        with self.assertRaises(ValidationError):
            MeetingUpdate.model_validate({"mode": "confirm", "pending_booking": {"room_id": 1}})
        result = merge_conditions(MeetingUpdate(mode="confirm"), None, datetime.now())
        self.assertEqual(result.mode, "clarify")
        book = Mock()
        answer_meeting(MeetingRequest(mode="confirm"), self.employee, Mock(), Mock(), book)
        book.assert_not_called()

    def test_conflict_repeat_past_capacity_and_disabled_room_are_checked(self) -> None:
        args = (self.db, self.employee, self.room, "2099-10-13 15:00", "2099-10-13 16:00", 8)
        bid = book_meeting_room(*args)
        self.assertEqual(book_meeting_room(*args), bid)
        with self.assertRaisesRegex(ValueError, "已有预订"):
            book_meeting_room(self.db, {"username": "another", "role": "employee"}, self.room, "2099-10-13 15:30", "2099-10-13 16:30", 8)
        for start, end, capacity in [("2000-01-01 10:00", "2000-01-01 11:00", 8), ("2099-10-13 17:00", "2099-10-13 18:00", 20)]:
            with self.assertRaises(ValueError):
                book_meeting_room(self.db, self.employee, self.room, start, end, capacity)
        self.db.execute("UPDATE meeting_rooms SET is_active=0 WHERE id=?", (self.room,))
        self.db.commit()
        with self.assertRaises(ValueError):
            book_meeting_room(self.db, self.employee, self.room, "2099-10-13 17:00", "2099-10-13 18:00", 8)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM meeting_bookings").fetchone()[0], 1)

    def test_conflict_after_preview_is_rechecked_at_confirmation(self) -> None:
        query = MeetingRequest(mode="book", room_id=self.room, start_at="2099-10-13 15:00", end_at="2099-10-13 16:00")
        reply = answer_meeting(query, self.employee, partial(list_meeting_rooms, self.db), partial(list_available_meeting_rooms, self.db))
        pending = MeetingRequest.model_validate_json(reply["meeting_context"]).model_copy(update={"mode": "confirm"})
        book_meeting_room(self.db, {"username": "another", "role": "employee"}, self.room, query.start_at, query.end_at)
        result = answer_meeting(pending, self.employee, Mock(), Mock(), partial(book_meeting_room, self.db))
        self.assertIn("未能完成预订", result["answer"])
        self.assertEqual(result["meeting_context"], "")

    def test_concurrent_users_cannot_double_book(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "booking.db"
            with sqlite3.connect(path) as db:
                self.db.backup(db)
            barrier = Barrier(2)

            def reserve(username: str) -> bool:
                with sqlite3.connect(path, timeout=10) as db:
                    barrier.wait()
                    try:
                        book_meeting_room(db, {"username": username, "role": "employee"}, self.room, "2099-10-13 15:00", "2099-10-13 16:00")
                        return True
                    except ValueError:
                        return False

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(reserve, ["employee", "another"]))
            self.assertEqual(sum(results), 1)
