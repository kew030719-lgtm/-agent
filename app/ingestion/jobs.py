"""建库任务的持久化、授权与租约。SQLite 是任务和文档状态的唯一来源。"""

import hashlib
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.auth import ROLE_PERMISSIONS, User
from app.knowledge.access import AccessPolicy, current_subject, require_document_manager, write_policy
from app.knowledge.catalog import ProductMetadata, register_product
from app.knowledge.documents import SCOPES


DEFAULT_COLLECTION = "employee_knowledge_bge_small_zh_v15"
LEASE_SECONDS = 120
REPUBLISH_AFTER_SECONDS = 60
JOB_FIELDS = (
    "id", "document_id", "kind", "status", "stage", "processed_chunks",
    "total_chunks", "attempts", "error", "created_at", "updated_at",
    "created_by", "collection_name", "lease_token", "lease_until", "published_at", "dispatch_token",
)


class JobConflictError(ValueError):
    """任务当前状态与操作冲突，可返回 HTTP 409。"""


class BusyJobError(RuntimeError):
    """有效执行者仍持有租约，消息应延迟重投。"""


class LostLeaseError(RuntimeError):
    """旧执行者已失去提交结果的资格。"""


def ensure_ingestion_schema(db: sqlite3.Connection) -> None:
    """由应用初始化调用；事务由调用者提交。"""
    db.execute("""CREATE TABLE IF NOT EXISTS ingestion_jobs (
        id TEXT PRIMARY KEY,
        document_id INTEGER NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
        kind TEXT NOT NULL CHECK(kind IN ('import','reindex')),
        created_by TEXT NOT NULL REFERENCES users(username),
        request_key TEXT NOT NULL,
        request_fingerprint TEXT NOT NULL,
        collection_name TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','running','succeeded','failed')),
        stage TEXT NOT NULL DEFAULT 'queued',
        processed_chunks INTEGER NOT NULL DEFAULT 0,
        total_chunks INTEGER NOT NULL DEFAULT 0,
        attempts INTEGER NOT NULL DEFAULT 0,
        error TEXT NOT NULL DEFAULT '',
        lease_token TEXT,
        lease_until REAL,
        published_at REAL,
        dispatch_token TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(created_by, request_key)
    )""")
    columns = {row[1] for row in db.execute("PRAGMA table_info(ingestion_jobs)")}
    if "dispatch_token" not in columns:
        db.execute("ALTER TABLE ingestion_jobs ADD COLUMN dispatch_token TEXT")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS ingestion_one_active_document "
               "ON ingestion_jobs(document_id) WHERE status IN ('queued','running')")
    db.execute("CREATE INDEX IF NOT EXISTS ingestion_owner_time "
               "ON ingestion_jobs(created_by,created_at)")


def _fresh_writer(db: sqlite3.Connection, user: User) -> User:
    subject = current_subject(db, user)
    if subject is None or "kb:write" not in ROLE_PERMISSIONS[str(subject["role"])]:
        raise PermissionError("当前账号无权维护知识库")
    return {"username": str(subject["username"]), "role": str(subject["role"])}


