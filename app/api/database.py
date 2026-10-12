import sqlite3

from app.knowledge.documents import ensure_knowledge_schema
from app.knowledge.feedback import ensure_feedback_schema
from app.knowledge.usage import ensure_usage_schema
from app.tools.expense import ensure_expense_schema
from app.tools.handoff import ensure_handoff_schema
from app.tools.it import ensure_it_schema
from app.tools.leave import ensure_leave_schema
from app.tools.meeting import ensure_meeting_schema


def initialize(db: sqlite3.Connection) -> None:
    for ensure in (
        ensure_it_schema, ensure_leave_schema, ensure_handoff_schema,
        ensure_expense_schema, ensure_meeting_schema, ensure_knowledge_schema,
        ensure_feedback_schema, ensure_usage_schema,
    ):
        ensure(db)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS web_sessions (
            token_hash TEXT PRIMARY KEY,
            username TEXT NOT NULL REFERENCES users(username),
            expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL REFERENCES users(username),
            title TEXT NOT NULL DEFAULT '新对话',
            role TEXT NOT NULL,
            pending_ticket TEXT NOT NULL DEFAULT '',
            knowledge_question TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_conversation_user ON conversations(username);
        CREATE TABLE IF NOT EXISTS chat_turns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            request_id TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL DEFAULT '',
            domain TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL CHECK(status IN ('processing','completed','failed')),
            feedback_allowed INTEGER NOT NULL DEFAULT 0,
            rating TEXT CHECK(rating IN ('有用','无用')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(conversation_id, request_id)
        );
        CREATE UNIQUE INDEX IF NOT EXISTS one_processing_turn
            ON chat_turns(conversation_id) WHERE status = 'processing';
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
    if "meeting_context" not in columns:
        db.execute("ALTER TABLE conversations ADD COLUMN meeting_context TEXT NOT NULL DEFAULT ''")
        db.commit()
