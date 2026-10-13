import sqlite3
from typing import Literal, TypedDict

from app.auth import User, has_permission
from app.tools.notifications import (ensure_notifications_schema, notify_handoff_created,
                                     notify_handoff_reply, sync_handoff_notification_read)

class Handoff(TypedDict):
    id: int
    created_by:str
    question:str
    reason:str
    created_at:str
    status: str
    reply: str
    resolved_at: str | None
    resolved_by: str | None
    read_at: str | None

def ensure_handoff_schema(db: sqlite3.Connection) -> None:
    ensure_notifications_schema(db)
    db.execute("""
        CREATE TABLE IF NOT EXISTS handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by TEXT NOT NULL REFERENCES users(username),
            question TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'resolved')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TEXT,
            resolved_by TEXT REFERENCES users(username)
        )
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(handoffs)")}
    for name, definition in {"reply": "TEXT NOT NULL DEFAULT ''", "read_at": "TEXT"}.items():
        if name not in columns:
            db.execute(f"ALTER TABLE handoffs ADD COLUMN {name} {definition}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_handoff_owner_status ON handoffs(created_by,status)")
    db.commit()

def create_handoff(
    db: sqlite3.Connection,
    user: User,
    question: str,
    reason: str,
) -> int:
    question = question.strip()
    reason = reason.strip()
    if not question or not reason:
        raise ValueError("转人工问题和原因不能为空")

    with db:
        cursor = db.execute(
            "INSERT INTO handoffs(created_by, question, reason) VALUES (?, ?, ?)",
            (user["username"], question, reason),
        )
        if cursor.lastrowid is not None:
            notify_handoff_created(db, cursor.lastrowid, user["username"])

    if cursor.lastrowid is None:
        raise RuntimeError("转人工记录创建失败")
    return cursor.lastrowid

def list_pending_handoffs(
    db: sqlite3.Connection,
    actor: User,
) -> list[Handoff]:
    if not has_permission(actor, "handoff:manage"):
        raise PermissionError("当前账号无权查看待人工处理事项")

    return list_handoffs(db, actor, mine=False, status="pending", limit=None)


HANDOFF_COLUMNS = "id,created_by,question,reason,created_at,status,reply,resolved_at,resolved_by,read_at"


def handoff_rows(cursor: sqlite3.Cursor) -> list[Handoff]:
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def list_handoffs(db: sqlite3.Connection, actor: User, *, mine: bool,
                  status: Literal["pending", "resolved", "all"] = "all",
                  limit: int | None = 50, offset: int = 0) -> list[Handoff]:
    if not mine and not has_permission(actor, "handoff:manage"):
        raise PermissionError("当前账号无权查看他人的人工事项")
    clauses: list[str] = []
    parameters: list[object] = []
    if mine:
        clauses.append("created_by=?")
        parameters.append(actor["username"])
    if status != "all":
        clauses.append("status=?")
        parameters.append(status)
    sql = f"SELECT {HANDOFF_COLUMNS} FROM handoffs WHERE " + (" AND ".join(clauses) or "1=1") + " ORDER BY id DESC"
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
    return handoff_rows(db.execute(sql, parameters))


def get_handoff(db: sqlite3.Connection, actor: User, handoff_id: int) -> Handoff:
    sql = f"SELECT {HANDOFF_COLUMNS} FROM handoffs WHERE id=?"
    parameters: list[object] = [handoff_id]
    if not has_permission(actor, "handoff:manage"):
        sql += " AND created_by=?"
        parameters.append(actor["username"])
    rows = handoff_rows(db.execute(sql, parameters))
    if not rows:
        raise LookupError("人工事项不存在或无权查看")
    return rows[0]


def unread_handoffs(db: sqlite3.Connection, actor: User) -> int:
    return int(db.execute("SELECT COUNT(*) FROM handoffs WHERE created_by=? AND status='resolved' AND reply<>'' AND read_at IS NULL", (actor["username"],)).fetchone()[0])


def mark_handoff_read(db: sqlite3.Connection, actor: User, handoff_id: int) -> bool:
    with db:
        cursor = db.execute("UPDATE handoffs SET read_at=COALESCE(read_at,CURRENT_TIMESTAMP) WHERE id=? AND created_by=? AND status='resolved' AND reply<>''", (handoff_id, actor["username"]))
        if cursor.rowcount == 1:
            sync_handoff_notification_read(db, actor["username"], handoff_id)
    return cursor.rowcount == 1


def resolve_handoff(
    db: sqlite3.Connection,
    actor: User,
    handoff_id: int,
    reply: str,
) -> bool:
    if not has_permission(actor, "handoff:manage"):
        raise PermissionError("当前账号无权处理转人工事项")

    reply = reply.strip()
    if not reply or len(reply) > 4000:
        raise ValueError("处理回复不能为空，且不能超过4000字")

    with db:
        cursor = db.execute("""
            UPDATE handoffs
            SET status = 'resolved',
                resolved_at = CURRENT_TIMESTAMP,
                resolved_by = ?,
                reply = ?,
                read_at = NULL
            WHERE id = ? AND status = 'pending'
        """, (actor["username"], reply, handoff_id))
        if cursor.rowcount == 1:
            notify_handoff_reply(db, handoff_id)

    return cursor.rowcount == 1
