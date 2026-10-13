"""年假申请：工作半天计费、预占余额和组织审批在数据库事务内执行。"""

import hashlib
import json
import sqlite3
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from app.auth import ROLE_PERMISSIONS, User, has_permission
from app.tools.expense_approval import approval_chain
from app.tools.notifications import ensure_notifications_schema, notify_approval_pending, notify_approval_result

LeavePeriod = Literal["am", "pm"]
LeaveView = Literal["mine", "inbox", "reviewed", "all"]
CALENDAR_NOTE = "默认周一至周五为工作日，尚未自动包含法定节假日和调休；按管理员配置的单日工作日历覆盖。"


class LeaveConflict(ValueError):
    """申请、审批、日历或余额已发生变化，需要刷新。"""


def ensure_leave_workflow_schema(db: sqlite3.Connection) -> None:
    ensure_notifications_schema(db)
    db.execute("""CREATE TABLE IF NOT EXISTS leave_balances (
        username TEXT PRIMARY KEY REFERENCES users(username),
        balance_days REAL NOT NULL CHECK (balance_days >= 0)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS leave_balance_adjustments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor TEXT NOT NULL REFERENCES users(username),
        username TEXT NOT NULL REFERENCES users(username),
        days REAL NOT NULL CHECK(days >= 0),
        request_key TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(actor,request_key)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS leave_calendar (
        date TEXT PRIMARY KEY,
        is_workday INTEGER NOT NULL CHECK(is_workday IN (0,1)),
        note TEXT NOT NULL DEFAULT '',
        updated_by TEXT NOT NULL REFERENCES users(username),
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS leave_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_by TEXT NOT NULL REFERENCES users(username),
        start_date TEXT NOT NULL,
        start_period TEXT NOT NULL CHECK(start_period IN ('am','pm')),
        end_date TEXT NOT NULL,
        end_period TEXT NOT NULL CHECK(end_period IN ('am','pm')),
        reason TEXT NOT NULL,
        days REAL NOT NULL CHECK(days > 0),
        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled')),
        calendar_signature TEXT NOT NULL,
        request_key TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        approval_round INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(created_by,request_key)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS leave_request_slots (
        leave_id INTEGER NOT NULL REFERENCES leave_requests(id),
        date TEXT NOT NULL,
        period TEXT NOT NULL CHECK(period IN ('am','pm')),
        PRIMARY KEY(leave_id,date,period)
    )""")
    db.execute("""CREATE TABLE IF NOT EXISTS leave_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        leave_id INTEGER NOT NULL REFERENCES leave_requests(id),
        round INTEGER NOT NULL,
        position INTEGER NOT NULL,
        approver TEXT NOT NULL REFERENCES users(username),
        status TEXT NOT NULL CHECK(status IN ('pending','waiting','approved','rejected','superseded')),
        comment TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT,
        UNIQUE(leave_id,round,position)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_leave_owner_status ON leave_requests(created_by,status)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_leave_approver ON leave_approvals(approver,status)")
    db.commit()


def fresh_leave_user(db: sqlite3.Connection, user: User) -> User:
    """写事务中重新确认账号状态；兼容旧余额工具的最小 users 表。"""
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    if "role" not in columns:
        return user
    fields = "role,enabled" if "enabled" in columns else "role,1"
    row = db.execute(f"SELECT {fields} FROM users WHERE username=?", (user["username"],)).fetchone()
    if row is None or not row[1] or row[0] not in ROLE_PERMISSIONS:
        raise PermissionError("账号不存在、已停用或权限无效")
    return {**user, "role": str(row[0])}


def _rows(cursor: sqlite3.Cursor) -> list[dict[str, object]]:
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _parse_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("日期必须为 YYYY-MM-DD") from exc
    if parsed.isoformat() != value:
        raise ValueError("日期必须为 YYYY-MM-DD")
    return parsed


def _date_range(start_date: str, end_date: str) -> tuple[date, date]:
    start, end = _parse_date(start_date), _parse_date(end_date)
    if start > end or (end - start).days >= 366:
        raise ValueError("起止日期无效，单次范围最多366天")
    return start, end


def list_calendar(db: sqlite3.Connection, user: User, start_date: str, end_date: str) -> list[dict[str, object]]:
    user = fresh_leave_user(db, user)
    if not has_permission(user, "leave:read_self"):
        raise PermissionError("当前账号无权查看请假日历")
    start, end = _date_range(start_date, end_date)
    overrides = {row[0]: (bool(row[1]), row[2]) for row in db.execute(
        "SELECT date,is_workday,note FROM leave_calendar WHERE date BETWEEN ? AND ?", (start_date, end_date))}
    result: list[dict[str, object]] = []
    for offset in range((end - start).days + 1):
        current = start + timedelta(days=offset)
        is_workday, note = overrides.get(current.isoformat(), (current.weekday() < 5, "默认工作日" if current.weekday() < 5 else "默认周末休息"))
        result.append({"date": current.isoformat(), "is_workday": is_workday, "note": note})
    return result


def set_calendar_day(db: sqlite3.Connection, user: User, date: str, is_workday: bool, note: str) -> None:
    _parse_date(date)
    note = note.strip()
    if type(is_workday) is not bool or len(note) > 200:
        raise ValueError("工作日标记必须为布尔值，说明最多200字")
    with db:
        db.execute("BEGIN IMMEDIATE")
        actor = fresh_leave_user(db, user)
        if not has_permission(actor, "leave:write_all"):
            raise PermissionError("当前账号无权维护工作日历")
        db.execute("""INSERT INTO leave_calendar(date,is_workday,note,updated_by) VALUES (?,?,?,?)
            ON CONFLICT(date) DO UPDATE SET is_workday=excluded.is_workday,note=excluded.note,
            updated_by=excluded.updated_by,updated_at=CURRENT_TIMESTAMP""", (date, int(is_workday), note, actor["username"]))


def _slots(db: sqlite3.Connection, start_date: str, start_period: LeavePeriod, end_date: str, end_period: LeavePeriod, *, allow_past: bool = False) -> list[tuple[str, str]]:
    start, end = _date_range(start_date, end_date)
    if start_period not in {"am", "pm"} or end_period not in {"am", "pm"}:
        raise ValueError("请指定上午或下午")
    if start == end and start_period == "pm" and end_period == "am":
        raise ValueError("结束时间不能早于开始时间")
    if not allow_past and start < datetime.now(ZoneInfo("Asia/Shanghai")).date():
        raise ValueError("只能申请今天及以后的年假")
    overrides = {row[0]: bool(row[1]) for row in db.execute(
        "SELECT date,is_workday FROM leave_calendar WHERE date BETWEEN ? AND ?", (start_date, end_date))}
    slots: list[tuple[str, str]] = []
    for offset in range((end - start).days + 1):
        current = start + timedelta(days=offset)
        if not overrides.get(current.isoformat(), current.weekday() < 5):
            continue
        for period in ("am", "pm"):
            if current == start and start_period == "pm" and period == "am":
                continue
            if current == end and end_period == "am" and period == "pm":
                continue
            slots.append((current.isoformat(), period))
    return slots


def _signature(slots: list[tuple[str, str]]) -> str:
    return hashlib.sha256(json.dumps(slots, separators=(",", ":")).encode("utf-8")).hexdigest()


def _balance(db: sqlite3.Connection, username: str) -> dict[str, float | None]:
    row = db.execute("""SELECT
        (SELECT balance_days FROM leave_balances WHERE username=?),
        (SELECT COALESCE(SUM(days),0) FROM leave_requests WHERE created_by=? AND status='pending')""",
        (username, username)).fetchone()
    balance = float(row[0]) if row[0] is not None else None
    reserved = float(row[1])
    return {"balance_days": balance, "reserved_days": reserved, "available_days": None if balance is None else balance - reserved}


def leave_balance_summary(db: sqlite3.Connection, user: User) -> dict[str, float | None]:
    user = fresh_leave_user(db, user)
    if not has_permission(user, "leave:read_self"):
        raise PermissionError("当前账号无权查询请假余额")
    return _balance(db, user["username"])


def _validate_reason(reason: str) -> str:
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise ValueError("请假事由不能为空且最多1000字")
    return reason


def _check_overlap(db: sqlite3.Connection, username: str, slots: list[tuple[str, str]]) -> None:
    occupied = {(row[0], row[1]) for row in db.execute("""SELECT s.date,s.period FROM leave_request_slots s
        JOIN leave_requests r ON r.id=s.leave_id WHERE r.created_by=? AND r.status IN ('pending','approved')
        AND s.date BETWEEN ? AND ?""", (username, slots[0][0], slots[-1][0]))}
    if occupied.intersection(slots):
        raise LeaveConflict("所选工作半天与待审批或已通过的请假重叠，请调整日期")


def preview_leave(db: sqlite3.Connection, user: User, start_date: str, start_period: LeavePeriod, end_date: str, end_period: LeavePeriod, reason: str) -> dict[str, object]:
    user = fresh_leave_user(db, user)
    if not has_permission(user, "leave:create"):
        raise PermissionError("当前账号无权申请年假")
    reason = _validate_reason(reason)
    slots = _slots(db, start_date, start_period, end_date, end_period)
    if not slots:
        raise ValueError("所选时间不包含工作半天，无需申请年假")
    _check_overlap(db, user["username"], slots)
    balance = _balance(db, user["username"])
    days = len(slots) / 2
    available = balance["available_days"]
    if available is None:
        raise LeaveConflict("尚未配置年假余额，请联系人事维护")
    if available < days:
        raise LeaveConflict(f"可用年假仅 {available:g} 天，本次需要 {days:g} 天")
    chain, issue = approval_chain(db, user["username"])
    return {"created_by": user["username"], "start_date": start_date, "start_period": start_period,
            "end_date": end_date, "end_period": end_period, "reason": reason, "days": days,
            "calendar_signature": _signature(slots), "work_dates": list(dict.fromkeys(item[0] for item in slots)),
            "calendar_note": CALENDAR_NOTE, "available_days": available,
            "current_approver": chain[0] if chain else None, "routing_issue": issue}


def _assign(db: sqlite3.Connection, leave_id: int, username: str) -> None:
    chain, _ = approval_chain(db, username)
    db.execute("UPDATE leave_approvals SET status='superseded' WHERE leave_id=? AND status IN ('pending','waiting')", (leave_id,))
    db.execute("UPDATE leave_requests SET approval_round=approval_round+1 WHERE id=?", (leave_id,))
    round_number = db.execute("SELECT approval_round FROM leave_requests WHERE id=?", (leave_id,)).fetchone()[0]
    for index, approver in enumerate(chain):
        db.execute("INSERT INTO leave_approvals(leave_id,round,position,approver,status) VALUES (?,?,?,?,?)",
                   (leave_id, round_number, index + 1, approver, "pending" if index == 0 else "waiting"))
    notify_approval_pending(db, "leave", leave_id)


def create_leave_request(db: sqlite3.Connection, user: User, start_date: str, start_period: LeavePeriod, end_date: str, end_period: LeavePeriod, reason: str, *, request_key: str, expected_calendar_signature: str) -> int:
    if not request_key or len(request_key) > 128:
        raise ValueError("提交标识不能为空且最多128字")
    reason = _validate_reason(reason)
    with db:
        db.execute("BEGIN IMMEDIATE")
        actor = fresh_leave_user(db, user)
        if not has_permission(actor, "leave:create"):
            raise PermissionError("当前账号无权申请年假")
        existing = db.execute("""SELECT id,start_date,start_period,end_date,end_period,reason,calendar_signature
            FROM leave_requests WHERE created_by=? AND request_key=?""", (actor["username"], request_key)).fetchone()
        if existing is not None:
            if tuple(existing)[1:] != (start_date, start_period, end_date, end_period, reason, expected_calendar_signature):
                raise LeaveConflict("提交标识已用于另一份申请，请重新预览")
            return int(existing[0])
        preview = preview_leave(db, actor, start_date, start_period, end_date, end_period, reason)
        if preview["calendar_signature"] != expected_calendar_signature:
            raise LeaveConflict("工作日历已变化，请重新预览并确认天数")
        cursor = db.execute("""INSERT INTO leave_requests(created_by,start_date,start_period,end_date,end_period,reason,days,calendar_signature,request_key)
            VALUES (?,?,?,?,?,?,?,?,?)""", (actor["username"], start_date, start_period, end_date, end_period, reason,
                                            preview["days"], expected_calendar_signature, request_key))
        leave_id = cursor.lastrowid
        if leave_id is None:
            raise RuntimeError("未能创建年假申请")
        slots = _slots(db, start_date, start_period, end_date, end_period)
        db.executemany("INSERT INTO leave_request_slots(leave_id,date,period) VALUES (?,?,?)", [(leave_id, day, period) for day, period in slots])
        _assign(db, leave_id, actor["username"])
    return leave_id


def _steps(db: sqlite3.Connection, leave_id: int) -> list[dict[str, object]]:
    return _rows(db.execute("SELECT id,round,position,approver,status,comment,reviewed_at FROM leave_approvals WHERE leave_id=? ORDER BY round,position", (leave_id,)))


def _routing_issue(db: sqlite3.Connection, row: dict[str, object], steps: list[dict[str, object]]) -> str:
    if row["status"] != "pending":
        return ""
    chain, issue = approval_chain(db, str(row["created_by"]))
    if issue:
        return issue
    if chain != [step["approver"] for step in steps if step["round"] == row["approval_round"]]:
        return "组织关系已变化或尚未分配审批，请重新匹配审批人（从第一级重新审核）。"
    return ""


def _calendar_issue(db: sqlite3.Connection, row: dict[str, object]) -> str:
    if row["status"] != "pending":
        return ""
    slots = _slots(db, str(row["start_date"]), str(row["start_period"]), str(row["end_date"]), str(row["end_period"]), allow_past=True)
    if _signature(slots) != row["calendar_signature"]:
        return "申请期间的工作日历已变化，请申请人撤回后按新日历重新提交。"
    return ""


def _summarize(db: sqlite3.Connection, user: User, row: dict[str, object]) -> dict[str, object]:
    steps = _steps(db, int(row["id"]))
    routing_issue = _routing_issue(db, row, steps)
    calendar_issue = _calendar_issue(db, row)
    active = next((step for step in steps if step["round"] == row["approval_round"] and step["status"] == "pending"), None)
    pending = row["status"] == "pending"
    result = {name: value for name, value in row.items() if name not in {"request_key", "calendar_signature"}}
    result.update({"routing_issue": routing_issue, "calendar_issue": calendar_issue,
                   "current_approver": active["approver"] if active and pending and not routing_issue else None,
                   "can_review": bool(pending and active and not routing_issue and not calendar_issue and active["approver"] == user["username"] and row["created_by"] != user["username"]),
                   "can_cancel": bool(pending and row["created_by"] == user["username"]),
                   "can_reassign": bool(pending and routing_issue and not calendar_issue and (row["created_by"] == user["username"] or has_permission(user, "user:create"))),
                   "steps": steps})
    return result


def _can_read(db: sqlite3.Connection, user: User, row: dict[str, object]) -> bool:
    if row["created_by"] == user["username"] or has_permission(user, "leave:read_all"):
        return True
    steps = _steps(db, int(row["id"]))
    if any(step["approver"] == user["username"] and step["status"] in {"approved", "rejected"} for step in steps):
        return True
    return row["status"] == "pending" and not _routing_issue(db, row, steps) and any(
        step["approver"] == user["username"] and step["round"] == row["approval_round"] and step["status"] == "pending" for step in steps)


def get_leave(db: sqlite3.Connection, user: User, id: int) -> dict[str, object]:
    actor = fresh_leave_user(db, user)
    rows = _rows(db.execute("SELECT * FROM leave_requests WHERE id=?", (id,)))
    if not rows or not _can_read(db, actor, rows[0]):
        raise LookupError("请假申请不存在或无权查看")
    return _summarize(db, actor, rows[0])


def list_leaves(db: sqlite3.Connection, user: User, view: LeaveView = "mine", limit: int = 50, offset: int = 0) -> list[dict[str, object]]:
    actor = fresh_leave_user(db, user)
    if view not in {"mine", "inbox", "reviewed", "all"} or not 1 <= limit <= 200 or offset < 0:
        raise ValueError("无效的列表或分页参数")
    if view == "all" and not has_permission(actor, "leave:read_all"):
        raise PermissionError("当前账号无权查看全部请假")
    clauses = {"mine": "r.created_by=?",
               "inbox": "r.status='pending' AND EXISTS (SELECT 1 FROM leave_approvals a WHERE a.leave_id=r.id AND a.round=r.approval_round AND a.status='pending' AND a.approver=?)",
               "reviewed": "EXISTS (SELECT 1 FROM leave_approvals a WHERE a.leave_id=r.id AND a.approver=? AND a.status IN ('approved','rejected'))", "all": "1=1"}
    parameters = () if view == "all" else (actor["username"],)
    candidates = _rows(db.execute(f"SELECT r.* FROM leave_requests r WHERE {clauses[view]} ORDER BY r.id DESC", parameters))
    visible: list[dict[str, object]] = []
    for row in candidates:
        if view == "inbox" and _routing_issue(db, row, _steps(db, int(row["id"]))):
            continue
        visible.append(row)
        if len(visible) >= offset + limit:
            break
    return [_summarize(db, actor, row) for row in visible[offset:offset + limit]]


def review_leave(db: sqlite3.Connection, user: User, id: int, step_id: int, decision: Literal["approved", "rejected"], comment: str) -> dict[str, object]:
    comment = comment.strip()
    if decision not in {"approved", "rejected"} or len(comment) > 1000 or (decision == "rejected" and not comment):
        raise ValueError("驳回必须填写原因，审批意见最多1000字")
    with db:
        db.execute("BEGIN IMMEDIATE")
        actor = fresh_leave_user(db, user)
        row = get_leave(db, actor, id)
        if row["status"] != "pending":
            raise LeaveConflict("该申请已处理，请刷新查看结果")
        if row["calendar_issue"]:
            raise LeaveConflict(str(row["calendar_issue"]))
        step = next((item for item in row["steps"] if item["id"] == step_id and item["round"] == row["approval_round"] and item["status"] == "pending"), None)
        if not row["can_review"] or step is None or step["approver"] != actor["username"]:
            raise LeaveConflict("审批人、审批阶段或组织关系已变化，请刷新列表")
        db.execute("UPDATE leave_approvals SET status=?,comment=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'", (decision, comment, step_id))
        next_step = db.execute("SELECT id FROM leave_approvals WHERE leave_id=? AND round=? AND status='waiting' ORDER BY position LIMIT 1", (id, row["approval_round"])).fetchone()
        if decision == "approved" and next_step:
            db.execute("UPDATE leave_approvals SET status='pending' WHERE id=?", (next_step[0],))
            notify_approval_pending(db, "leave", id)
        else:
            if decision == "approved":
                balance = _balance(db, str(row["created_by"]))
                if balance["balance_days"] is None or balance["available_days"] < 0:
                    raise LeaveConflict("年假余额不足以覆盖预占，请联系人事核对")
                changed = db.execute("UPDATE leave_balances SET balance_days=balance_days-? WHERE username=? AND balance_days>=?", (row["days"], row["created_by"], row["days"]))
                if changed.rowcount != 1:
                    raise LeaveConflict("年假余额已变化，请刷新")
            db.execute("UPDATE leave_requests SET status=? WHERE id=?", (decision, id))
            db.execute("UPDATE leave_approvals SET status='superseded' WHERE leave_id=? AND status='waiting'", (id,))
            notify_approval_result(db, "leave", id)
        db.execute("UPDATE leave_requests SET version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (id,))
    return get_leave(db, actor, id)


def cancel_leave(db: sqlite3.Connection, user: User, id: int, version: int) -> dict[str, object]:
    with db:
        db.execute("BEGIN IMMEDIATE")
        actor = fresh_leave_user(db, user)
        row = get_leave(db, actor, id)
        if not row["can_cancel"] or row["version"] != version:
            raise LeaveConflict("申请已变化或你无权撤回，请刷新")
        db.execute("UPDATE leave_requests SET status='cancelled',version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (id,))
        db.execute("UPDATE leave_approvals SET status='superseded' WHERE leave_id=? AND status IN ('pending','waiting')", (id,))
        notify_approval_result(db, "leave", id)
    return get_leave(db, actor, id)


def reassign_leave(db: sqlite3.Connection, user: User, id: int, version: int) -> dict[str, object]:
    with db:
        db.execute("BEGIN IMMEDIATE")
        actor = fresh_leave_user(db, user)
        row = get_leave(db, actor, id)
        if row["calendar_issue"]:
            raise LeaveConflict(str(row["calendar_issue"]))
        if not row["can_reassign"] or row["version"] != version:
            raise LeaveConflict("申请无需重新分配、已经变化或你无权操作")
        _assign(db, id, str(row["created_by"]))
        db.execute("UPDATE leave_requests SET version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (id,))
    return get_leave(db, actor, id)


def query_own_leaves(db: sqlite3.Connection, user: User, id: int | None = None) -> str:
    actor = fresh_leave_user(db, user)
    if not has_permission(actor, "leave:read_self"):
        raise PermissionError("当前账号无权查询自己的请假")
    if id is not None:
        owner = db.execute("SELECT created_by FROM leave_requests WHERE id=?", (id,)).fetchone()
        if owner is None or owner[0] != actor["username"]:
            return "未找到你本人的该条请假申请，请核对编号。"
        rows = [get_leave(db, actor, id)]
    else:
        rows = list_leaves(db, actor, limit=20)
    if not rows:
        return "你目前没有请假申请。可以告诉我请假的日期、上午/下午和事由。"
    labels = {"pending": "待审批", "approved": "已通过", "rejected": "已驳回", "cancelled": "已撤回"}
    periods = {"am": "上午", "pm": "下午"}
    lines = ["你的年假申请（最多显示最近20条，完整记录见「请假与审批」）："]
    for row in rows:
        line = f"#{row['id']} {row['start_date']} {periods[row['start_period']]} 至 {row['end_date']} {periods[row['end_period']]}，{row['days']:g} 天，{labels[row['status']]}"
        if row["current_approver"]:
            line += f"，当前审批人：{row['current_approver']}"
        if row["calendar_issue"] or row["routing_issue"]:
            line += f"。{row['calendar_issue'] or row['routing_issue']}"
        rejected = next((step for step in reversed(row["steps"]) if step["status"] == "rejected"), None)
        if rejected:
            line += f"。驳回原因：{rejected['comment']}"
        lines.append(line)
    return "\n".join(lines)
