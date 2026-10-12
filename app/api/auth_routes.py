import os
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response

from app.api.dependencies import Actor, COOKIE_NAME, Database, token_hash
from app.api.schemas import LoginInput, Profile
from app.auth import ROLE_PERMISSIONS, User, authenticate


router = APIRouter(prefix="/auth", tags=["登录与权限"])


def profile(user: User) -> Profile:
    return Profile(**user, permissions=sorted(ROLE_PERMISSIONS[user["role"]]))


@router.post("/login", response_model=Profile)
def login(body: LoginInput, response: Response, request: Request, db: Database) -> Profile:
    user = authenticate(db, body.username.strip(), body.password)
    if user is None:
        raise HTTPException(401, "账号或密码不正确")
    token = secrets.token_urlsafe(32)
    ttl = int(os.getenv("API_SESSION_TTL_HOURS", "8")) * 3600
    with db:
        db.execute("DELETE FROM web_sessions WHERE expires_at<=? OR token_hash=?", (
            int(time.time()), token_hash(request.cookies.get(COOKIE_NAME, "")),
        ))
        db.execute("INSERT INTO web_sessions VALUES (?, ?, ?)", (
            token_hash(token), user["username"], int(time.time()) + ttl,
        ))
    response.set_cookie(
        COOKIE_NAME, token, httponly=True, samesite="strict", max_age=ttl,
        secure=os.getenv("API_COOKIE_SECURE", "false").lower() == "true", path="/api",
    )
    return profile(user)


@router.get("/me", response_model=Profile)
def me(user: Actor) -> Profile:
    return profile(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Database) -> None:
    with db:
        db.execute("DELETE FROM web_sessions WHERE token_hash=?", (
            token_hash(request.cookies.get(COOKIE_NAME, "")),
        ))
    response.delete_cookie(COOKIE_NAME, path="/api")
