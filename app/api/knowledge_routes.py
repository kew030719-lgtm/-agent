import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse

from app.organization import JOB_LEVELS
from app.knowledge.access import AccessPolicy, can_manage_document, require_document_manager, validate_policy_targets, visible_document_ids, write_policy

from app.api.dependencies import Actor, Database
from app.api.schemas import KnowledgeFile
from app.auth import User, has_permission
from app.knowledge.documents import add_knowledge_file, get_visible_asset_path
from app.knowledge.catalog import ProductMetadata, register_product
from app.knowledge.embeddings import index_document
from app.knowledge.lifecycle import delete_knowledge_document


router = APIRouter(prefix="/knowledge", tags=["知识库"])


def require_writer(user: User) -> None:
    if not has_permission(user, "kb:write"):
        raise HTTPException(403, "当前账号无权维护知识库")


@router.get("", response_model=list[KnowledgeFile])
def list_files(user: Actor, db: Database, managed: bool = False) -> list[dict[str, object]]:
    visible = set(visible_document_ids(db, user))
    if managed:
        require_writer(user)
        ids = tuple(row[0] for row in db.execute("SELECT id FROM knowledge_documents WHERE created_by=?", (user["username"],)))
    else:
        ids = tuple(visible)
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        "SELECT d.id,d.title,d.scope,d.source_format,d.created_at,d.access_policy,COUNT(c.id) AS chunks "
        "FROM knowledge_documents d LEFT JOIN knowledge_chunks c ON c.document_id=d.id "
        f"WHERE d.id IN ({placeholders}) GROUP BY d.id ORDER BY d.id DESC", ids,
    ).fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        raw = item.pop("access_policy")
        try:
            mode = AccessPolicy.model_validate_json(raw).mode
        except ValueError:
            mode = "restricted"
        item.update(access_mode=mode, can_read=row["id"] in visible,
                    can_manage=can_manage_document(db, user, row["id"]))
        result.append(item)
    return result


@router.get("/access-options")
def access_options(user: Actor, db: Database) -> dict[str, object]:
    require_writer(user)
    return {
        "departments": [dict(row) for row in db.execute("SELECT id,name FROM departments ORDER BY id")],
        "job_levels": [{"value": value, "label": label} for value, label in JOB_LEVELS.items()],
        "users": [dict(row) for row in db.execute("SELECT username,display_name FROM users WHERE enabled=1 ORDER BY username")],
    }


