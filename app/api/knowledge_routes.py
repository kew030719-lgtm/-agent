import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from app.api.dependencies import Actor, Database
from app.api.schemas import KnowledgeFile
from app.auth import User, has_permission
from app.knowledge.documents import SCOPES, add_knowledge_file
from app.knowledge.embeddings import index_document
from app.knowledge.lifecycle import delete_knowledge_document


router = APIRouter(prefix="/knowledge", tags=["知识库"])


def require_writer(user: User) -> None:
    if not has_permission(user, "kb:write"):
        raise HTTPException(403, "当前账号无权维护知识库")


@router.get("", response_model=list[KnowledgeFile])
def list_files(user: Actor, db: Database) -> list[dict[str, object]]:
    scopes = [scope for scope in SCOPES if has_permission(user, f"kb:{scope}")]
    if not scopes:
        return []
    placeholders = ",".join("?" for _ in scopes)
    rows = db.execute(
        "SELECT d.id,d.title,d.scope,d.source_format,d.created_at,COUNT(c.id) AS chunks "
        "FROM knowledge_documents d LEFT JOIN knowledge_chunks c ON c.document_id=d.id "
        f"WHERE d.scope IN ({placeholders}) GROUP BY d.id ORDER BY d.id DESC", scopes,
    ).fetchall()
    return [dict(row) for row in rows]


@router.post("", status_code=201)
def upload_file(
    request: Request, user: Actor, db: Database,
    file: Annotated[UploadFile, File()],
    scope: Annotated[Literal["general", "hr", "it", "finance"], Form()],
) -> dict[str, int]:
    require_writer(user)
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
            document_id = add_knowledge_file(db, user, path, scope, request.app.state.data_root)
            try:
                count = index_document(db, document_id, embedder, store)
            except Exception as exc:
                raise HTTPException(503, f"文档 #{document_id} 已保存，但索引未完成；请点击重建索引") from exc
    return {"id": document_id, "chunks": count}


@router.post("/{document_id}/reindex")
def reindex(document_id: int, request: Request, user: Actor, db: Database) -> dict[str, int]:
    require_writer(user)
    if db.execute("SELECT id FROM knowledge_documents WHERE id=?", (document_id,)).fetchone() is None:
        raise HTTPException(404, "文档不存在")
    service = request.app.state.assistant
    with service.lock:
        count = index_document(db, document_id, service.get_embedder(), service.get_store())
    return {"id": document_id, "chunks": count}


@router.delete("/{document_id}", status_code=204)
def delete_file(document_id: int, request: Request, user: Actor, db: Database) -> None:
    require_writer(user)
    if db.execute("SELECT id FROM knowledge_documents WHERE id=?", (document_id,)).fetchone() is None:
        raise HTTPException(404, "文档不存在")
    service = request.app.state.assistant
    with service.lock:
        delete_knowledge_document(db, user, document_id, request.app.state.data_root, service.get_store())
