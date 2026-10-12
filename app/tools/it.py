import sqlite3

from app.auth import User, has_permission


def ensure_it_schema(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS it_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by TEXT NOT NULL REFERENCES users(username),
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    db.commit()

def create_it_ticket(
    db: sqlite3.Connection,
    user: User,
    description: str,
) -> int:
    if not has_permission(user, "ticket:create"):
        raise PermissionError("当前账号无权创建 IT 工单")

    description = description.strip()
    if not description:
        raise ValueError("工单内容不能为空")

    with db:
        cursor = db.execute(
            "INSERT INTO it_tickets(created_by, description) VALUES (?, ?)",
            (user["username"], description),
        )

    if cursor.lastrowid is None:
        raise RuntimeError("工单创建失败")

    return cursor.lastrowid