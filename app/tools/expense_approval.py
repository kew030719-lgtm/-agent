"""报销审批链：组织关系决定审批人，每次决策在同一写事务中完成。"""

import sqlite3
from typing import Literal

from app.auth import User, has_permission
from app.organization import JOB_LEVELS, read_user
from app.tools.notifications import ensure_notifications_schema, notify_approval_pending, notify_approval_result

ExpenseView = Literal["mine", "inbox", "reviewed", "all"]


class ApprovalConflict(ValueError):
    """审批已处理或组织关系已变化，需要刷新。"""


def ensure_approval_schema(db: sqlite3.Connection) -> None:
    ensure_notifications_schema(db)
    columns = {row[1] for row in db.execute("PRAGMA table_info(expense_requests)")}
    for name, definition in {"approval_round": "INTEGER NOT NULL DEFAULT 0", "approval_issue": "TEXT NOT NULL DEFAULT ''"}.items():
        if name not in columns:
            db.execute(f"ALTER TABLE expense_requests ADD COLUMN {name} {definition}")
    db.execute("""CREATE TABLE IF NOT EXISTS expense_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        expense_id INTEGER NOT NULL REFERENCES expense_requests(id),
        round INTEGER NOT NULL,
        position INTEGER NOT NULL,
        approver TEXT NOT NULL REFERENCES users(username),
        status TEXT NOT NULL CHECK(status IN ('pending','waiting','approved','rejected','superseded')),
        comment TEXT NOT NULL DEFAULT '',
        reviewed_at TEXT,
        UNIQUE(expense_id,round,position)
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_expense_approver ON expense_approvals(approver,status)")


def approval_chain(db: sqlite3.Connection, username: str) -> tuple[list[str], str]:
    # 兼容旧库：缺少组织档案时保留申请，提示配置，不能猜测审批人。
    if "manager_username" not in {row[1] for row in db.execute("PRAGMA table_info(users)")}:
        return [], "尚未配置组织档案，请联系管理员。"
    person = read_user(db, username)
    if person is None or not person["enabled"]:
        return [], "申请人不存在或已停用，请联系管理员。"
    ranks = {level: index for index, level in enumerate(JOB_LEVELS)}
    if person["job_level"] not in ranks:
        return [], "申请人职级无效，请联系管理员。"
    one_step = ranks[str(person["job_level"])] >= ranks["department_manager"]
    chain: list[str] = []
    seen = {username}
    while True:
        manager = person["manager_username"]
        if not manager:
            return [], "汇报链尚未配置完整（无直属上级），请联系管理员。"
        supervisor = read_user(db, str(manager))
        if supervisor is None or not supervisor["enabled"]:
            return [], "审批上级不存在或已停用，请联系管理员。"
        if manager in seen or supervisor["job_level"] not in ranks or ranks[str(supervisor["job_level"])] <= ranks[str(person["job_level"])]:
            return [], "汇报链职级或上下级关系无效，请联系管理员。"
        if supervisor["job_level"] in {"team_lead", "department_manager"} and (
            person["department_id"] is None or person["department_id"] != supervisor["department_id"]
        ):
            return [], "部门内审批人必须属于同一部门，请联系管理员。"
        chain.append(str(manager))
        seen.add(str(manager))
        if one_step or ranks[str(supervisor["job_level"])] >= ranks["department_manager"]:
            return chain, ""
        person = supervisor


def assign_approval(db: sqlite3.Connection, expense_id: int, username: str) -> None:
    """由调用者持有事务；旧审批记录保留，新汇报链从第一级重新审核。"""
    chain, issue = approval_chain(db, username)
    db.execute("UPDATE expense_approvals SET status='superseded' WHERE expense_id=? AND status IN ('pending','waiting')", (expense_id,))
    db.execute("UPDATE expense_requests SET approval_round=approval_round+1,approval_issue=? WHERE id=?", (issue, expense_id))
    round_number = db.execute("SELECT approval_round FROM expense_requests WHERE id=?", (expense_id,)).fetchone()[0]
    for index, approver in enumerate(chain):
        db.execute("INSERT INTO expense_approvals(expense_id,round,position,approver,status) VALUES (?,?,?,?,?)",
                   (expense_id, round_number, index + 1, approver, "pending" if index == 0 else "waiting"))
    notify_approval_pending(db, "expense", expense_id)


def rows_as_dicts(cursor: sqlite3.Cursor) -> list[dict[str, object]]:
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def approval_steps(db: sqlite3.Connection, expense_id: int) -> list[dict[str, object]]:
    return rows_as_dicts(db.execute("SELECT id,round,position,approver,status,comment,reviewed_at FROM expense_approvals WHERE expense_id=? ORDER BY round,position", (expense_id,)))


def routing_issue(db: sqlite3.Connection, row: dict[str, object], steps: list[dict[str, object]]) -> str:
    if row["status"] != "pending":
        return ""
    chain, issue = approval_chain(db, str(row["created_by"]))
    if issue:
        return issue
    recorded = [step["approver"] for step in steps if step["round"] == row["approval_round"]]
    if recorded != chain:
        return "组织关系已变化或尚未分配审批，请重新匹配审批人（从第一级重新审核）。"
    return ""


def summarize_expense(db: sqlite3.Connection, user: User, row: dict[str, object]) -> dict[str, object]:
    steps = approval_steps(db, int(row["id"]))
    issue = routing_issue(db, row, steps)
    active = next((step for step in steps if step["round"] == row["approval_round"] and step["status"] == "pending"), None)
    return {"id": row["id"], "created_by": row["created_by"], "amount": f"{int(row['amount_cents']) // 100}.{int(row['amount_cents']) % 100:02d}",
            "description": row["description"], "status": row["status"], "created_at": row["created_at"],
            "approval_round": row["approval_round"], "routing_issue": issue,
            "current_approver": active["approver"] if active and not issue else None,
            "can_review": bool(active and not issue and row["status"] == "pending" and active["approver"] == user["username"] and row["created_by"] != user["username"]),
            "can_reassign": bool(issue and row["status"] == "pending" and (row["created_by"] == user["username"] or has_permission(user, "user:create"))),
            "steps": steps}


def can_read_expense(db: sqlite3.Connection, user: User, row: dict[str, object]) -> bool:
    if row["created_by"] == user["username"] or has_permission(user, "expense:read_all"):
        return True
    steps = approval_steps(db, int(row["id"]))
    if any(step["approver"] == user["username"] and step["status"] in {"approved", "rejected"} for step in steps):
        return True
    return not routing_issue(db, row, steps) and any(
        step["round"] == row["approval_round"] and step["status"] == "pending" and step["approver"] == user["username"] for step in steps
    )


def get_expense(db: sqlite3.Connection, user: User, expense_id: int) -> dict[str, object]:
    rows = rows_as_dicts(db.execute("SELECT * FROM expense_requests WHERE id=?", (expense_id,)))
    if not rows or not can_read_expense(db, user, rows[0]):
        raise LookupError("申请不存在或无权查看")
    return summarize_expense(db, user, rows[0])


def list_expenses(db: sqlite3.Connection, user: User, view: ExpenseView, limit: int = 50, offset: int = 0) -> list[dict[str, object]]:
    if view == "all" and not has_permission(user, "expense:read_all"):
        raise PermissionError("当前账号无权查看全部报销")
    clauses = {"mine": "e.created_by=?", "inbox": "e.status='pending' AND EXISTS (SELECT 1 FROM expense_approvals a WHERE a.expense_id=e.id AND a.round=e.approval_round AND a.status='pending' AND a.approver=?)",
               "reviewed": "EXISTS (SELECT 1 FROM expense_approvals a WHERE a.expense_id=e.id AND a.approver=? AND a.status IN ('approved','rejected'))", "all": "1=1"}
    parameters = () if view == "all" else (user["username"],)
    # 在权限过滤之后分页；变化中的组织关系不会让旧审批人看到待审批正文。
    candidates = rows_as_dicts(db.execute(f"SELECT e.* FROM expense_requests e WHERE {clauses[view]} ORDER BY e.id DESC", parameters))
    visible: list[dict[str, object]] = []
    for row in candidates:
        if view == "inbox" and routing_issue(db, row, approval_steps(db, int(row["id"]))):
            continue
        visible.append(row)
        if len(visible) >= offset + limit:
            break
    return [summarize_expense(db, user, row) for row in visible[offset:offset + limit]]


def review_expense(db: sqlite3.Connection, user: User, expense_id: int, step_id: int, decision: Literal["approved", "rejected"], comment: str) -> dict[str, object]:
    comment = comment.strip()
    if decision not in {"approved", "rejected"} or len(comment) > 1000 or (decision == "rejected" and not comment):
        raise ValueError("驳回必须填写原因，审批意见最多1000字")
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = get_expense(db, user, expense_id)
        if row["status"] != "pending":
            raise ApprovalConflict("该申请已处理，请刷新查看结果")
        step = next((item for item in row["steps"] if item["id"] == step_id and item["status"] == "pending"), None)
        if not row["can_review"] or step is None or step["approver"] != user["username"] or step["round"] != row["approval_round"]:
            raise ApprovalConflict("审批人、审批阶段或组织关系已变化，请刷新列表")
        db.execute("UPDATE expense_approvals SET status=?,comment=?,reviewed_at=CURRENT_TIMESTAMP WHERE id=? AND status='pending'", (decision, comment, step_id))
        following = db.execute("SELECT id FROM expense_approvals WHERE expense_id=? AND round=? AND status='waiting' ORDER BY position LIMIT 1", (expense_id, row["approval_round"])).fetchone()
        if decision == "approved" and following:
            db.execute("UPDATE expense_approvals SET status='pending' WHERE id=?", (following[0],))
            notify_approval_pending(db, "expense", expense_id)
        else:
            db.execute("UPDATE expense_requests SET status=?,approval_issue='' WHERE id=?", (decision, expense_id))
            db.execute("UPDATE expense_approvals SET status='superseded' WHERE expense_id=? AND status='waiting'", (expense_id,))
            notify_approval_result(db, "expense", expense_id)
    return get_expense(db, user, expense_id)


def reassign_expense(db: sqlite3.Connection, user: User, expense_id: int) -> dict[str, object]:
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = get_expense(db, user, expense_id)
        if not row["can_reassign"]:
            raise ApprovalConflict("当前申请不需要重新分配，或你无权操作")
        assign_approval(db, expense_id, str(row["created_by"]))
    return get_expense(db, user, expense_id)
