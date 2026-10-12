import sqlite3
from decimal import Decimal, InvalidOperation

from app.auth import User, has_permission

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
) -> int:
    if not has_permission(user, "expense:create"):
        raise PermissionError("当前账号无权发起报销")

    cents = amount_to_cents(amount)
    description = description.strip()
    if not description:
        raise ValueError("报销事由不能为空")

    with db:
        cursor = db.execute(
            "INSERT INTO expense_requests(created_by, amount_cents, description) "
            "VALUES (?, ?, ?)",
            (user["username"], cents, description),
        )

    if cursor.lastrowid is None:
        raise RuntimeError("报销申请创建失败")
    return cursor.lastrowid