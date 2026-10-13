import hashlib
import json
import shutil
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import TypedDict

from app.auth import User, has_permission
from app.knowledge.access import AccessPolicy, ensure_access_schema, visible_document_ids, write_policy
from app.knowledge.catalog import ensure_catalog_schema
from app.knowledge.blocks import ParsedBlock, SourceLocation
from app.knowledge.chunks import KnowledgeChunk, chunk_blocks
from app.knowledge.parsers.dispatch import parse_file


SCOPES = ("general", "hr", "it", "finance")


class KnowledgeDocument(TypedDict):
    id: int
    title: str
    content: str
    scope: str
    created_by: str
    created_at: str


class KnowledgeChunkRecord(TypedDict):
    id: int
    document_id: int
    title: str
    text: str
    scope: str
    heading_path: tuple[str, ...]
    locations: list[dict[str, object]]
    image_refs: tuple[str, ...]
    embedding: bytes | None
    embedding_model: str | None


class KnowledgeSourceRecord(TypedDict):
    id: int
    document_id: int
    title: str
    text: str
    scope: str
    heading_path: tuple[str, ...]
    locations: list[dict[str, object]]
    image_refs: tuple[str, ...]


class KnowledgeAssetRecord(TypedDict):
    id: int
    document_id: int
    title: str
    scope: str
    image_ref: str
    location: dict[str, object]


def ensure_knowledge_schema(db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            scope TEXT NOT NULL
                CHECK (scope IN ('general', 'hr', 'it', 'finance')),
            created_by TEXT NOT NULL REFERENCES users(username),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)
    columns = {
        row[1] for row in db.execute("PRAGMA table_info(knowledge_documents)")
    }
    for name in ("source_name", "source_format", "source_path", "source_hash"):
        if name not in columns:
            db.execute(f"ALTER TABLE knowledge_documents ADD COLUMN {name} TEXT")
    if "index_status" not in columns:
        # 旧同步导入保持原可见性；后台导入必须显式使用 pending。
        db.execute("ALTER TABLE knowledge_documents ADD COLUMN index_status TEXT NOT NULL DEFAULT 'ready' CHECK(index_status IN ('pending','processing','failed','ready'))")
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_chunks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL
                REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            chunk_order INTEGER NOT NULL,
            text TEXT NOT NULL,
            source_format TEXT NOT NULL,
            kind TEXT NOT NULL,
            heading_path_json TEXT NOT NULL,
            locations_json TEXT NOT NULL,
            image_refs_json TEXT NOT NULL,
            text_source TEXT,
            embedding BLOB,
            embedding_model TEXT,
            UNIQUE(document_id, chunk_order)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_assets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document_id INTEGER NOT NULL
                REFERENCES knowledge_documents(id) ON DELETE CASCADE,
            block_order INTEGER NOT NULL,
            image_ref TEXT NOT NULL,
            location_json TEXT NOT NULL,
            UNIQUE(document_id, block_order)
        )
    """)
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_scope "
        "ON knowledge_documents(scope)"
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_chunk_document "
        "ON knowledge_chunks(document_id)"
    )
    has_keyword_index = db.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'knowledge_fts'"
    ).fetchone() is not None
    db.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(
            text, content='knowledge_chunks', content_rowid='id',
            tokenize='trigram'
        )
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS knowledge_fts_insert
        AFTER INSERT ON knowledge_chunks BEGIN
            INSERT INTO knowledge_fts(rowid, text) VALUES (new.id, new.text);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS knowledge_fts_delete
        AFTER DELETE ON knowledge_chunks BEGIN
            INSERT INTO knowledge_fts(knowledge_fts, rowid, text)
            VALUES ('delete', old.id, old.text);
        END
    """)
    db.execute("""
        CREATE TRIGGER IF NOT EXISTS knowledge_fts_update
        AFTER UPDATE OF text ON knowledge_chunks BEGIN
            INSERT INTO knowledge_fts(knowledge_fts, rowid, text)
            VALUES ('delete', old.id, old.text);
            INSERT INTO knowledge_fts(rowid, text) VALUES (new.id, new.text);
        END
    """)
    old_documents = db.execute(
        "SELECT d.id, d.content FROM knowledge_documents AS d "
        "WHERE d.content <> '' AND NOT EXISTS ("
        "SELECT 1 FROM knowledge_chunks AS c WHERE c.document_id = d.id)"
    ).fetchall()
    for document_id, content in old_documents:
        blocks = [ParsedBlock(
            source_format="md", order=0, kind="paragraph", text=content,
            location=SourceLocation(), text_source="native",
        )]
        _insert_chunks(db, document_id, chunk_blocks(blocks))
    if not has_keyword_index:
        db.execute("INSERT INTO knowledge_fts(knowledge_fts) VALUES ('rebuild')")
    ensure_access_schema(db)
    ensure_catalog_schema(db)
    db.commit()