def _row(db: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = db.execute("SELECT " + ",".join(JOB_FIELDS) + " FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone()
    return dict(zip(JOB_FIELDS, row)) if row is not None else None


def _retryable(row: dict[str, Any]) -> bool:
    now = time.time()
    return bool(
        row["status"] == "failed"
        or (row["status"] == "running" and (row["lease_until"] or 0) <= now)
        or (row["status"] == "queued" and (row["published_at"] or 0) <= now - REPUBLISH_AFTER_SECONDS)
    )


def _public(db: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    result = {name: row[name] for name in JOB_FIELDS[:11]}
    result["can_retry"] = _retryable(row)
    title = db.execute("SELECT title FROM knowledge_documents WHERE id=?", (row["document_id"],)).fetchone()
    result["title"] = title[0] if title else ""
    return result


def get_job(db: sqlite3.Connection, user: User, job_id: str) -> dict[str, Any]:
    actor = _fresh_writer(db, user)
    row = _row(db, job_id)
    if row is None:
        raise LookupError("建库任务不存在")
    require_document_manager(db, actor, row["document_id"])
    return _public(db, row)


def list_jobs(db: sqlite3.Connection, user: User, limit: int = 20, offset: int = 0) -> list[dict[str, Any]]:
    actor = _fresh_writer(db, user)
    if not 1 <= limit <= 100 or offset < 0:
        raise ValueError("分页参数无效")
    ids = db.execute(
        "SELECT j.id FROM ingestion_jobs AS j JOIN knowledge_documents AS d ON d.id=j.document_id "
        "WHERE d.created_by=? ORDER BY j.created_at DESC,j.rowid DESC LIMIT ? OFFSET ?",
        (actor["username"], limit, offset),
    ).fetchall()
    return [get_job(db, actor, row[0]) for row in ids]


def ensure_document_idle(db: sqlite3.Connection, document_id: int) -> None:
    if db.execute("SELECT 1 FROM ingestion_jobs WHERE document_id=? AND status IN ('queued','running')", (document_id,)).fetchone():
        raise JobConflictError("文档正在建库，请先等待完成或重试已失效的任务")


def _fingerprint(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _request_key(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100:
        raise ValueError("请求编号不能为空且不能超过 100 字符")
    return value.strip()


def _existing(db: sqlite3.Connection, actor: User, request_key: str, fingerprint: str) -> dict[str, Any] | None:
    row = db.execute("SELECT id,request_fingerprint FROM ingestion_jobs WHERE created_by=? AND request_key=?", (actor["username"], request_key)).fetchone()
    if row is None:
        return None
    if row[1] != fingerprint:
        raise JobConflictError("同一请求编号不能用于不同的导入参数")
    return get_job(db, actor, row[0])


def _insert_job(db: sqlite3.Connection, actor: User, document_id: int, kind: str, request_key: str, fingerprint: str) -> str:
    job_id = str(uuid4())
    db.execute(
        "INSERT INTO ingestion_jobs(id,document_id,kind,created_by,request_key,request_fingerprint,collection_name,status) "
        "VALUES (?,?,?,?,?,?,?,'queued')",
        (job_id, document_id, kind, actor["username"], request_key, fingerprint,
         os.getenv("MILVUS_COLLECTION", DEFAULT_COLLECTION)),
    )
    db.execute("UPDATE knowledge_documents SET index_status='pending' WHERE id=?", (document_id,))
    return job_id


def create_import_job(
    db: sqlite3.Connection, user: User, path: Path, scope: str, data_root: Path,
    policy: AccessPolicy | None, product: ProductMetadata | None, request_key: str,
) -> dict[str, Any]:
    actor = _fresh_writer(db, user)
    request_key = _request_key(request_key)
    path, data_root = Path(path), Path(data_root)
    suffix = path.suffix.lower()
    if scope not in SCOPES:
        raise ValueError("文档范围必须是 general/hr/it/finance")
    if not path.is_file() or suffix not in {".pdf", ".md", ".docx", ".xlsx", ".xlsm", ".xls"}:
        raise ValueError("仅支持现有的 PDF、Markdown、DOCX、XLSX、XLSM、XLS 文件")
    # 文件复制和哈希在写事务外；发布前文件已永久保存。
    staging_root = data_root / "knowledge" / "staging"
    staging_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    staged = staging_root / (str(uuid4()) + suffix)
    stored: Path | None = None
    try:
        digest = hashlib.sha256()
        with path.open("rb") as source, staged.open("xb") as target:
            staged.chmod(0o600)
            while part := source.read(1024 * 1024):
                target.write(part)
                digest.update(part)
            target.flush()
            os.fsync(target.fileno())
        fingerprint = _fingerprint({"kind": "import", "source_name": path.name, "hash": digest.hexdigest(),
                                    "scope": scope, "policy": policy.model_dump() if policy else None,
                                    "product": product.model_dump() if product else None})
        db.execute("BEGIN IMMEDIATE")
        try:
            actor = _fresh_writer(db, user)
            existing = _existing(db, actor, request_key, fingerprint)
            if existing is not None:
                db.commit()
                return existing
            cursor = db.execute(
                "INSERT INTO knowledge_documents(title,content,scope,created_by,source_name,source_format,source_hash,index_status) "
                "VALUES (?,'',?,?,?,?,?,'pending')",
                (path.stem, scope, actor["username"], path.name, suffix[1:], digest.hexdigest()),
            )
            document_id = int(cursor.lastrowid or 0)
            if policy is not None:
                write_policy(db, actor, document_id, policy)
            if product is not None:
                register_product(db, actor, document_id, product)
            stored = data_root / "knowledge" / "files" / str(document_id) / f"source{suffix}"
            stored.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            staged.replace(stored)
            db.execute("UPDATE knowledge_documents SET source_path=? WHERE id=?", (stored.relative_to(data_root).as_posix(), document_id))
            job_id = _insert_job(db, actor, document_id, "import", request_key, fingerprint)
            db.commit()
        except BaseException:
            db.rollback()
            if stored is not None:
                shutil.rmtree(stored.parent, ignore_errors=True)
            raise
    finally:
        staged.unlink(missing_ok=True)
    return get_job(db, actor, job_id)


def create_reindex_job(db: sqlite3.Connection, user: User, document_id: int, request_key: str) -> dict[str, Any]:
    request_key = _request_key(request_key)
    fingerprint = _fingerprint({"kind": "reindex", "document_id": document_id})
    db.execute("BEGIN IMMEDIATE")
    try:
        actor = _fresh_writer(db, user)
        require_document_manager(db, actor, document_id)
        existing = _existing(db, actor, request_key, fingerprint)
        if existing is not None:
            db.commit()
            return existing
        ensure_document_idle(db, document_id)
        if not db.execute("SELECT 1 FROM knowledge_chunks WHERE document_id=?", (document_id,)).fetchone():
            raise ValueError("文档没有可重建的切片，请重试原导入任务")
        job_id = _insert_job(db, actor, document_id, "reindex", request_key, fingerprint)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return get_job(db, actor, job_id)


def retry_job(db: sqlite3.Connection, user: User, job_id: str) -> dict[str, Any]:
    db.execute("BEGIN IMMEDIATE")
    try:
        actor = _fresh_writer(db, user)
        row = _row(db, job_id)
        if row is None:
            raise LookupError("建库任务不存在")
        require_document_manager(db, actor, row["document_id"])
        if not _retryable(row):
            raise JobConflictError("任务尚在有效执行期或已完成，请勿重复重试")
        other = db.execute("SELECT 1 FROM ingestion_jobs WHERE document_id=? AND id<>? AND status IN ('queued','running')", (row["document_id"], job_id)).fetchone()
        if other:
            raise JobConflictError("该文档已有其他建库任务")
        if row["collection_name"] != os.getenv("MILVUS_COLLECTION", DEFAULT_COLLECTION):
            raise ValueError("任务所属向量集合与当前服务配置不一致")
        db.execute("UPDATE ingestion_jobs SET status='queued',stage='queued',error='',processed_chunks=0,total_chunks=0,lease_token=NULL,lease_until=NULL,published_at=NULL,dispatch_token=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (job_id,))
        db.execute("UPDATE knowledge_documents SET index_status='pending' WHERE id=?", (row["document_id"],))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return get_job(db, actor, job_id)


def publish_job(db: sqlite3.Connection, job_id: str) -> bool:
    """短事务领取投递令牌，网络调用在事务外，结果只更新对应投递尝试。"""
    dispatch_token = str(uuid4())
    db.execute("BEGIN IMMEDIATE")
    try:
        row = _row(db, job_id)
        if row is None or row["status"] != "queued":
            db.commit()
            return False
        if row["published_at"] is not None and row["published_at"] > time.time() - REPUBLISH_AFTER_SECONDS:
            db.commit()
            return False
        # 网络发送期间也记录投递令牌；人工重试会清除此令牌。
        db.execute("UPDATE ingestion_jobs SET dispatch_token=?,published_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued'", (dispatch_token, time.time(), job_id))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    try:
        _fresh_writer(db, {"username": row["created_by"], "role": ""})
        require_document_manager(db, {"username": row["created_by"], "role": ""}, row["document_id"])
        from app.ingestion.celery_app import celery_app
        celery_app.send_task("knowledge.ingest", args=[job_id], task_id=str(uuid4()))
    except Exception:
        with db:
            changed = db.execute("UPDATE ingestion_jobs SET status='failed',stage='dispatch',error='任务投递失败，请检查队列服务后重试',dispatch_token=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued' AND dispatch_token=?", (job_id, dispatch_token)).rowcount
            if changed:
                db.execute("UPDATE knowledge_documents SET index_status='failed' WHERE id=?", (row["document_id"],))
        return False
    with db:
        db.execute("UPDATE ingestion_jobs SET published_at=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status='queued' AND dispatch_token=?", (time.time(), job_id, dispatch_token))
    return True


def claim_job(db: sqlite3.Connection, job_id: str, collection: str) -> dict[str, Any] | None:
    """同一时刻只有一个有效 token；重复投递安全返回。"""
    db.execute("BEGIN IMMEDIATE")
    try:
        row = _row(db, job_id)
        if row is None or row["status"] in {"succeeded", "failed"}:
            db.commit()
            return None
        if row["status"] == "running" and (row["lease_until"] or 0) > time.time():
            raise BusyJobError("任务已有有效执行者")
        actor: User = {"username": row["created_by"], "role": ""}
        try:
            _fresh_writer(db, actor)
            require_document_manager(db, actor, row["document_id"])
            if row["collection_name"] != collection:
                raise ValueError("Worker 向量集合配置与任务不一致")
        except (PermissionError, ValueError) as error:
            # 尚未获得执行 token 的拒绝必须在本次 claim 事务内完成，
            # 不能释放锁后再把其他重试产生的 queued 状态标记失败。
            message = ("任务负责人已停用或失去文档维护权限，请恢复授权后重试"
                       if isinstance(error, PermissionError)
                       else "Worker 向量集合配置与任务不一致，请检查配置后重试")
            db.execute("UPDATE ingestion_jobs SET status='failed',error=?,lease_token=NULL,lease_until=NULL,dispatch_token=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND status=? AND lease_token IS ?", (message, job_id, row["status"], row["lease_token"]))
            db.execute("UPDATE knowledge_documents SET index_status='failed' WHERE id=?", (row["document_id"],))
            db.commit()
            return None
        token = str(uuid4())
        db.execute("UPDATE ingestion_jobs SET status='running',stage='parsing',attempts=attempts+1,error='',processed_chunks=0,total_chunks=0,lease_token=?,lease_until=?,dispatch_token=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=?", (token, time.time() + LEASE_SECONDS, job_id))
        db.execute("UPDATE knowledge_documents SET index_status='processing' WHERE id=?", (row["document_id"],))
        db.commit()
        return _row(db, job_id)
    except BaseException:
        db.rollback()
        raise


def require_lease(db: sqlite3.Connection, job_id: str, token: str) -> dict[str, Any]:
    row = _row(db, job_id)
    if row is None or row["status"] != "running" or row["lease_token"] != token or (row["lease_until"] or 0) <= time.time():
        raise LostLeaseError("任务执行租约已失效")
    actor: User = {"username": row["created_by"], "role": ""}
    _fresh_writer(db, actor)
    require_document_manager(db, actor, row["document_id"])
    return row


def update_progress(db: sqlite3.Connection, job_id: str, token: str, stage: str, processed: int = 0, total: int = 0) -> None:
    db.execute("BEGIN IMMEDIATE")
    try:
        require_lease(db, job_id, token)
        db.execute("UPDATE ingestion_jobs SET stage=?,processed_chunks=?,total_chunks=?,lease_until=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND lease_token=?", (stage, processed, total, time.time() + LEASE_SECONDS, job_id, token))
        db.commit()
    except BaseException:
        db.rollback()
        raise


def fail_job(db: sqlite3.Connection, job_id: str, token: str | None, error: str) -> None:
    """失败回写与新租约竞争同一写锁，旧执行者不能覆盖新执行者。"""
    if token is None:
        return  # 未获得执行权的拒绝由 claim_job 在自己的事务内落库。
    db.execute("BEGIN IMMEDIATE")
    try:
        row = _row(db, job_id)
        if row is None or row["status"] not in {"queued", "running"} or row["lease_token"] != token:
            db.commit()
            return
        changed = db.execute(
            "UPDATE ingestion_jobs SET status='failed',error=?,lease_token=NULL,lease_until=NULL,dispatch_token=NULL,updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND status=? AND lease_token IS ?",
            (error, job_id, row["status"], token),
        ).rowcount
        if changed:
            db.execute("UPDATE knowledge_documents SET index_status='failed' WHERE id=?", (row["document_id"],))
        db.commit()
    except BaseException:
        db.rollback()
        raise
