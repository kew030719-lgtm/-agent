import sqlite3

from app.auth import User, has_permission
from app.tools.notifications import ensure_notifications_schema, notify_ticket_created


def ensure_it_schema(db: sqlite3.Connection) -> None:
    ensure_notifications_schema(db)
    db.execute("""
        CREATE TABLE IF NOT EXISTS it_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by TEXT NOT NULL REFERENCES users(username),
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(it_tickets)")}
    if "request_key" not in columns:
        db.execute("ALTER TABLE it_tickets ADD COLUMN request_key TEXT")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_it_tickets_request ON it_tickets(created_by, request_key)")
    for name, definition in {
        "assigned_to": "TEXT REFERENCES users(username)",
        "resolution": "TEXT NOT NULL DEFAULT ''",
        "version": "INTEGER NOT NULL DEFAULT 0",
        "updated_at": "TEXT",
    }.items():
        if name not in columns:
            db.execute(f"ALTER TABLE it_tickets ADD COLUMN {name} {definition}")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ticket_owner ON it_tickets(created_by,id)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ticket_queue ON it_tickets(status,assigned_to)")
    db.execute("""CREATE TABLE IF NOT EXISTS it_ticket_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticket_id INTEGER NOT NULL REFERENCES it_tickets(id),
        actor TEXT NOT NULL REFERENCES users(username),
        action TEXT NOT NULL CHECK(action IN ('claim','resolve','confirm','reopen')),
        comment TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_ticket_event ON it_ticket_events(ticket_id,id)")
    db.commit()

def create_it_ticket(
    db: sqlite3.Connection,
    user: User,
    description: str,
    *, request_key: str | None = None,
) -> int:
    if not has_permission(user, "ticket:create"):
        raise PermissionError("当前账号无权创建 IT 工单")

    description = description.strip()
    if not description:
        raise ValueError("工单内容不能为空")

    with db:
        cursor = db.execute(
            "INSERT INTO it_tickets(created_by, description, request_key) VALUES (?, ?, ?) "
            "ON CONFLICT(created_by, request_key) DO NOTHING",
            (user["username"], description, request_key),
        )
        if cursor.rowcount and cursor.lastrowid is not None:
            notify_ticket_created(db, cursor.lastrowid, user["username"])
        if request_key is not None:
            row = db.execute("SELECT id,description FROM it_tickets WHERE created_by=? AND request_key=?",
                             (user["username"], request_key)).fetchone()
            if row is None or row[1] != description:
                raise ValueError("同一报修请求的内容发生变化，请重新确认")
            return int(row[0])
    if cursor.lastrowid is None:
        raise RuntimeError("工单创建失败")
    return cursor.lastrowid