def _insert_chunks(
    db: sqlite3.Connection,
    document_id: int,
    chunks: list[KnowledgeChunk],
) -> None:
    db.executemany(
        "INSERT INTO knowledge_chunks("
        "document_id, chunk_order, text, source_format, kind, "
        "heading_path_json, locations_json, image_refs_json, text_source"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                document_id, chunk.order, chunk.text,
                chunk.source_format, chunk.kind,
                json.dumps(chunk.heading_path, ensure_ascii=False),
                json.dumps(
                    [asdict(location) for location in chunk.locations],
                    ensure_ascii=False,
                ),
                json.dumps(chunk.image_refs, ensure_ascii=False),
                chunk.text_source,
            )
            for chunk in chunks
        ],
    )


def _insert_assets(
    db: sqlite3.Connection,
    document_id: int,
    blocks: list[ParsedBlock],
) -> None:
    db.executemany(
        "INSERT INTO knowledge_assets("
        "document_id, block_order, image_ref, location_json"
        ") VALUES (?, ?, ?, ?)",
        [
            (
                document_id, block.order, block.image_ref,
                json.dumps(asdict(block.location), ensure_ascii=False),
            )
            for block in blocks
            if block.kind == "image" and block.image_ref
        ],
    )


def add_knowledge_document(
    db: sqlite3.Connection,
    actor: User,
    title: str,
    content: str,
    scope: str,
    policy: AccessPolicy | None = None,
) -> int:
    if not has_permission(actor, "kb:write"):
        raise PermissionError("当前账号无权维护知识库")

    title = title.strip()
    content = content.strip()
    if not title or not content:
        raise ValueError("文档标题和内容不能为空")
    if scope not in SCOPES:
        raise ValueError("文档范围必须是 general/hr/it/finance")

    ensure_knowledge_schema(db)
    with db:
        cursor = db.execute(
            "INSERT INTO knowledge_documents(title, content, scope, created_by) "
            "VALUES (?, ?, ?, ?)",
            (title, content, scope, actor["username"]),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("知识文档创建失败")
        document_id = cursor.lastrowid
        blocks = [ParsedBlock(
            source_format="md", order=0, kind="paragraph", text=content,
            location=SourceLocation(), text_source="native",
        )]
        _insert_chunks(db, document_id, chunk_blocks(blocks))
        if policy is not None:
            write_policy(db, actor, document_id, policy)
    return document_id


def add_knowledge_file(
    db: sqlite3.Connection,
    actor: User,
    path: Path,
    scope: str,
    data_root: Path,
    policy: AccessPolicy | None = None,
) -> int:
    if not has_permission(actor, "kb:write"):
        raise PermissionError("当前账号无权维护知识库")
    if scope not in SCOPES:
        raise ValueError("文档范围必须是 general/hr/it/finance")
    if not path.is_file():
        raise ValueError("知识文件不存在")
    suffix = path.suffix.lower()
    if suffix not in {".pdf", ".md", ".docx", ".xlsx", ".xlsm", ".xls"}:
        raise ValueError("仅支持 PDF、Markdown、DOCX、XLSX、XLSM、XLS；旧 DOC 请先转换")

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while part := source.read(1024 * 1024):
            digest.update(part)

    ensure_knowledge_schema(db)
    stored_path: Path | None = None
    document_id: int | None = None
    try:
        with db:
            cursor = db.execute(
                "INSERT INTO knowledge_documents("
                "title, content, scope, created_by, source_name, "
                "source_format, source_hash) VALUES (?, '', ?, ?, ?, ?, ?)",
                (path.stem, scope, actor["username"], path.name,
                 suffix[1:], digest.hexdigest()),
            )
            if cursor.lastrowid is None:
                raise RuntimeError("知识文件创建失败")
            document_id = cursor.lastrowid
            if policy is not None:
                write_policy(db, actor, document_id, policy)
            stored_path = (
                data_root / "knowledge" / "files" / str(document_id)
                / f"source{suffix}"
            )
            stored_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            shutil.copyfile(path, stored_path)
            stored_path.chmod(0o600)
            blocks = parse_file(stored_path, data_root, document_id)
            chunks = chunk_blocks(blocks)
            if not chunks and not any(
                block.kind == "image" and block.image_ref for block in blocks
            ):
                raise ValueError("文件没有可检索的文字或图片内容")
            _insert_assets(db, document_id, blocks)
            _insert_chunks(db, document_id, chunks)
            db.execute(
                "UPDATE knowledge_documents SET source_path = ? WHERE id = ?",
                (stored_path.relative_to(data_root).as_posix(), document_id),
            )
    except Exception:
        if stored_path is not None:
            shutil.rmtree(stored_path.parent, ignore_errors=True)
        if document_id is not None:
            shutil.rmtree(
                data_root / "knowledge" / "images" / str(document_id),
                ignore_errors=True,
            )
        raise
    if document_id is None:
        raise RuntimeError("知识文件创建失败")
    return document_id


def list_visible_documents(
    db: sqlite3.Connection,
    user: User,
) -> list[KnowledgeDocument]:
    allowed_ids = visible_document_ids(db, user)
    if not allowed_ids:
        return []

    placeholders = ", ".join("?" for _ in allowed_ids)
    rows = db.execute(
        "SELECT id, title, content, scope, created_by, created_at "
        f"FROM knowledge_documents WHERE id IN ({placeholders}) "
        "ORDER BY id",
        allowed_ids,
    ).fetchall()

    return [
        {
            "id": row[0],
            "title": row[1],
            "content": row[2],
            "scope": row[3],
            "created_by": row[4],
            "created_at": row[5],
        }
        for row in rows
    ]


def list_visible_chunks(
    db: sqlite3.Connection,
    user: User,
) -> list[KnowledgeChunkRecord]:
    allowed_ids = visible_document_ids(db, user)
    if not allowed_ids:
        return []
    placeholders = ", ".join("?" for _ in allowed_ids)
    rows = db.execute(
        "SELECT c.id, d.id, d.title, c.text, d.scope, "
        "c.heading_path_json, c.locations_json, c.image_refs_json, "
        "c.embedding, c.embedding_model "
        "FROM knowledge_chunks AS c "
        "JOIN knowledge_documents AS d ON d.id = c.document_id "
        f"WHERE d.id IN ({placeholders}) "
        "ORDER BY d.id, c.chunk_order",
        allowed_ids,
    ).fetchall()
    return [
        {
            "id": row[0], "document_id": row[1], "title": row[2],
            "text": row[3], "scope": row[4],
            "heading_path": tuple(json.loads(row[5])),
            "locations": json.loads(row[6]),
            "image_refs": tuple(json.loads(row[7])),
            "embedding": row[8], "embedding_model": row[9],
        }
        for row in rows
    ]


def get_visible_chunks_by_ids(
    db: sqlite3.Connection,
    user: User,
    chunk_ids: list[int],
) -> dict[int, KnowledgeSourceRecord]:
    allowed_ids = visible_document_ids(db, user)
    if not allowed_ids or not chunk_ids:
        return {}
    scope_marks = ", ".join("?" for _ in allowed_ids)
    id_marks = ", ".join("?" for _ in chunk_ids)
    rows = db.execute(
        "SELECT c.id, d.id, d.title, c.text, d.scope, "
        "c.heading_path_json, c.locations_json, c.image_refs_json "
        "FROM knowledge_chunks AS c "
        "JOIN knowledge_documents AS d ON d.id = c.document_id "
        f"WHERE d.id IN ({scope_marks}) AND c.id IN ({id_marks})",
        (*allowed_ids, *chunk_ids),
    ).fetchall()
    return {
        row[0]: {
            "id": row[0], "document_id": row[1], "title": row[2],
            "text": row[3], "scope": row[4],
            "heading_path": tuple(json.loads(row[5])),
            "locations": json.loads(row[6]),
            "image_refs": tuple(json.loads(row[7])),
        }
        for row in rows
    }


def list_visible_assets(
    db: sqlite3.Connection,
    user: User,
) -> list[KnowledgeAssetRecord]:
    allowed_ids = visible_document_ids(db, user)
    if not allowed_ids:
        return []
    placeholders = ", ".join("?" for _ in allowed_ids)
    rows = db.execute(
        "SELECT a.id, d.id, d.title, d.scope, "
        "a.image_ref, a.location_json "
        "FROM knowledge_assets AS a "
        "JOIN knowledge_documents AS d ON d.id = a.document_id "
        f"WHERE d.id IN ({placeholders}) "
        "ORDER BY d.id, a.block_order",
        allowed_ids,
    ).fetchall()
    return [
        {
            "id": row[0], "document_id": row[1], "title": row[2],
            "scope": row[3], "image_ref": row[4],
            "location": json.loads(row[5]),
        }
        for row in rows
    ]


def get_visible_asset_path(
    db: sqlite3.Connection,
    user: User,
    asset_id: int,
    data_root: Path,
) -> Path | None:
    if asset_id < 1:
        return None
    allowed_ids = visible_document_ids(db, user)
    if not allowed_ids:
        return None
    placeholders = ", ".join("?" for _ in allowed_ids)
    row = db.execute(
        "SELECT a.image_ref, d.id FROM knowledge_assets AS a "
        "JOIN knowledge_documents AS d ON d.id = a.document_id "
        f"WHERE a.id = ? AND d.id IN ({placeholders})",
        (asset_id, *allowed_ids),
    ).fetchone()
    if row is None:
        return None
    image_dir = (data_root / "knowledge" / "images" / str(row[1])).resolve()
    image_path = (data_root / row[0]).resolve()
    if not image_path.is_relative_to(image_dir) or not image_path.is_file():
        return None
    return image_path