@router.get("/{document_id}/access", response_model=AccessPolicy)
def get_access(document_id: int, user: Actor, db: Database) -> AccessPolicy:
    require_document_manager(db, user, document_id)
    return AccessPolicy.model_validate_json(db.execute("SELECT access_policy FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()[0])


@router.post("/{document_id}/access", response_model=AccessPolicy)
def update_access(document_id: int, body: AccessPolicy, user: Actor, db: Database) -> AccessPolicy:
    # 先取得 SQLite 写锁，校验与保存处于同一事务。
    with db:
        db.execute("BEGIN IMMEDIATE")
        write_policy(db, user, document_id, body)
    return body


@router.get("/{document_id}/access-audit")
def access_audit(document_id: int, user: Actor, db: Database) -> list[dict[str, object]]:
    require_document_manager(db, user, document_id)
    return [dict(row) for row in db.execute(
        "SELECT actor,old_policy,new_policy,created_at FROM knowledge_access_audit WHERE document_id=? ORDER BY id DESC LIMIT 100",
        (document_id,),
    )]


def require_reader(db: Database, user: User, document_id: int) -> None:
    if document_id not in visible_document_ids(db, user):
        raise HTTPException(404, "资料不存在或无权访问")


@router.get("/{document_id}/preview")
def preview(document_id: int, user: Actor, db: Database) -> dict[str, object]:
    require_reader(db, user, document_id)
    row = db.execute("SELECT title FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
    return {"id": document_id, "title": row[0],
            "chunks": [dict(row) for row in db.execute("SELECT id,text FROM knowledge_chunks WHERE document_id=? ORDER BY chunk_order", (document_id,))],
            "assets": [row[0] for row in db.execute("SELECT id FROM knowledge_assets WHERE document_id=? ORDER BY block_order", (document_id,))]}


@router.get("/{document_id}/download")
def download(document_id: int, request: Request, user: Actor, db: Database) -> FileResponse:
    require_reader(db, user, document_id)
    row = db.execute("SELECT source_path,source_name FROM knowledge_documents WHERE id=?", (document_id,)).fetchone()
    if not row[0]:
        raise HTTPException(404, "没有原文件")
    root = request.app.state.data_root
    path = (root / row[0]).resolve()
    if not path.is_relative_to((root / "knowledge" / "files" / str(document_id)).resolve()) or not path.is_file():
        raise HTTPException(404, "原文件不存在")
    return FileResponse(path, filename=row[1], media_type="application/octet-stream")


@router.get("/assets/{asset_id}")
def asset(asset_id: int, request: Request, user: Actor, db: Database) -> FileResponse:
    path = get_visible_asset_path(db, user, asset_id, request.app.state.data_root)
    if path is None:
        raise HTTPException(404, "图片不存在或无权访问")
    return FileResponse(path)


@router.get("/{document_id}/product", response_model=ProductMetadata | None)
def get_product(document_id: int, user: Actor, db: Database) -> dict[str, object] | None:
    require_document_manager(db, user, document_id)
    row = db.execute("SELECT company,code,name FROM product_documents WHERE document_id=?", (document_id,)).fetchone()
    return dict(row) if row else None


@router.post("/{document_id}/product", response_model=ProductMetadata)
def set_product(document_id: int, body: ProductMetadata, user: Actor, db: Database) -> ProductMetadata:
    with db:
        register_product(db, user, document_id, body)
    return body.model_copy(update={"code": body.code.upper()})


@router.post("", status_code=201)
def upload_file(
    request: Request, user: Actor, db: Database,
    file: Annotated[UploadFile, File()],
    scope: Annotated[Literal["general", "hr", "it", "finance"], Form()],
    access_policy: Annotated[str | None, Form()] = None,
    product_metadata: Annotated[str | None, Form()] = None,
) -> dict[str, int]:
    require_writer(user)
    policy = AccessPolicy.model_validate_json(access_policy) if access_policy is not None else AccessPolicy(mode="legacy")
    validate_policy_targets(db, policy)
    product = ProductMetadata.model_validate_json(product_metadata) if product_metadata else None
    filename = Path((file.filename or "").replace("\\", "/")).name
    if Path(filename).suffix.lower() not in {".pdf", ".md", ".docx", ".xlsx", ".xlsm", ".xls"}:
        raise HTTPException(400, "仅支持 PDF、MD、DOCX、XLSX、XLSM、XLS")
    maximum = int(os.getenv("API_UPLOAD_MAX_MB", "20")) * 1024 * 1024
    with TemporaryDirectory() as temporary:
        path = Path(temporary) / filename
        total = 0
        with path.open("wb") as target:
            while chunk := file.file.read(1024 * 1024):
                total += len(chunk)
                if total > maximum:
                    raise HTTPException(413, "文件超过上传大小限制")
                target.write(chunk)
        if not total:
            raise HTTPException(400, "不能上传空文件")
        service = request.app.state.assistant
        with service.lock:
            store = service.get_store()
            embedder = service.get_embedder()
            document_id = add_knowledge_file(db, user, path, scope, request.app.state.data_root, policy)
            if product is not None:
                with db:
                    register_product(db, user, document_id, product)
            try:
                count = index_document(db, document_id, embedder, store)
            except Exception as exc:
                raise HTTPException(503, f"文档 #{document_id} 已保存，但索引未完成；请点击重建索引") from exc
    return {"id": document_id, "chunks": count}


@router.post("/{document_id}/reindex")
def reindex(document_id: int, request: Request, user: Actor, db: Database) -> dict[str, int]:
    require_document_manager(db, user, document_id)
    if db.execute("SELECT id FROM knowledge_documents WHERE id=?", (document_id,)).fetchone() is None:
        raise HTTPException(404, "文档不存在")
    service = request.app.state.assistant
    with service.lock:
        count = index_document(db, document_id, service.get_embedder(), service.get_store())
    return {"id": document_id, "chunks": count}


@router.delete("/{document_id}", status_code=204)
def delete_file(document_id: int, request: Request, user: Actor, db: Database) -> None:
    require_document_manager(db, user, document_id)
    if db.execute("SELECT id FROM knowledge_documents WHERE id=?", (document_id,)).fetchone() is None:
        raise HTTPException(404, "文档不存在")
    service = request.app.state.assistant
    with service.lock:
        delete_knowledge_document(db, user, document_id, request.app.state.data_root, service.get_store())
