"""文档级授权。SQLite 是权限来源，规则不交给模型判断。"""

import hashlib
import json
import sqlite3
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.auth import ROLE_PERMISSIONS, User
from app.organization import JOB_LEVELS


class AccessGroup(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    department_ids: list[int] = Field(default_factory=list, max_length=100)
    min_job_level: Literal["employee", "team_lead", "department_manager", "general_manager", "chairman"] | None = None
    usernames: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def not_empty(self) -> "AccessGroup":
        if not self.department_ids and self.min_job_level is None and not self.usernames:
            raise ValueError("授权组不能没有条件；全员可见请明确选择")
        if any(value < 1 for value in self.department_ids) or any(not value.strip() for value in self.usernames):
            raise ValueError("部门或员工标识无效")
        return self


class AccessPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["legacy", "all", "restricted"] = "restricted"
    groups: list[AccessGroup] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def consistent(self) -> "AccessPolicy":
        if self.mode != "restricted" and self.groups:
            raise ValueError("只有自定义授权使用授权组")
        return self


def ensure_access_schema(db: sqlite3.Connection) -> None:
    columns = {row[1] for row in db.execute("PRAGMA table_info(knowledge_documents)")}
    if "access_policy" not in columns:
        db.execute("ALTER TABLE knowledge_documents ADD COLUMN access_policy TEXT NOT NULL DEFAULT '{\"mode\":\"legacy\",\"groups\":[]}'")
    db.execute("""CREATE TABLE IF NOT EXISTS knowledge_access_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, document_id INTEGER NOT NULL,
        actor TEXT NOT NULL, old_policy TEXT, new_policy TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""")


def current_subject(db: sqlite3.Connection, user: User) -> dict[str, object] | None:
    cursor = db.execute(
        "SELECT username,role,department_id,job_level,enabled FROM users WHERE username=?",
        (user["username"],),
    )
    row = cursor.fetchone()
    if row is None or not row[4] or row[1] not in ROLE_PERMISSIONS:
        return None
    return dict(zip([item[0] for item in cursor.description], row))


def policy_allows(subject: dict[str, object], policy: AccessPolicy, scope: str) -> bool:
    if policy.mode == "legacy":
        return f"kb:{scope}" in ROLE_PERMISSIONS.get(str(subject["role"]), frozenset())
    if policy.mode == "all":
        return True
    ranks = {name: rank for rank, name in enumerate(JOB_LEVELS)}
    for group in policy.groups:
        if group.department_ids and subject["department_id"] not in group.department_ids:
            continue
        if group.usernames and subject["username"] not in group.usernames:
            continue
        if group.min_job_level is not None and ranks.get(str(subject["job_level"]), -1) < ranks[group.min_job_level]:
            continue
        return True
    return False


def visible_document_ids(db: sqlite3.Connection, user: User) -> tuple[int, ...]:
    subject = current_subject(db, user)
    if subject is None:
        return ()
    visible: list[int] = []
    for document_id, scope, raw in db.execute("SELECT id,scope,access_policy FROM knowledge_documents WHERE index_status='ready' ORDER BY id"):
        try:
            policy = AccessPolicy.model_validate_json(raw)
        except ValueError:
            continue  # 损坏的规则默认拒绝。
        if policy_allows(subject, policy, scope):
            visible.append(document_id)
    return tuple(visible)


def access_stamp(db: sqlite3.Connection, user: User) -> str:
    """仅用于失效会话中的追问上下文；不缓存权限结果。"""
    state = [current_subject(db, user), list(db.execute("SELECT id,scope,access_policy,index_status FROM knowledge_documents ORDER BY id"))]
    # sqlite.Row 不能直接序列化。
    state[1] = [tuple(row) for row in state[1]]
    return hashlib.sha256(json.dumps(state, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def can_manage_document(db: sqlite3.Connection, user: User, document_id: int) -> bool:
    subject = current_subject(db, user)
    row = db.execute("SELECT created_by FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
    return bool(subject and row and row[0] == subject["username"] and
                "kb:write" in ROLE_PERMISSIONS[str(subject["role"])])


def require_document_manager(db: sqlite3.Connection, user: User, document_id: int) -> None:
    if not can_manage_document(db, user, document_id):
        raise PermissionError("仅文档负责人且具有知识维护权限的账号可修改该文档")


def validate_policy_targets(db: sqlite3.Connection, policy: AccessPolicy) -> None:
    for group in policy.groups:
        for department in group.department_ids:
            if db.execute("SELECT 1 FROM departments WHERE id=?", (department,)).fetchone() is None:
                raise ValueError("授权部门不存在")
        for username in group.usernames:
            if db.execute("SELECT 1 FROM users WHERE username=? AND enabled=1", (username,)).fetchone() is None:
                raise ValueError("授权员工不存在或已停用")


def write_policy(db: sqlite3.Connection, actor: User, document_id: int, policy: AccessPolicy) -> None:
    """调用者负责事务；导入时和切片一起提交，避免临时公开。"""
    require_document_manager(db, actor, document_id)
    validate_policy_targets(db, policy)
    old = db.execute("SELECT access_policy FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()[0]
    new = policy.model_dump_json()
    db.execute("UPDATE knowledge_documents SET access_policy=? WHERE id=?", (new, document_id))
    db.execute("INSERT INTO knowledge_access_audit(document_id,actor,old_policy,new_policy) VALUES (?,?,?,?)",
               (document_id, actor["username"], old, new))


def sources_visible(db: sqlite3.Connection, user: User, raw: str | None) -> bool:
    if raw is None:
        return False  # 旧回答没有可验证的来源，不重新展示正文。
    try:
        ids = json.loads(raw)
    except (TypeError, ValueError):
        return False
    return isinstance(ids, list) and bool(ids) and all(type(i) is int for i in ids) and set(ids) <= set(visible_document_ids(db, user))
