import hashlib
import hmac
import secrets
import sqlite3
from pathlib import Path
from typing import TypedDict


DB_PATH = Path(__file__).resolve().parents[1] / "data" / "employee_assistant.db"

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "employee": frozenset({
        "kb:general", "leave:read_self", "ticket:create",
        "expense:create", "meeting:read", "meeting:book",
    }),
    "hr": frozenset({
        "kb:general", "kb:hr", "leave:read_self", "leave:read_all",
        "ticket:create", "expense:create", "meeting:read", "meeting:book", "leave:write_all",
    }),
    "it": frozenset({
        "kb:general", "kb:it", "leave:read_self", "ticket:create",
        "ticket:read_all", "expense:create", "meeting:read", "meeting:book",
    }),
    "finance": frozenset({
        "kb:general", "kb:finance", "leave:read_self", "ticket:create",
        "expense:create", "expense:read_all", "meeting:read", "meeting:book",
    }),
    "admin": frozenset({
        "user:create", "kb:general", "kb:hr", "kb:it", "kb:finance",
        "leave:read_self", "leave:read_all", "ticket:create",
        "ticket:read_all", "expense:create", "expense:read_all",
        "meeting:read", "meeting:book", "leave:write_all","handoff:manage","meeting:write","kb:write",
    }),
}


class User(TypedDict):
    username: str
    role: str


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

    return {"username": username, "role": role}


def has_permission(user: User, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(user["role"], frozenset())


def create_user(
    db: sqlite3.Connection,
    actor: User,
    username: str,
    password: str,
    role: str,
) -> None:
    if not has_permission(actor, "user:create"):
        raise PermissionError("当前账号无权创建用户")

    insert_user(db, username, password, role)
