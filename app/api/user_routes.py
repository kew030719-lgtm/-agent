import sqlite3

from fastapi import APIRouter, HTTPException

from app.api.dependencies import Actor, Database
from app.api.schemas import RoleOption, UserCreate, UserSummary
from app.auth import ROLE_PERMISSIONS, User, create_user, has_permission


router = APIRouter(prefix="/users", tags=["用户管理"])


def require_user_manager(actor: User) -> None:
    if not has_permission(actor, "user:create"):
        raise HTTPException(403, "当前账号无权管理用户")


@router.get("", response_model=list[UserSummary])
def list_users(actor: Actor, db: Database) -> list[dict[str, object]]:
    require_user_manager(actor)
    rows = db.execute("SELECT username,role,enabled FROM users ORDER BY username").fetchall()
    return [dict(row) for row in rows]


@router.get("/roles", response_model=list[RoleOption])
def list_roles(actor: Actor) -> list[RoleOption]:
    require_user_manager(actor)
    return [RoleOption(role=role, permissions=sorted(permissions))
            for role, permissions in ROLE_PERMISSIONS.items()]


@router.post("", response_model=UserSummary, status_code=201)
def add_user(body: UserCreate, actor: Actor, db: Database) -> UserSummary:
    require_user_manager(actor)
    try:
        create_user(db, actor, body.username, body.password, body.role)
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "该账号已存在，请使用其他账号名") from exc
    return UserSummary(username=body.username, role=body.role, enabled=True)
