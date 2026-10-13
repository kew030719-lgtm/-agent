import sqlite3
from decimal import Decimal, InvalidOperation

from app.auth import User, has_permission
from app.tools.expense_approval import assign_approval, ensure_approval_schema

MAX_SQLITE_INTEGER = (1 << 63) - 1

def ensure_expense_schema(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS expense_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_by TEXT NOT NULL REFERENCES users(username),
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    columns = {row[1] for row in db.execute("PRAGMA table_info(expense_requests)")}
    if "request_key" not in columns:
        db.execute("ALTER TABLE expense_requests ADD COLUMN request_key TEXT")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_expense_requests_request ON expense_requests(created_by, request_key)")
    ensure_approval_schema(db)
    db.commit()

def amount_to_cents(amount: str) -> int:
    try:
        value = Decimal(amount.strip())
    except InvalidOperation as exc:
        raise ValueError("报销金额必须是数字") from exc

    if not value.is_finite() or value <= 0:
        raise ValueError("报销金额必须大于 0")
    if value > Decimal(MAX_SQLITE_INTEGER) / 100:
        raise ValueError("报销金额过大")
    if value != value.quantize(Decimal("0.01")):
        raise ValueError("报销金额最多保留两位小数")

    return int(value * 100)

def create_expense_request(
    db: sqlite3.Connection,
    user: User,
    amount: str,
    description: str,
    *, request_key: str | None = None,
) -> int:
    if not has_permission(user, "expense:create"):
        raise PermissionError("当前账号无权发起报销")

    cents = amount_to_cents(amount)
    description = description.strip()
    if not description:
        raise ValueError("报销事由不能为空")

    with db:
        cursor = db.execute(
            "INSERT INTO expense_requests(created_by, amount_cents, description, request_key) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(created_by, request_key) DO NOTHING",
            (user["username"], cents, description, request_key),
        )
        if cursor.rowcount and cursor.lastrowid is not None:
            assign_approval(db, cursor.lastrowid, user["username"])
        if request_key is not None:
            row = db.execute("SELECT id,amount_cents,description FROM expense_requests WHERE created_by=? AND request_key=?",
                             (user["username"], request_key)).fetchone()
            if row is None or row[1] != cents or row[2] != description:
                raise ValueError("同一报销请求的内容发生变化，请重新确认")
            return int(row[0])
    if cursor.lastrowid is None:
        raise RuntimeError("报销申请创建失败")
    return cursor.lastrowid
