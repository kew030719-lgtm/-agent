"""按当前身份与业务状态汇总待办；通知已读不会改变待办状态。"""

import sqlite3

from app.auth import User, has_permission
from app.tools.expense_approval import get_expense
from app.tools.handoff import get_handoff
from app.tools.leave_workflow import get_leave
from app.tools.notifications import fresh_notification_user, unread_notification_count
from app.tools.ticket_workflow import get_ticket


def _collect_todos(db: sqlite3.Connection, user: User) -> list[dict[str, object]]:
    actor = fresh_notification_user(db, user)
    username = actor["username"]
    result: list[dict[str, object]] = []

    def add(kind: str, row: dict[str, object], action: str, title: str, summary: str, label: str) -> None:
        result.append({"key": f"{kind}:{row['id']}:{action}", "kind": kind,
                       "record_id": row["id"], "title": title, "summary": summary,
                       "action_label": label, "created_at": row["created_at"]})

    for kind, reader, label in (("expense", get_expense, "报销"), ("leave", get_leave, "请假")):
        # 仅取本人异常申请和当前轮到本人审批的记录；正文仍由业务 getter 重新授权。
        candidates = db.execute(f"""SELECT r.id FROM {kind}_requests r
            WHERE r.status='pending' AND (r.created_by=? OR EXISTS (
                SELECT 1 FROM {kind}_approvals a WHERE a.{kind}_id=r.id
                AND a.round=r.approval_round AND a.status='pending' AND a.approver=?))""",
                                (username, username)).fetchall()
        for candidate in candidates:
            try:
                row = reader(db, actor, int(candidate[0]))
            except (LookupError, PermissionError):
                continue
            if row["can_review"]:
                summary = f"申请人：{row['created_by']}；" + (f"金额：¥{row['amount']}" if kind == "expense" else f"{row['start_date']} 至 {row['end_date']}，{row['days']:g} 天")
                add(kind, row, "review", f"{label}申请 #{row['id']} 待审批", summary, "查看并审批")
            elif row["created_by"] == username and row.get("calendar_issue") and row.get("can_cancel"):
                add(kind, row, "calendar", f"请假申请 #{row['id']} 需重新提交", str(row["calendar_issue"]), "查看并撤回")
            elif row["created_by"] == username and row["can_reassign"]:
                add(kind, row, "reassign", f"{label}申请 #{row['id']} 需重新匹配审批人", str(row["routing_issue"]), "查看并重新匹配")

    manage_tickets = has_permission(actor, "ticket:manage")
    candidates = db.execute("""SELECT id FROM it_tickets WHERE
        (status='awaiting_confirmation' AND created_by=?) OR
        (? AND ((status='open' AND assigned_to IS NULL AND created_by<>?)
                OR (status='in_progress' AND assigned_to=?)))""",
                            (username, int(manage_tickets), username, username)).fetchall()
    for candidate in candidates:
        try:
            row = get_ticket(db, actor, int(candidate[0]))
        except (LookupError, PermissionError):
            continue
        for action, title, label in (("claim", "等待认领", "查看并认领"),
                                     ("resolve", "等待处理", "提交处理结果"),
                                     ("confirm", "等待确认结果", "确认或退回")):
            if row[f"can_{action}"]:
                add("ticket", row, action, f"IT 工单 #{row['id']} {title}", f"报修人：{row['created_by']}；{str(row['description'])[:160]}", label)
                break

    if has_permission(actor, "handoff:manage"):
        for candidate in db.execute("SELECT id FROM handoffs WHERE status='pending'").fetchall():
            try:
                row = get_handoff(db, actor, int(candidate[0]))
            except (LookupError, PermissionError):
                continue
            add("handoff", row, "reply", f"人工协助事项 #{row['id']} 待回复", f"申请人：{row['created_by']}；{str(row['question'])[:160]}", "查看并回复")
    result.sort(key=lambda row: (str(row["created_at"]), str(row["key"])), reverse=True)
    return result


def list_todos(db: sqlite3.Connection, user: User, *, limit: int = 50, offset: int = 0) -> list[dict[str, object]]:
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("分页参数无效")
    return _collect_todos(db, user)[offset:offset + limit]


def todo_count(db: sqlite3.Connection, user: User) -> int:
    return len(_collect_todos(db, user))


def work_center_summary(db: sqlite3.Connection, user: User) -> dict[str, int]:
    return {"todo_count": todo_count(db, user), "unread_count": unread_notification_count(db, user)}


def query_todos(db: sqlite3.Connection, user: User) -> str:
    rows = _collect_todos(db, user)
    if not rows:
        return "你当前没有需要处理的待办。申请进度和已处理记录可在对应业务页面查看。"
    lines = [f"你当前有 {len(rows)} 项待办："]
    lines.extend(f"- {row['title']}：{row['summary']}（{row['action_label']}）" for row in rows[:20])
    if len(rows) > 20:
        lines.append("这里只展示前 20 项。")
    lines.append("可在「待办与通知」打开详情处理；查看或阅读通知不会自动处理业务。")
    return "\n".join(lines)
