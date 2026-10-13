import sqlite3

from fastapi import APIRouter, HTTPException

from app.api.dependencies import Actor, Database
from app.api.schemas import DepartmentInput, RoleOption, UserCreate, UserSummary, UserUpdate
from app.auth import ROLE_PERMISSIONS, User, create_user, has_permission
from app.organization import JOB_LEVELS, OrganizationFields, read_user, set_organization, validate_organization


router = APIRouter(prefix="/users", tags=["用户管理"])


def require_user_manager(actor: User) -> None:
    if not has_permission(actor, "user:create"):
        raise HTTPException(403, "当前账号无权管理用户")


def organization_fields(body: UserCreate | UserUpdate) -> OrganizationFields:
    return {
        "display_name": body.display_name, "department_id": body.department_id,
        "job_level": body.job_level, "manager_username": body.manager_username,
    }


def user_summary(db: sqlite3.Connection, username: str) -> UserSummary:
    record = read_user(db, username)
    if record is None:
        raise HTTPException(404, "账号不存在")
    return UserSummary(**record)


@router.get("", response_model=list[UserSummary])
def list_users(actor: Actor, db: Database) -> list[UserSummary]:
    require_user_manager(actor)
    return [user_summary(db, row[0]) for row in db.execute("SELECT username FROM users ORDER BY username").fetchall()]


@router.get("/roles", response_model=list[RoleOption])
def list_roles(actor: Actor) -> list[RoleOption]:
    require_user_manager(actor)
    return [RoleOption(role=role, permissions=sorted(permissions))
            for role, permissions in ROLE_PERMISSIONS.items()]


@router.get("/organization")
def organization_options(actor: Actor, db: Database) -> dict[str, object]:
    require_user_manager(actor)
    return {
        "departments": [dict(row) for row in db.execute("SELECT id,name FROM departments ORDER BY id")],
        "job_levels": [{"value": value, "label": label} for value, label in JOB_LEVELS.items()],
    }


@router.post("/departments", status_code=201)
def add_department(body: DepartmentInput, actor: Actor, db: Database) -> dict[str, object]:
    require_user_manager(actor)
    try:
        with db:
            cursor = db.execute("INSERT INTO departments(name) VALUES (?)", (body.name,))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "该部门已存在") from exc
    return {"id": cursor.lastrowid, "name": body.name}


@router.post("", response_model=UserSummary, status_code=201)
def add_user(body: UserCreate, actor: Actor, db: Database) -> UserSummary:
    require_user_manager(actor)
    try:
        create_user(db, actor, body.username, body.password, body.role, organization_fields(body))
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "该账号已存在，请使用其他账号名") from exc
    return user_summary(db, body.username)


@router.post("/{username:path}/profile", response_model=UserSummary)
def update_user(username: str, body: UserUpdate, actor: Actor, db: Database) -> UserSummary:
    require_user_manager(actor)
    user_summary(db, username)
    if username == actor["username"] and body.role != actor["role"]:
        raise HTTPException(400, "请由另一位管理员调整你的权限角色，避免失去管理入口")
    with db:
        db.execute("UPDATE users SET role=? WHERE username=?", (body.role, username))
        if db.execute("SELECT 1 FROM users WHERE role='admin' AND enabled=1").fetchone() is None:
            raise ValueError("至少需要保留一位已启用的系统管理员")
        set_organization(db, username, organization_fields(body))
        validate_organization(db)
    return user_summary(db, username)
