import shutil
import sqlite3
from pathlib import Path

from app.auth import User, has_permission
from app.knowledge.access import AccessPolicy, require_document_manager
from app.knowledge.documents import add_knowledge_file, ensure_knowledge_schema
from app.knowledge.catalog import ProductMetadata, register_product
from app.knowledge.embeddings import LocalEmbedder, index_document
from app.knowledge.vector_store import MilvusVectorStore


def _document_scope(db: sqlite3.Connection, document_id: int) -> str:
    row = db.execute(
        "SELECT scope FROM knowledge_documents WHERE id = ?", (document_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"知识文件 #{document_id} 不存在")
    return str(row[0])


def delete_knowledge_document(
    db: sqlite3.Connection,
    actor: User,
    document_id: int,
    data_root: Path,
    store: MilvusVectorStore,
) -> None:
    if not has_permission(actor, "kb:write"):
        raise PermissionError("当前账号无权维护知识库")
    ensure_knowledge_schema(db)
    # 与后台任务的创建/重试共享写锁，避免删除与重新入库并发。
    from app.ingestion.jobs import ensure_document_idle, ensure_ingestion_schema
    ensure_ingestion_schema(db)
    db.commit()
    with db:
        db.execute("BEGIN IMMEDIATE")
        require_document_manager(db, actor, document_id)
        ensure_document_idle(db, document_id)
        _document_scope(db, document_id)
        ids = [int(row[0]) for row in db.execute(
            "SELECT id FROM knowledge_chunks WHERE document_id = ?", (document_id,))]
        store.delete(ids)
        db.execute(
            "DELETE FROM knowledge_documents WHERE id = ?", (document_id,)
        )
    for kind in ("files", "images"):
        directory = data_root / "knowledge" / kind / str(document_id)
        if directory.exists():
            try:
                shutil.rmtree(directory)
            except OSError as exc:
                raise RuntimeError(
                    f"知识文件 #{document_id} 的记录和向量已删除，"
                    f"但残留文件目录 {directory} 清理失败"
                ) from exc


def replace_knowledge_file(
    db: sqlite3.Connection,
    actor: User,
    document_id: int,
    path: Path,
    data_root: Path,
    embedder: LocalEmbedder,
    store: MilvusVectorStore,
) -> tuple[int, int]:
    if not has_permission(actor, "kb:write"):
        raise PermissionError("当前账号无权维护知识库")
    ensure_knowledge_schema(db)
    from app.ingestion.jobs import JobConflictError, ensure_document_idle, ensure_ingestion_schema
    ensure_ingestion_schema(db)
    db.commit()
    require_document_manager(db, actor, document_id)
    ensure_document_idle(db, document_id)
    scope = _document_scope(db, document_id)
    policy = AccessPolicy.model_validate_json(db.execute("SELECT access_policy FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()[0])
    new_id = add_knowledge_file(db, actor, path, scope, data_root, policy)
    try:
        product = db.execute("SELECT company,code,name FROM product_documents WHERE document_id=?", (document_id,)).fetchone()
        if product:
            with db:
                register_product(db, actor, new_id, ProductMetadata(company=product[0], code=product[1], name=product[2]))
        count = index_document(db, new_id, embedder, store)
    except Exception:
        try:
            delete_knowledge_document(db, actor, new_id, data_root, store)
        except Exception as cleanup_error:
            raise RuntimeError(
                f"新版 #{new_id} 索引失败且清理未完成，请检查该文档"
            ) from cleanup_error
        raise
    try:
        delete_knowledge_document(db, actor, document_id, data_root, store)
    except JobConflictError as exc:
        # 新版索引期间旧版可能被另一个请求安排重建；撤销本次新版。
        try:
            delete_knowledge_document(db, actor, new_id, data_root, store)
        except Exception as cleanup_error:
            raise RuntimeError(
                f"旧版 #{document_id} 出现并发建库任务，替换未完成；"
                f"新版 #{new_id} 清理失败，请检查并清理该文档"
            ) from cleanup_error
        raise JobConflictError(
            f"旧版 #{document_id} 正在建库，替换已取消，本次新版已清理"
        ) from exc
    except Exception as exc:
        raise RuntimeError(
            f"新版 #{new_id} 已导入并索引，但旧版 #{document_id} 清理未完成；"
            "请检查旧版记录、向量及文件"
        ) from exc
    return new_id, count
