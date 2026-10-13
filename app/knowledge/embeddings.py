import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

import numpy as np
from fastembed import TextEmbedding

from app.knowledge.vector_store import MilvusVectorStore, VectorRecord, VECTOR_DIMENSION


MODEL_NAME = "BAAI/bge-small-zh-v1.5"
MAX_MODEL_TOKENS = 500


class LocalEmbedder:
    def __init__(self, data_root: Path, download: bool = False) -> None:
        model_root = data_root / "knowledge" / "models"
        hub_root = data_root / "knowledge" / "hf_cache"
        model_root.mkdir(parents=True, exist_ok=True)
        hub_root.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(hub_root.resolve())
        os.environ["HF_HUB_DISABLE_XET"] = "1"
        self.model = TextEmbedding(
            model_name=MODEL_NAME,
            cache_dir=str(model_root),
            local_files_only=not download,
        )

    def embed(self, texts: list[str]) -> list[bytes]:
        for text in texts:
            if self.model.token_count(text) > MAX_MODEL_TOKENS:
                raise ValueError("知识切片超过模型长度，请缩小切片后重新导入")
        vectors = list(self.model.embed(texts))
        return [np.asarray(vector, dtype="<f4").tobytes() for vector in vectors]

    def embed_query(self, question: str) -> np.ndarray:
        if self.model.token_count(question) > MAX_MODEL_TOKENS:
            raise ValueError("问题过长，请缩短后重试")
        vector = next(iter(self.model.embed([question])))
        return np.asarray(vector, dtype=np.float32)


def index_document(
    db: sqlite3.Connection,
    document_id: int,
    embedder: LocalEmbedder,
    store: MilvusVectorStore,
    *,
    progress: Callable[[str, int, int], None] | None = None,
) -> int:
    rows = db.execute(
        "SELECT c.id, c.text, d.scope, c.embedding, c.embedding_model "
        "FROM knowledge_chunks AS c "
        "JOIN knowledge_documents AS d ON d.id = c.document_id "
        "WHERE d.id = ? ORDER BY c.chunk_order",
        (document_id,),
    ).fetchall()
    count = 0
    total = len(rows)
    if progress is not None:
        progress("embedding", 0, total)
    for start in range(0, len(rows), 32):
        batch = rows[start:start + 32]
        existing = store.existing_scopes([row[0] for row in batch])
        pending = [row for row in batch if existing.get(row[0]) != row[2]]
        if not pending:
            if progress is not None:
                progress("indexing", start + len(batch), total)
            continue
        vectors: dict[int, np.ndarray] = {}
        fresh = []
        for chunk_id, text, _scope, legacy_blob, legacy_model in pending:
            if legacy_blob is not None and legacy_model == MODEL_NAME:
                vector = np.frombuffer(legacy_blob, dtype="<f4")
                if vector.size == VECTOR_DIMENSION:
                    vectors[chunk_id] = vector
                    continue
            fresh.append((chunk_id, text))
        if progress is not None:
            progress("embedding", start, total)
        for (chunk_id, _text), blob in zip(
            fresh, embedder.embed([text for _chunk_id, text in fresh])
        ):
            vectors[chunk_id] = np.frombuffer(blob, dtype="<f4")
        records: list[VectorRecord] = [
            {
                "id": row[0], "document_id": document_id,
                "scope": row[2], "vector": vectors[row[0]].tolist(),
            }
            for row in pending
        ]
        if progress is not None:
            progress("indexing", start, total)
        count += store.upsert(records)
        if progress is not None:
            progress("indexing", start + len(batch), total)
    return count


def index_missing(
    db: sqlite3.Connection,
    embedder: LocalEmbedder,
    store: MilvusVectorStore,
) -> int:
    rows = db.execute(
        "SELECT DISTINCT document_id FROM knowledge_chunks ORDER BY document_id"
    ).fetchall()
    return sum(index_document(db, row[0], embedder, store) for row in rows)
