import hashlib
import hmac
import secrets
import sqlite3
from pathlib import Path
from typing import NotRequired, TypedDict

from app.organization import OrganizationFields, ensure_organization_schema, read_user, set_organization, validate_organization


DB_PATH = Path(__file__).resolve().parents[1] / "data" / "employee_assistant.db"

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "employee": frozenset({
        "kb:general", "leave:read_self", "leave:create", "ticket:create",
        "expense:create", "meeting:read", "meeting:book",
    }),
    "hr": frozenset({
        "kb:general", "kb:hr", "leave:read_self", "leave:create", "leave:read_all",
        "ticket:create", "expense:create", "meeting:read", "meeting:book", "leave:write_all",
    }),
    "it": frozenset({
        "kb:general", "kb:it", "leave:read_self", "leave:create", "ticket:create",
        "ticket:read_all", "ticket:manage", "expense:create", "meeting:read", "meeting:book",
    }),
    "finance": frozenset({
        "kb:general", "kb:finance", "leave:read_self", "leave:create", "ticket:create",
        "expense:create", "expense:read_all", "meeting:read", "meeting:book",
    }),
    "admin": frozenset({
        "user:create", "kb:general", "kb:hr", "kb:it", "kb:finance",
        "leave:read_self", "leave:create", "leave:read_all", "ticket:create",
        "ticket:read_all", "ticket:manage", "expense:create", "expense:read_all",
        "meeting:read", "meeting:book", "leave:write_all","handoff:manage","meeting:write","kb:write",
    }),
}


class User(TypedDict):
    username: str
    role: str
    display_name: NotRequired[str]
    department_id: NotRequired[int | None]
    department_name: NotRequired[str | None]
    job_level: NotRequired[str]
    manager_username: NotRequired[str | None]


def connect(database_path: Path = DB_PATH, *, check_same_thread: bool = True) -> sqlite3.Connection:
    database_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    database_path.parent.chmod(0o700)
    db = sqlite3.connect(database_path, check_same_thread=check_same_thread, timeout=15)
    database_path.chmod(0o600)
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            username TEXT PRIMARY KEY,
            salt BLOB NOT NULL,
            password_hash BLOB NOT NULL,
            role TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1
        )
    """)
    ensure_organization_schema(db)
    return db


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, 600_000
    )


def insert_user(
    db: sqlite3.Connection,
    username: str,
    password: str,
    role: str,
    organization: OrganizationFields | None = None,
) -> None:
    if not username or len(password) < 8 or role not in ROLE_PERMISSIONS:
        raise ValueError("账号、密码或角色无效；密码至少 8 位")

    salt = secrets.token_bytes(16)
    with db:
        db.execute(
            "INSERT INTO users(username, salt, password_hash, role) "
            "VALUES (?, ?, ?, ?)",
            (username, salt, hash_password(password, salt), role),
        )
        if organization is not None:
            set_organization(db, username, organization)
            validate_organization(db)


def create_first_admin(
    db: sqlite3.Connection,
    username: str,
    password: str,
) -> None:
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone() is not None:
        raise ValueError("已经有账号，不能再次初始化管理员")

    insert_user(db, username, password, "admin")


def authenticate(
    db: sqlite3.Connection,
    username: str,
    password: str,
) -> User | None:
    row = db.execute(
        "SELECT salt, password_hash, role, enabled "
        "FROM users WHERE username = ?",
        (username,),
    ).fetchone()

    if row is None:
        return None

    salt, expected_hash, role, enabled = row

    if not enabled or role not in ROLE_PERMISSIONS:
        return None

    if not hmac.compare_digest(
        hash_password(password, salt), expected_hash
    ):
        return None

    record = read_user(db, username)
    assert record is not None
    record.pop("enabled")
    return record


def has_permission(user: User, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(user["role"], frozenset())


def create_user(
    db: sqlite3.Connection,
    actor: User,
    username: str,
    password: str,
    role: str,
    organization: OrganizationFields | None = None,
) -> None:
    if not has_permission(actor, "user:create"):
        raise PermissionError("当前账号无权创建用户")

    insert_user(db, username, password, role, organization)
