import math
import sqlite3

from app.auth import User,has_permission

def ensure_leave_schema(db:sqlite3.Connection)->None:
    db.execute("""
    CREATE TABLE IF NOT EXISTS leave_balances (
            username TEXT PRIMARY KEY REFERENCES users(username),
            balance_days REAL NOT NULL CHECK (balance_days >= 0)
        )
        """)
    db.commit()

def set_leave_balance(
        db:sqlite3.Connection,
        actor:User,
        username:str,
        days:float,
)->None:
    if not has_permission(actor,"leave:write_all"):
        raise PermissionError("当前账号无权维护请假余额")
    if not math.isfinite(days) or days < 0:
        raise ValueError("余额必须是非负有限数字")
    if db.execute("SELECT 1 FROM users WHERE username = ?",(username,)).fetchone() is None:
        raise ValueError("账号不存在")

    with db:
        db.execute(
            """
            INSERT INTO leave_balances(username, balance_days)
            VALUES (?, ?)
            ON CONFLICT(username)
            DO UPDATE SET balance_days = excluded.balance_days
            """,
            (username, days),
        )


def get_own_leave_balance(db:sqlite3.Connection,user:User)->float|None:
    if not has_permission(user,"leave:read_self"):
        raise PermissionError("当前账号无权查询请假余额")
    row = db.execute(
        "SELECT balance_days FROM leave_balances WHERE username = ?",
        (user["username"],)
    ).fetchone()
    return float(row[0]) if row is not None else None