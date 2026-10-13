"""组织档案：部门和汇报关系独立于系统权限角色。"""

import sqlite3
from typing import TypedDict


DEFAULT_DEPARTMENTS = ("研发部", "销售部", "产品部", "运营部", "人事部", "IT支持部", "财务部", "管理部")
JOB_LEVELS = {
    "employee": "普通员工",
    "team_lead": "组长",
    "department_manager": "部门经理",
    "general_manager": "总经理",
    "chairman": "董事长",
}


class OrganizationFields(TypedDict):
    display_name: str
    department_id: int | None
    job_level: str
    manager_username: str | None


def ensure_organization_schema(db: sqlite3.Connection) -> None:
    columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
    if "manager_username" in columns:
        return
    with db:
        db.execute("CREATE TABLE IF NOT EXISTS departments (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE)")
        db.executemany("INSERT OR IGNORE INTO departments(name) VALUES (?)", [(name,) for name in DEFAULT_DEPARTMENTS])
        additions = {
            "display_name": "TEXT NOT NULL DEFAULT ''",
            "department_id": "INTEGER REFERENCES departments(id)",
            "job_level": "TEXT NOT NULL DEFAULT 'employee'",
            "manager_username": "TEXT REFERENCES users(username)",
        }
        for name, definition in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE users ADD COLUMN {name} {definition}")


def read_user(db: sqlite3.Connection, username: str) -> dict[str, object] | None:
    cursor = db.execute(
        "SELECT u.username,u.role,u.enabled,u.display_name,u.department_id,"
        "d.name AS department_name,u.job_level,u.manager_username "
        "FROM users u LEFT JOIN departments d ON d.id=u.department_id WHERE u.username=?",
        (username,),
    )
    row = cursor.fetchone()
    return dict(zip([item[0] for item in cursor.description], row)) if row is not None else None


def validate_organization(db: sqlite3.Connection) -> None:
    """在同一写事务内检查所有关系，调岗时也检查其现有下属。"""
    ranks = {level: index for index, level in enumerate(JOB_LEVELS)}
    rows = db.execute("SELECT username,department_id,job_level,manager_username,enabled FROM users").fetchall()
    people = {row[0]: row for row in rows}
    for username, department, level, manager, _ in rows:
        if level not in ranks:
            raise ValueError("无效的岗位职级")
        if manager is None:
            continue  # 允许尚未分配上级的历史账号和新建档案。
        supervisor = people.get(manager)
        if supervisor is None or not supervisor[4]:
            raise ValueError("直属上级不存在或已停用")
        if manager == username or ranks[supervisor[2]] <= ranks[level]:
            raise ValueError(f"{username} 的直属上级职级必须更高；不能选择自己或形成循环汇报")
        if supervisor[2] in {"team_lead", "department_manager"} and (
            department is None or department != supervisor[1]
        ):
            raise ValueError(f"{username} 应选择同部门的组长或部门经理；跨部门汇报请选择总经理或董事长")


def set_organization(db: sqlite3.Connection, username: str, fields: OrganizationFields) -> None:
    """由调用者持有写事务并在提交前运行 validate_organization。"""
    department = fields["department_id"]
    if department is not None and db.execute("SELECT 1 FROM departments WHERE id=?", (department,)).fetchone() is None:
        raise ValueError("部门不存在")
    if fields["job_level"] not in JOB_LEVELS:
        raise ValueError("无效的岗位职级")
    manager = fields["manager_username"]
    if manager is not None and db.execute("SELECT 1 FROM users WHERE username=?", (manager,)).fetchone() is None:
        raise ValueError("直属上级不存在")
    db.execute(
        "UPDATE users SET display_name=?,department_id=?,job_level=?,manager_username=? WHERE username=?",
        (fields["display_name"].strip(), department, fields["job_level"], manager, username),
    )
