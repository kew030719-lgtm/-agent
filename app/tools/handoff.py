import sqlite3
from typing import TypedDict

from app.auth import User, has_permission

class Handoff(TypedDict):
    id: int
    created_by:str
    question:str
    reason:str
    created_at:str

def ensure_handoff_schema(db: sqlite3.Connection) -> None:
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

    if cursor.lastrowid is None:
        raise RuntimeError("转人工记录创建失败")
    return cursor.lastrowid

def list_pending_handoffs(
    db: sqlite3.Connection,
    actor: User,
) -> list[Handoff]:
    if not has_permission(actor, "handoff:manage"):
        raise PermissionError("当前账号无权查看待人工处理事项")

    rows = db.execute("""
        SELECT id, created_by, question, reason, created_at
        FROM handoffs
        WHERE status = 'pending'
        ORDER BY id
    """).fetchall()

    return [
        {
            "id": row[0],
            "created_by": row[1],
            "question": row[2],
            "reason": row[3],
            "created_at": row[4],
        }
        for row in rows
    ]


def resolve_handoff(
    db: sqlite3.Connection,
    actor: User,
    handoff_id: int,
) -> bool:
    if not has_permission(actor, "handoff:manage"):
        raise PermissionError("当前账号无权处理转人工事项")

    with db:
        cursor = db.execute("""
            UPDATE handoffs
            SET status = 'resolved',
                resolved_at = CURRENT_TIMESTAMP,
                resolved_by = ?
            WHERE id = ? AND status = 'pending'
        """, (actor["username"], handoff_id))

    return cursor.rowcount == 1