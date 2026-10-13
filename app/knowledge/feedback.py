import json
import sqlite3
from typing import TypedDict

from app.auth import User, has_permission
from app.knowledge.access import sources_visible


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
    columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_feedback)")}
    if "source_ids_json" not in columns:
        db.execute("ALTER TABLE knowledge_feedback ADD COLUMN source_ids_json TEXT")
    db.commit()


def record_feedback(
    db: sqlite3.Connection,
    user: User,
    question: str,
    answer: str,
    rating: str,
    source_ids: list[int] | None = None,
) -> int:
    if rating not in {"有用", "无用"}:
        raise ValueError("反馈只能是“有用”或“无用”")
    ensure_feedback_schema(db)
    if source_ids is not None and not sources_visible(db, user, json.dumps(source_ids)):
        raise PermissionError("当前无权访问反馈所引用的资料")
    with db:
        cursor = db.execute(
            "INSERT INTO knowledge_feedback(username, question, answer, rating, source_ids_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (user["username"], question, answer, rating, json.dumps(source_ids) if source_ids is not None else None),
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
        "SELECT id, username, question, answer, rating, created_at, source_ids_json "
        "FROM knowledge_feedback ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "id": row[0], "username": row[1], "question": row[2] if sources_visible(db, actor, row[6]) else "资料无权访问或旧记录无来源",
            "answer": row[3] if sources_visible(db, actor, row[6]) else "正文已隐藏", "rating": row[4], "created_at": row[5],
        }
        for row in rows
    ]
