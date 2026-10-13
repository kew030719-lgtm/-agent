import math
import sqlite3

from app.auth import User, has_permission
from app.tools.leave_workflow import (
    LeaveConflict,
    ensure_leave_workflow_schema,
    fresh_leave_user,
    leave_balance_summary,
)


def ensure_leave_schema(db: sqlite3.Connection) -> None:
    ensure_leave_workflow_schema(db)


def set_leave_balance(db: sqlite3.Connection, actor: User, username: str, days: float, *, request_key: str | None = None) -> None:
    if not math.isfinite(days) or days < 0:
        raise ValueError("余额必须是非负有限数字")
    if request_key is not None and (not request_key or len(request_key) > 128):
        raise ValueError("提交标识不能为空且最多128字")
    with db:
        db.execute("BEGIN IMMEDIATE")
        actor = fresh_leave_user(db, actor)
        if not has_permission(actor, "leave:write_all"):
            raise PermissionError("当前账号无权维护请假余额")
        if request_key is not None:
            previous = db.execute(
                "SELECT username,days FROM leave_balance_adjustments WHERE actor=? AND request_key=?",
                (actor["username"], request_key),
            ).fetchone()
            if previous is not None:
                if previous[0] != username or float(previous[1]) != days:
                    raise LeaveConflict("提交标识已用于另一笔余额维护，请重新确认")
                return
        if db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone() is None:
            raise ValueError("账号不存在")
        reserved = float(db.execute("SELECT COALESCE(SUM(days),0) FROM leave_requests WHERE created_by=? AND status='pending'", (username,)).fetchone()[0])
        if days < reserved:
            raise LeaveConflict(f"总余额不能低于待审批申请已预占的 {reserved:g} 天")
        db.execute("""INSERT INTO leave_balances(username,balance_days) VALUES (?,?)
            ON CONFLICT(username) DO UPDATE SET balance_days=excluded.balance_days""", (username, days))
        if request_key is not None:
            db.execute(
                "INSERT INTO leave_balance_adjustments(actor,username,days,request_key) VALUES (?,?,?,?)",
                (actor["username"], username, days, request_key),
            )


def get_own_leave_balance(db: sqlite3.Connection, user: User) -> float | None:
    """可用余额已经减去待审批申请的预占额度。"""
    return leave_balance_summary(db, user)["available_days"]
