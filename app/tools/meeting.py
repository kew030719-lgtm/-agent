import sqlite3
import os
from zoneinfo import ZoneInfo
from typing import TypedDict

from app.auth import User, has_permission
from datetime import datetime

MAX_CAPACITY = (1 << 63) - 1


def validate_capacity(capacity: int) -> None:
    if not 1 <= capacity <= MAX_CAPACITY:
        raise ValueError("人数必须为有效的正整数，且不能超出支持范围")


class MeetingRoom(TypedDict):
    id: int
    name: str
    location: str
    capacity: int


def ensure_meeting_schema(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS meeting_rooms (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            location TEXT NOT NULL,
            capacity INTEGER NOT NULL CHECK (capacity > 0),
            is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS meeting_bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id INTEGER NOT NULL REFERENCES meeting_rooms(id),
            created_by TEXT NOT NULL REFERENCES users(username),
            start_at TEXT NOT NULL,
            end_at TEXT NOT NULL,
            CHECK (start_at < end_at)
        )
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS meeting_bookings_no_overlap
        BEFORE INSERT ON meeting_bookings
        WHEN EXISTS (
            SELECT 1 FROM meeting_bookings
            WHERE room_id = NEW.room_id
              AND start_at < NEW.end_at AND end_at > NEW.start_at
        )
        BEGIN
            SELECT RAISE(ABORT, 'booking overlap');
        END
    """)
    db.commit()


def add_meeting_room(
    db: sqlite3.Connection,
    actor: User,
    name: str,
    location: str,
    capacity: int,
) -> int:
    if not has_permission(actor, "meeting:write"):
        raise PermissionError("当前账号无权维护会议室")

    name = name.strip()
    location = location.strip()
    if not name or not location:
        raise ValueError("会议室名称和位置不能为空")
    validate_capacity(capacity)

    with db:
        cursor = db.execute(
            "INSERT INTO meeting_rooms(name, location, capacity) VALUES (?, ?, ?)",
            (name, location, capacity),
        )

    if cursor.lastrowid is None:
        raise RuntimeError("会议室创建失败")
    return cursor.lastrowid


def list_meeting_rooms(
    db: sqlite3.Connection,
    user: User,
    min_capacity: int = 1,
) -> list[MeetingRoom]:
    if not has_permission(user, "meeting:read"):
        raise PermissionError("当前账号无权查看会议室")
    validate_capacity(min_capacity)

    rows = db.execute("""
        SELECT id, name, location, capacity
        FROM meeting_rooms
        WHERE is_active = 1 AND capacity >= ?
        ORDER BY capacity, id
    """, (min_capacity,)).fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
            "location": row[2],
            "capacity": row[3],
        }
        for row in rows
    ]

TIME_FORMAT = "%Y-%m-%d %H:%M"


def normalize_period(start_at: str, end_at: str) -> tuple[str, str]:
    try:
        start = datetime.strptime(start_at.strip(), TIME_FORMAT)
        end = datetime.strptime(end_at.strip(), TIME_FORMAT)
    except ValueError as exc:
        raise ValueError("时间格式应为 YYYY-MM-DD HH:MM") from exc

    if end <= start:
        raise ValueError("结束时间必须晚于开始时间")

    return start.strftime(TIME_FORMAT), end.strftime(TIME_FORMAT)


def add_meeting_booking(
    db: sqlite3.Connection,
    actor: User,
    room_id: int,
    start_at: str,
    end_at: str,
) -> int:
    if not has_permission(actor, "meeting:write"):
        raise PermissionError("当前账号无权维护会议室预订")

    return _insert_booking(db, actor, room_id, start_at, end_at)


def book_meeting_room(
    db: sqlite3.Connection, user: User, room_id: int,
    start_at: str, end_at: str, min_capacity: int = 1,
) -> int:
    """员工只以登录身份预订；冲突由数据库触发器在写入时检查。"""
    if not has_permission(user, "meeting:book"):
        raise PermissionError("当前账号无权预订会议室")
    validate_capacity(min_capacity)
    start, end = normalize_period(start_at, end_at)
    now = datetime.now(ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))).strftime(TIME_FORMAT)
    if start < now:
        raise ValueError("不能预订已经过去的时段，请重新选择开始时间")
    room = db.execute("SELECT capacity FROM meeting_rooms WHERE id=? AND is_active=1", (room_id,)).fetchone()
    if room is None or room[0] < min_capacity:
        raise ValueError("会议室已停用、不存在或容量不足，请重新选择")
    try:
        return _insert_booking(db, user, room_id, start, end)
    except ValueError:
        # 网络重试或另一个会话重复提交同一人的同一预订时复用结果。
        existing = db.execute("SELECT id FROM meeting_bookings WHERE room_id=? AND created_by=? AND start_at=? AND end_at=?",
                              (room_id, user["username"], start, end)).fetchone()
        if existing:
            return int(existing[0])
        raise


def _insert_booking(
    db: sqlite3.Connection, actor: User, room_id: int, start_at: str, end_at: str,
) -> int:

    start, end = normalize_period(start_at, end_at)
    room = db.execute(
        "SELECT 1 FROM meeting_rooms WHERE id = ? AND is_active = 1",
        (room_id,),
    ).fetchone()
    if room is None:
        raise ValueError("会议室不存在或已停用")

    try:
        with db:
            cursor = db.execute("""
                INSERT INTO meeting_bookings(
                    room_id, created_by, start_at, end_at
                )
                VALUES (?, ?, ?, ?)
            """, (room_id, actor["username"], start, end))
    except sqlite3.IntegrityError as exc:
        if "booking overlap" in str(exc):
            raise ValueError("该会议室在此时段已有预订") from exc
        raise

    if cursor.lastrowid is None:
        raise RuntimeError("预订记录创建失败")
    return cursor.lastrowid


def list_available_meeting_rooms(
    db: sqlite3.Connection,
    user: User,
    min_capacity: int,
    start_at: str,
    end_at: str,
) -> list[MeetingRoom]:
    if not has_permission(user, "meeting:read"):
        raise PermissionError("当前账号无权查看会议室")
    validate_capacity(min_capacity)

    start, end = normalize_period(start_at, end_at)
    rows = db.execute("""
        SELECT r.id, r.name, r.location, r.capacity
        FROM meeting_rooms AS r
        WHERE r.is_active = 1 AND r.capacity >= ?
          AND NOT EXISTS (
              SELECT 1 FROM meeting_bookings AS b
              WHERE b.room_id = r.id
                AND b.start_at < ? AND b.end_at > ?
          )
        ORDER BY r.capacity, r.id
    """, (min_capacity, end, start)).fetchall()

    return [
        {
            "id": row[0],
            "name": row[1],
            "location": row[2],
            "capacity": row[3],
        }
        for row in rows
    ]
