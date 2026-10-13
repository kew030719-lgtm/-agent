"""站内通知：业务事务内投递，读取时重新检查账号与业务记录权限。"""

import sqlite3
from datetime import datetime, timezone
from typing import Iterator, Literal
from zoneinfo import ZoneInfo

from app.auth import ROLE_PERMISSIONS, User, has_permission

NotificationKind = Literal["leave", "expense", "ticket", "handoff"]
KIND_LABELS = {"leave": "请假", "expense": "报销", "ticket": "IT 工单", "handoff": "人工协助"}


def ensure_notifications_schema(db: sqlite3.Connection) -> None:
    """不提交调用方事务；各业务 schema 与完整 API 初始化都可以安全调用。"""
    db.execute("""CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        recipient TEXT NOT NULL REFERENCES users(username),
        kind TEXT NOT NULL CHECK(kind IN ('leave','expense','ticket','handoff')),
        record_id INTEGER NOT NULL,
        event_key TEXT NOT NULL,
        title TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        read_at TEXT,
        UNIQUE(recipient,kind,record_id,event_key)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_notification_recipient ON notifications(recipient,read_at,id)")
    db.execute("CREATE TABLE IF NOT EXISTS notification_migrations (name TEXT PRIMARY KEY, completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")


def emit_notification(db: sqlite3.Connection, recipient: str, kind: NotificationKind,
                      record_id: int, event_key: str, title: str, *,
                      created_at: str | None = None, read_at: str | None = None) -> None:
    """只能在业务写事务内调用；同一业务事件重试不会插入第二条通知。"""
    db.execute("""INSERT INTO notifications(recipient,kind,record_id,event_key,title,created_at,read_at)
        VALUES (?,?,?,?,?,COALESCE(?,CURRENT_TIMESTAMP),?)
        ON CONFLICT(recipient,kind,record_id,event_key) DO NOTHING""",
               (recipient, kind, record_id, event_key, title, created_at, read_at))


def permission_recipients(db: sqlite3.Connection, permission: str, *, excluding: str = "") -> list[str]:
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    if "role" not in columns:
        return []  # 兼容仅有 username 的旧工具测试表，不推测其权限。
    enabled = " AND enabled=1" if "enabled" in columns else ""
    return [str(row[0]) for row in db.execute(f"SELECT username,role FROM users WHERE username<>?{enabled}", (excluding,))
            if has_permission({"username": str(row[0]), "role": str(row[1])}, permission)]


def notify_approval_pending(db: sqlite3.Connection, kind: Literal["leave", "expense"],
                            record_id: int, *, created_at: str | None = None) -> None:
    table, fk = ("leave_approvals", "leave_id") if kind == "leave" else ("expense_approvals", "expense_id")
    row = db.execute(f"SELECT id,approver FROM {table} WHERE {fk}=? AND status='pending' ORDER BY round DESC,position LIMIT 1", (record_id,)).fetchone()
    if row is not None:
        emit_notification(db, str(row[1]), kind, record_id, f"approval:{row[0]}",
                          f"{KIND_LABELS[kind]}申请 #{record_id} 等待你审批", created_at=created_at)


def notify_approval_result(db: sqlite3.Connection, kind: Literal["leave", "expense"],
                           record_id: int, *, created_at: str | None = None) -> None:
    table = "leave_requests" if kind == "leave" else "expense_requests"
    row = db.execute(f"SELECT created_by,status,approval_round FROM {table} WHERE id=?", (record_id,)).fetchone()
    if row is not None and row[1] in {"approved", "rejected", "cancelled"}:
        label = {"approved": "已通过", "rejected": "已驳回", "cancelled": "已撤回"}[row[1]]
        emit_notification(db, str(row[0]), kind, record_id, f"result:{row[2]}:{row[1]}",
                          f"你的{KIND_LABELS[kind]}申请 #{record_id} {label}", created_at=created_at)


def notify_ticket_created(db: sqlite3.Connection, ticket_id: int, owner: str, *, created_at: str | None = None) -> None:
    for recipient in permission_recipients(db, "ticket:manage", excluding=owner):
        emit_notification(db, recipient, "ticket", ticket_id, "created",
                          f"IT 工单 #{ticket_id} 等待认领", created_at=created_at)


def notify_ticket_transition(db: sqlite3.Connection, ticket_id: int, action: str, *, created_at: str | None = None) -> None:
    row = db.execute("SELECT created_by,assigned_to,version FROM it_tickets WHERE id=?", (ticket_id,)).fetchone()
    if row is None:
        return
    labels = {"claim": "已被认领", "resolve": "已提交处理结果，请确认是否解决", "reopen": "被退回，请继续处理", "confirm": "已由员工确认解决"}
    recipient = row[0] if action in {"claim", "resolve"} else row[1]
    if recipient is not None:
        emit_notification(db, str(recipient), "ticket", ticket_id, f"version:{row[2]}:{action}",
                          f"IT 工单 #{ticket_id} {labels[action]}", created_at=created_at)


def notify_handoff_created(db: sqlite3.Connection, handoff_id: int, owner: str, *, created_at: str | None = None) -> None:
    for recipient in permission_recipients(db, "handoff:manage", excluding=owner):
        emit_notification(db, recipient, "handoff", handoff_id, "created",
                          f"人工协助事项 #{handoff_id} 等待处理", created_at=created_at)


def notify_handoff_reply(db: sqlite3.Connection, handoff_id: int) -> None:
    row = db.execute("SELECT created_by,resolved_at,read_at FROM handoffs WHERE id=? AND status='resolved' AND reply<>''", (handoff_id,)).fetchone()
    if row is not None:
        emit_notification(db, str(row[0]), "handoff", handoff_id, "replied",
                          f"你的人工协助事项 #{handoff_id} 收到了回复", created_at=row[1], read_at=row[2])


def sync_handoff_notification_read(db: sqlite3.Connection, username: str, handoff_id: int) -> None:
    """由旧人工事项已读 API 调用，共享业务事务。"""
    db.execute("""UPDATE notifications SET read_at=COALESCE(read_at,CURRENT_TIMESTAMP)
        WHERE recipient=? AND kind='handoff' AND record_id=? AND event_key='replied'""", (username, handoff_id))


def fresh_notification_user(db: sqlite3.Connection, user: User) -> User:
    """通知查询不信任旧会话中的 role，也不允许停用账号读取旧通知。"""
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    if "role" not in columns:
        raise PermissionError("账号权限尚未配置")
    fields = "role,enabled" if "enabled" in columns else "role,1"
    row = db.execute(f"SELECT {fields} FROM users WHERE username=?", (user["username"],)).fetchone()
    if row is None or not row[1] or row[0] not in ROLE_PERMISSIONS:
        raise PermissionError("账号不存在、已停用或权限无效")
    return {**user, "role": str(row[0])}


def _can_read_record(db: sqlite3.Connection, user: User, kind: str, record_id: int) -> bool:
    # 局部导入避免业务写函数与通知读取函数的循环依赖。
    from app.tools.expense_approval import get_expense
    from app.tools.handoff import get_handoff
    from app.tools.leave_workflow import get_leave
    from app.tools.ticket_workflow import get_ticket

    readers = {"leave": get_leave, "expense": get_expense, "ticket": get_ticket, "handoff": get_handoff}
    reader = readers.get(kind)
    if reader is None:
        return False
    try:
        reader(db, user, record_id)
    except (LookupError, PermissionError):
        return False
    return True


def _visible_notifications(db: sqlite3.Connection, actor: User, *, unread_only: bool = False) -> Iterator[dict[str, object]]:
    clause = " AND read_at IS NULL" if unread_only else ""
    cursor = db.execute("SELECT id,kind,record_id,title,created_at,read_at FROM notifications "
                        f"WHERE recipient=?{clause} ORDER BY created_at DESC,id DESC", (actor["username"],))
    names = [column[0] for column in cursor.description]
    for row in cursor:
        item = dict(zip(names, row))
        if _can_read_record(db, actor, str(item["kind"]), int(item["record_id"])):
            yield item


def list_notifications(db: sqlite3.Connection, user: User, *, unread_only: bool = False,
                       limit: int = 50, offset: int = 0) -> list[dict[str, object]]:
    if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
        raise ValueError("通知分页参数无效")
    actor = fresh_notification_user(db, user)
    result: list[dict[str, object]] = []
    for index, item in enumerate(_visible_notifications(db, actor, unread_only=unread_only)):
        if index < offset:
            continue
        result.append(item)
        if len(result) >= limit:
            break
    return result


def unread_notification_count(db: sqlite3.Connection, user: User) -> int:
    actor = fresh_notification_user(db, user)
    return sum(1 for _ in _visible_notifications(db, actor, unread_only=True))


def mark_notification_read(db: sqlite3.Connection, user: User, notification_id: int) -> bool:
    with db:
        db.execute("BEGIN IMMEDIATE")
        actor = fresh_notification_user(db, user)
        row = db.execute("SELECT kind,record_id,event_key FROM notifications WHERE id=? AND recipient=?",
                         (notification_id, actor["username"])).fetchone()
        if row is None or not _can_read_record(db, actor, str(row[0]), int(row[1])):
            raise LookupError("通知不存在或无权查看")
        db.execute("UPDATE notifications SET read_at=COALESCE(read_at,CURRENT_TIMESTAMP) WHERE id=?", (notification_id,))
        if row[0] == "handoff" and row[2] == "replied":
            db.execute("""UPDATE handoffs SET read_at=COALESCE(read_at,CURRENT_TIMESTAMP)
                WHERE id=? AND created_by=? AND status='resolved' AND reply<>''""", (row[1], actor["username"]))
    return True


def query_notifications(db: sqlite3.Connection, user: User) -> str:
    items = list_notifications(db, user, unread_only=True, limit=21)
    if not items:
        return "你目前没有未读站内通知。已读通知可在“待办与通知 → 通知”中查看；已读不代表业务已处理。"
    lines = ["你的未读站内通知（最多显示最近 20 条，时间为北京时间）："]
    for item in items[:20]:
        created_at = datetime.fromisoformat(str(item["created_at"])).replace(tzinfo=timezone.utc)
        display_at = created_at.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M")
        lines.append(f"• {item['title']}（{display_at}）")
    if len(items) > 20:
        lines.append("还有更多未读通知，请进入通知列表查看。")
    lines.append("查询不会标记已读；历史提醒中的待审批、待认领等状态可能已改变，请在“待办与通知”打开详情，以当前业务状态为准。通知已读不代表业务已处理。")
    return "\n".join(lines)


def backfill_notifications(db: sqlite3.Connection) -> None:
    """首次升级只补当前状态，保留原时间和人工已读状态，不伪造历史流转。"""
    required = {"notifications", "notification_migrations", "expense_requests", "expense_approvals", "leave_requests", "leave_approvals", "it_tickets", "it_ticket_events", "handoffs"}
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required <= tables:
        return
    with db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM notification_migrations WHERE name='initial_current_state_v1'").fetchone():
            return
        for kind, table, approvals, fk in (("leave", "leave_requests", "leave_approvals", "leave_id"), ("expense", "expense_requests", "expense_approvals", "expense_id")):
            for record_id, status, created_at in db.execute(f"SELECT id,status,created_at FROM {table}").fetchall():
                if status == "pending":
                    reviewed_at = db.execute(f"SELECT MAX(reviewed_at) FROM {approvals} WHERE {fk}=?", (record_id,)).fetchone()[0]
                    notify_approval_pending(db, kind, record_id, created_at=reviewed_at or created_at)
                else:
                    reviewed_at = db.execute(f"SELECT MAX(reviewed_at) FROM {approvals} WHERE {fk}=?", (record_id,)).fetchone()[0]
                    # 撤回时间记录在 updated_at；报销没有此列。
                    result_at = db.execute("SELECT updated_at FROM leave_requests WHERE id=?", (record_id,)).fetchone()[0] if kind == "leave" else reviewed_at
                    notify_approval_result(db, kind, record_id, created_at=result_at or created_at)
        for ticket_id, owner, status, created_at, updated_at in db.execute("SELECT id,created_by,status,created_at,updated_at FROM it_tickets").fetchall():
            if status == "open":
                notify_ticket_created(db, ticket_id, owner, created_at=created_at)
            else:
                action = {"awaiting_confirmation": "resolve", "closed": "confirm"}.get(status)
                if status == "in_progress":
                    latest = db.execute("SELECT action FROM it_ticket_events WHERE ticket_id=? ORDER BY id DESC LIMIT 1", (ticket_id,)).fetchone()
                    action = "reopen" if latest and latest[0] == "reopen" else "claim"
                if action:
                    notify_ticket_transition(db, ticket_id, action, created_at=updated_at or created_at)
        for handoff_id, owner, status, created_at in db.execute("SELECT id,created_by,status,created_at FROM handoffs").fetchall():
            if status == "pending":
                notify_handoff_created(db, handoff_id, owner, created_at=created_at)
            else:
                notify_handoff_reply(db, handoff_id)
        db.execute("INSERT INTO notification_migrations(name) VALUES ('initial_current_state_v1')")
