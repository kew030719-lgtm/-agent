import sqlite3
from typing import TypedDict

from app.auth import User, has_permission


class KnowledgeFeedback(TypedDict):
    id: int
    username: str
    question: str
    answer: str
    rating: str
    created_at: str


def ensure_feedback_schema(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL REFERENCES users(username),
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            rating TEXT NOT NULL CHECK (rating IN ('有用', '无用')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()


def record_feedback(
    db: sqlite3.Connection,
    user: User,
    question: str,
    answer: str,
    rating: str,
) -> int:
    if rating not in {"有用", "无用"}:
        raise ValueError("反馈只能是“有用”或“无用”")
    ensure_feedback_schema(db)
    with db:
        cursor = db.execute(
            "INSERT INTO knowledge_feedback(username, question, answer, rating) "
            "VALUES (?, ?, ?, ?)",
            (user["username"], question, answer, rating),
        )
    if cursor.lastrowid is None:
        raise RuntimeError("知识回答反馈保存失败")
    return cursor.lastrowid


def list_recent_feedback(
    db: sqlite3.Connection,
    actor: User,
    limit: int = 20,
) -> list[KnowledgeFeedback]:
    if not has_permission(actor, "kb:write"):
        raise PermissionError("当前账号无权查看知识回答反馈")
    ensure_feedback_schema(db)
    rows = db.execute(
        "SELECT id, username, question, answer, rating, created_at "
        "FROM knowledge_feedback ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "id": row[0], "username": row[1], "question": row[2],
            "answer": row[3], "rating": row[4], "created_at": row[5],
        }
        for row in rows
    ]
