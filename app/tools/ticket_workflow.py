"""IT 工单查询和状态流转；身份、权限和版本由服务端校验。"""

import sqlite3
from typing import Literal

from app.auth import User, has_permission
from app.tools.notifications import notify_ticket_transition

TicketView = Literal["mine", "queue", "assigned"]
TicketAction = Literal["claim", "resolve", "confirm", "reopen"]
STATUS_LABELS = {"open": "待处理", "in_progress": "处理中", "awaiting_confirmation": "待员工确认", "closed": "已关闭"}


class TicketConflict(ValueError):
    """工单已经变化，必须重新读取后操作。"""


def records(cursor: sqlite3.Cursor) -> list[dict[str, object]]:
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def active_actor(db: sqlite3.Connection, user: User) -> User:
    row = db.execute("SELECT role,enabled FROM users WHERE username=?", (user["username"],)).fetchone()
    if row is None or not row[1]:
        raise PermissionError("账号不存在或已停用")
    return {"username": user["username"], "role": row[0]}


def decorate(row: dict[str, object], actor: User) -> dict[str, object]:
    owner = row["created_by"] == actor["username"]
    handler = row["assigned_to"] == actor["username"] and has_permission(actor, "ticket:manage")
    return {**row, "status_label": STATUS_LABELS.get(str(row["status"]), str(row["status"])),
            "can_claim": has_permission(actor, "ticket:manage") and not owner and row["status"] == "open" and row["assigned_to"] is None,
            "can_resolve": handler and not owner and row["status"] == "in_progress",
            "can_confirm": owner and row["status"] == "awaiting_confirmation",
            "can_reopen": owner and row["status"] == "awaiting_confirmation"}


def get_ticket(db: sqlite3.Connection, user: User, ticket_id: int) -> dict[str, object]:
    actor = active_actor(db, user)
    rows = records(db.execute("SELECT * FROM it_tickets WHERE id=?", (ticket_id,)))
    if not rows or (rows[0]["created_by"] != actor["username"] and not has_permission(actor, "ticket:read_all")):
        raise LookupError("工单不存在或无权查看")
    item = decorate(rows[0], actor)
    item.pop("request_key", None)
    item["events"] = records(db.execute(
        "SELECT id,actor,action,comment,created_at FROM it_ticket_events WHERE ticket_id=? ORDER BY id", (ticket_id,)))
    return item


def list_tickets(db: sqlite3.Connection, user: User, view: TicketView = "mine", limit: int = 50, offset: int = 0) -> list[dict[str, object]]:
    actor = active_actor(db, user)
    if not 1 <= limit <= 100 or not 0 <= offset <= 1000000:
        raise ValueError("分页参数无效")
    if view == "mine":
        clause, args = "created_by=?", [actor["username"]]
    elif view in {"queue", "assigned"}:
        if not has_permission(actor, "ticket:manage"):
            raise PermissionError("当前账号无权处理 IT 工单")
        clause, args = ("status='open' AND assigned_to IS NULL", []) if view == "queue" else ("assigned_to=?", [actor["username"]])
    else:
        raise ValueError("列表范围无效")
    rows = records(db.execute(
        "SELECT id,created_by,description,status,created_at,assigned_to,resolution,version,updated_at "
        f"FROM it_tickets WHERE {clause} ORDER BY id DESC LIMIT ? OFFSET ?", (*args, limit, offset)))
    return [decorate(row, actor) for row in rows]


def transition_ticket(db: sqlite3.Connection, user: User, ticket_id: int, action: TicketAction,
                      version: int, comment: str = "") -> dict[str, object]:
    comment = comment.strip()
    if action not in {"claim", "resolve", "confirm", "reopen"} or version < 0 or len(comment) > 2000:
        raise ValueError("工单操作参数无效")
    if action in {"resolve", "reopen"} and not comment:
        raise ValueError("请填写处理结果或未解决原因")
    # 先获取写锁，再重新读取权限和版本，状态及历史在同一事务提交。
    with db:
        db.execute("BEGIN IMMEDIATE")
        actor = active_actor(db, user)
        item = get_ticket(db, actor, ticket_id)
        if action in {"claim", "resolve"} and not has_permission(actor, "ticket:manage"):
            raise PermissionError("当前账号无权处理 IT 工单")
        if action in {"claim", "resolve"} and item["created_by"] == actor["username"]:
            raise PermissionError("本人报修需由其他 IT 人员处理")
        if action == "resolve" and item["assigned_to"] != actor["username"]:
            raise PermissionError("只有当前处理人可以提交处理结果")
        if action in {"confirm", "reopen"} and item["created_by"] != actor["username"]:
            raise PermissionError("只有报修员工本人可以确认或退回")
        if item["version"] != version or not item[f"can_{action}"]:
            raise TicketConflict("工单状态已变化，请刷新详情后重试")
        target = {"claim": "in_progress", "resolve": "awaiting_confirmation", "confirm": "closed", "reopen": "in_progress"}[action]
        handler = actor["username"] if action == "claim" else item["assigned_to"]
        resolution = comment if action == "resolve" else item["resolution"]
        db.execute("UPDATE it_tickets SET status=?,assigned_to=?,resolution=?,version=version+1,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                   (target, handler, resolution, ticket_id))
        db.execute("INSERT INTO it_ticket_events(ticket_id,actor,action,comment) VALUES (?,?,?,?)",
                   (ticket_id, actor["username"], action, comment))
        notify_ticket_transition(db, ticket_id, action)
    return get_ticket(db, actor, ticket_id)


def query_own_tickets(db: sqlite3.Connection, user: User, ticket_id: int | None = None) -> str:
    actor = active_actor(db, user)
    # 助手的“我的工单”始终绑定登录人，即使此人有读取全部工单的权限。
    if ticket_id is not None:
        row = db.execute("SELECT id FROM it_tickets WHERE id=? AND created_by=?", (ticket_id, actor["username"])).fetchone()
        if row is None:
            return "未找到你名下的该工单，请到“IT 工单 → 我的报修”核对编号。"
        items = [get_ticket(db, actor, ticket_id)]
    else:
        items = list_tickets(db, actor, limit=21)
    if not items:
        return "你目前没有 IT 报修工单。可以描述具体故障，核对摘要后提交报修。"
    more = len(items) > 20
    lines = ["你的 IT 工单进度（按编号倒序）："]
    for item in items[:20]:
        lines.append(f"#{item['id']} · {item['status_label']} · 处理人：{item['assigned_to'] or '尚未认领'}\n故障：{item['description']}")
        if item["resolution"]:
            label = "上次处理结果（已退回，仍在处理）" if item["status"] == "in_progress" else "处理结果"
            lines.append(f"{label}：{item['resolution']}")
    if more:
        lines.append("这里只展示最近 20 条，更多工单请在列表中翻页查看。")
    lines.append("可在“IT 工单 → 我的报修”查看处理记录；待确认的工单可确认解决或填写原因退回。")
    return "\n".join(lines)
