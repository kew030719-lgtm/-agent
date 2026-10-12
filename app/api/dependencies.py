import hashlib
import sqlite3
import time
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request

from app.auth import ROLE_PERMISSIONS, User, connect


COOKIE_NAME = "employee_session"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    db = connect(request.app.state.database_path, check_same_thread=False)
    db.row_factory = sqlite3.Row
    try:
        yield db
    finally:
        db.close()


Database = Annotated[sqlite3.Connection, Depends(get_db)]


def current_user(request: Request, db: Database) -> User:
    token = request.cookies.get(COOKIE_NAME, "")
    row = db.execute(
        "SELECT u.username, u.role FROM web_sessions s JOIN users u ON u.username=s.username "
        "WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1",
        (token_hash(token), int(time.time())),
    ).fetchone()
    if row is None or row["role"] not in ROLE_PERMISSIONS:
        raise HTTPException(401, "登录已失效，请重新登录")
    return {"username": row["username"], "role": row["role"]}


Actor = Annotated[User, Depends(current_user)]


def owned_conversation(db: sqlite3.Connection, user: User, conversation_id: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM conversations WHERE id=? AND username=?",
        (conversation_id, user["username"]),
    ).fetchone()
    if row is None:
        raise HTTPException(404, "对话不存在")
    return row
