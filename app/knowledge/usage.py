import sqlite3
from typing import TypedDict

from app.auth import User, has_permission


class UsageSummary(TypedDict):
    outcome: str
    count: int
    average_ms: float


def ensure_usage_schema(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL REFERENCES users(username),
            outcome TEXT NOT NULL CHECK (outcome IN ('answered', 'handoff')),
            duration_ms INTEGER NOT NULL CHECK (duration_ms >= 0),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()


def record_usage(
    db: sqlite3.Connection,
    user: User,
    outcome: str,
    duration_ms: int,
) -> None:
    if outcome not in {"answered", "handoff"} or duration_ms < 0:
        raise ValueError("知识问答统计参数无效")
    ensure_usage_schema(db)
    with db:
        db.execute(
            "INSERT INTO knowledge_usage(username, outcome, duration_ms) "
            "VALUES (?, ?, ?)",
            (user["username"], outcome, duration_ms),
        )


def list_usage_summary(
    db: sqlite3.Connection,
    actor: User,
) -> list[UsageSummary]:
    if not has_permission(actor, "kb:write"):
        raise PermissionError("当前账号无权查看知识问答统计")
    ensure_usage_schema(db)
    rows = db.execute(
        "SELECT outcome, COUNT(*), AVG(duration_ms) "
        "FROM knowledge_usage GROUP BY outcome ORDER BY outcome"
    ).fetchall()
    return [
        {
            "outcome": row[0],
            "count": int(row[1]),
            "average_ms": float(row[2]),
        }
        for row in rows
    ]
