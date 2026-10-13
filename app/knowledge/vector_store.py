"""Milvus vector storage. SQLite remains the source of document permissions."""

import os
import re
from collections.abc import Iterable
from typing import TypedDict

from pymilvus import MilvusClient


VECTOR_DIMENSION = 512
DEFAULT_COLLECTION = "employee_knowledge_bge_small_zh_v15"


class VectorRecord(TypedDict):
    id: int
    document_id: int
    scope: str
    vector: list[float]


class VectorMatch(TypedDict):
    id: int
    score: float


class MilvusVectorStore:
    def __init__(self) -> None:
        uri = os.getenv("MILVUS_URI", "http://127.0.0.1:19530")
        token = os.getenv("MILVUS_TOKEN")
        collection = os.getenv("MILVUS_COLLECTION", DEFAULT_COLLECTION)
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,254}", collection):
            raise ValueError("Milvus 集合名称无效")
        self.collection = collection
        self.client = MilvusClient(
            uri=uri, token=token or "",
            timeout=float(os.getenv("MILVUS_TIMEOUT_SECONDS", "10")),
            # 避免客户端空闲时每 10 秒 ping，触发服务器 too_many_pings 断连。
            grpc_options={"grpc.keepalive_time_ms": 300000, "grpc.keepalive_permit_without_calls": False},
        )
        if not self.client.has_collection(collection_name=collection):
            self.client.create_collection(
                collection_name=collection,
                dimension=VECTOR_DIMENSION,
                metric_type="COSINE",
                consistency_level="Strong",
            )

    def __enter__(self) -> "MilvusVectorStore":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        self.client.close()

    def existing_scopes(self, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        rows = self.client.get(
            collection_name=self.collection,
            ids=ids,
            output_fields=["id", "scope"],
        )
        return {int(row["id"]): str(row["scope"]) for row in rows}

    def upsert(self, records: list[VectorRecord]) -> int:
        if not records:
            return 0
        self.client.upsert(collection_name=self.collection, data=records)
        return len(records)

    def delete(self, ids: list[int]) -> None:
        for start in range(0, len(ids), 256):
            self.client.delete(
                collection_name=self.collection,
                ids=ids[start:start + 256],
            )

    def search(
        self,
        vector: Iterable[float],
        scopes: tuple[str, ...],
        limit: int,
        document_ids: tuple[int, ...] | None = None,
    ) -> list[VectorMatch]:
        if not scopes or document_ids == () or limit < 1:
            return []
        allowed = {"general", "hr", "it", "finance"}
        if not set(scopes) <= allowed:
            raise ValueError("Milvus 检索范围无效")
        expression = "scope in [" + ", ".join(f'"{scope}"' for scope in scopes) + "]"
        if document_ids:
            if any(type(value) is not int or value < 1 for value in document_ids):
                raise ValueError("文档编号无效")
            expression += " and document_id in [" + ",".join(map(str, document_ids)) + "]"
        results = self.client.search(
            collection_name=self.collection,
            data=[list(vector)],
            filter=expression,
            limit=limit,
            output_fields=["id"],
            consistency_level="Strong",
        )
        return [
            {"id": int(hit["id"]), "score": float(hit["distance"])}
            for hit in results[0]
        ]
