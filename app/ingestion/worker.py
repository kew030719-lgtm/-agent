"""建库执行逻辑；可注入模型和向量库以做离线失败/重试测试。"""

import os
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.config import configured_database_path, model_data_root
from app.ingestion.jobs import (
    DEFAULT_COLLECTION, LEASE_SECONDS, BusyJobError, LostLeaseError, claim_job, fail_job,
    require_lease, update_progress,
)
from app.knowledge.chunks import chunk_blocks
from app.knowledge.documents import _insert_assets, _insert_chunks
from app.knowledge.parsers.dispatch import parse_file


_MODELS: dict[tuple[int, str], Any] = {}


def _database(path: Path) -> sqlite3.Connection:
    if not path.is_file():
        raise RuntimeError("Worker 数据库不存在，请先启动并初始化 API")
    db = sqlite3.connect(path, timeout=15)
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _embedder(data_root: Path) -> Any:
    # Celery prefork 父进程不创建 ONNX session；子进程首次执行时再加载。
    from app.knowledge.embeddings import LocalEmbedder
    root = model_data_root(data_root)
    key = (os.getpid(), str(root))
    if key not in _MODELS:
        _MODELS[key] = LocalEmbedder(root)
    return _MODELS[key]


def _store() -> Any:
    from app.knowledge.vector_store import MilvusVectorStore
    return MilvusVectorStore()


def _heartbeat(database: Path, job_id: str, token: str, stop: threading.Event) -> None:
    while not stop.wait(20):
        db: sqlite3.Connection | None = None
        try:
            db = _database(database)
            db.execute("BEGIN IMMEDIATE")
            require_lease(db, job_id, token)
            db.execute("UPDATE ingestion_jobs SET lease_until=?,updated_at=CURRENT_TIMESTAMP WHERE id=? AND lease_token=?", (time.time() + LEASE_SECONDS, job_id, token))
            db.commit()
        except (PermissionError, LostLeaseError):
            return
        except sqlite3.Error:
            # 临时锁竞争不能恢复已过期 token；下一次仍需 require_lease。
            pass
        finally:
            if db is not None:
                db.close()


def _prepare_chunks(db: sqlite3.Connection, job: dict[str, Any], data_root: Path) -> int:
    job_id, token, document_id = job["id"], job["lease_token"], job["document_id"]
    count = db.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE document_id=?", (document_id,)).fetchone()[0]
    if count:
        return int(count)  # 已提交的切片保留主键，失败重试不会重新切分。
    row = db.execute("SELECT source_path FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
    if not row or not row[0]:
        raise ValueError("原文件不存在")
    path = (data_root / row[0]).resolve()
    expected = (data_root / "knowledge" / "files" / str(document_id)).resolve()
    if not path.is_relative_to(expected) or not path.is_file():
        raise ValueError("原文件路径无效")
    update_progress(db, job_id, token, "parsing")
    blocks = parse_file(path, data_root, document_id)  # OCR/解析不持有写事务。
    update_progress(db, job_id, token, "chunking")
    chunks = chunk_blocks(blocks)
    if not chunks:
        raise ValueError("文件没有可检索文字，扫描文件请检查 OCR 配置")
    db.execute("BEGIN IMMEDIATE")
    try:
        require_lease(db, job_id, token)
        _insert_assets(db, document_id, blocks)
        _insert_chunks(db, document_id, chunks)
        db.execute("UPDATE ingestion_jobs SET total_chunks=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (len(chunks), job_id))
        db.commit()
    except BaseException:
        db.rollback()
        raise
    return len(chunks)


def _finish(db: sqlite3.Connection, job_id: str, token: str, total: int) -> None:
    db.execute("BEGIN IMMEDIATE")
    try:
        job = require_lease(db, job_id, token)
        count = db.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE document_id=?", (job["document_id"],)).fetchone()[0]
        if not total or total != count:
            raise RuntimeError("切片数量在执行期间发生变化")
        db.execute("UPDATE knowledge_documents SET index_status='ready' WHERE id=?", (job["document_id"],))
        db.execute("UPDATE ingestion_jobs SET status='succeeded',stage='complete',processed_chunks=?,total_chunks=?,error='',lease_token=NULL,lease_until=NULL,updated_at=CURRENT_TIMESTAMP WHERE id=? AND lease_token=?", (total, total, job_id, token))
        db.commit()
    except BaseException:
        db.rollback()
        raise


def _safe_error(error: Exception, stage: str) -> str:
    if isinstance(error, PermissionError):
        return "任务负责人已停用或失去文档维护权限，请恢复授权后重试"
    if isinstance(error, LostLeaseError):
        return "任务执行租约已失效，请检查 Worker 后重试"
    if stage in {"parsing", "chunking"}:
        return "文件解析或切片失败，请检查文件内容及 OCR 配置后重试"
    if stage in {"embedding", "indexing"}:
        return "向量化或入库失败，请检查本地模型和向量库服务后重试"
    return "建库任务未完成，请检查 Worker 配置后重试"


def execute_job(
    job_id: str, database_path: Path | None = None, *,
    embedder_factory: Callable[[Path], Any] | None = None,
    store_factory: Callable[[], Any] | None = None,
) -> bool:
    """成功为 True；重复消息/失败返回 False，结果以 SQLite 为准。"""
    from app.knowledge.embeddings import index_document

    database = configured_database_path(database_path)
    data_root = database.parent
    db = _database(database)
    token: str | None = None
    store: Any = None
    stopped = threading.Event()
    heartbeat: threading.Thread | None = None
    try:
        job = claim_job(db, job_id, os.getenv("MILVUS_COLLECTION", DEFAULT_COLLECTION))
        if job is None:
            return False
        token = job["lease_token"]
        heartbeat = threading.Thread(target=_heartbeat, args=(database, job_id, token, stopped), daemon=True)
        heartbeat.start()
        total = _prepare_chunks(db, job, data_root)
        update_progress(db, job_id, token, "embedding", 0, total)
        embedder = (embedder_factory or _embedder)(data_root)
        require_lease(db, job_id, token)
        store = (store_factory or _store)()
        if getattr(store, "collection", job["collection_name"]) != job["collection_name"]:
            raise RuntimeError("向量集合配置不一致")

        def progress(stage: str, processed: int, count: int) -> None:
            update_progress(db, job_id, token, stage, processed, count)

        index_document(db, job["document_id"], embedder, store, progress=progress)
        # 成功返回不足以判定完整；逐批确认所有切片都已经写入对应 scope。
        rows = db.execute("SELECT c.id,d.scope FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id WHERE c.document_id=? ORDER BY c.id", (job["document_id"],)).fetchall()
        for start in range(0, len(rows), 256):
            require_lease(db, job_id, token)
            batch = rows[start:start + 256]
            scopes = store.existing_scopes([row[0] for row in batch])
            if any(scopes.get(chunk_id) != scope for chunk_id, scope in batch):
                raise RuntimeError("向量入库尚未完整")
        _finish(db, job_id, token, total)
        return True
    except BusyJobError:
        raise  # Celery 延迟重投，防止崩溃后立即重投的消息在租约到期前被吞掉。
    except Exception as error:
        db.rollback()
        row = db.execute("SELECT stage FROM ingestion_jobs WHERE id=?", (job_id,)).fetchone()
        if token is not None:
            fail_job(db, job_id, token, _safe_error(error, row[0] if row else ""))
        return False
    finally:
        stopped.set()
        if heartbeat is not None:
            heartbeat.join(timeout=1)
        if store is not None:
            try:
                store.close()
            except Exception:
                pass
        db.close()
