import sqlite3
import re
import os
from typing import NotRequired, TypedDict

from app.auth import User
from app.knowledge.access import visible_document_ids
from app.knowledge.documents import SCOPES, KnowledgeSourceRecord, get_visible_chunks_by_ids
from app.knowledge.embeddings import LocalEmbedder
from app.knowledge.keyword import search_keywords
from app.knowledge.vector_store import MilvusVectorStore


class SearchHit(TypedDict):
    chunk_id: int
    document_id: int
    title: str
    text: str
    score: float
    heading_path: tuple[str, ...]
    locations: list[dict[str, object]]
    image_refs: tuple[str, ...]
    fusion_score: NotRequired[float]
    context_neighbor: NotRequired[bool]


def candidate_limit() -> int:
    value = int(os.getenv("RAG_CANDIDATE_LIMIT", "36"))
    if not 4 <= value <= 100:
        raise ValueError("RAG_CANDIDATE_LIMIT 必须在4至100之间")
    return value


def reciprocal_rank_fusion(rankings: list[list[int]], k: int = 60) -> dict[int, float]:
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(dict.fromkeys(ranking), start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


def _hit(row: KnowledgeSourceRecord, score: float = 0.0) -> SearchHit:
    return {
        "chunk_id": row["id"], "document_id": row["document_id"], "title": row["title"],
        "text": row["text"], "score": score, "heading_path": row["heading_path"],
        "locations": row["locations"], "image_refs": row["image_refs"],
    }


def mentioned_documents(db: sqlite3.Connection, question: str) -> tuple[int, ...]:
    """识别完整标题或标题中的明确编号名称，防止产品44与产品42混淆。

    此处只确定目标，不返回正文；实际检索仍同时执行 scope 和 SQLite 权限检查。
    即使点名的是无权文档，也不能回退成其他相似文档。
    """
    matched: list[int] = []
    for document_id, title in db.execute("SELECT id,title FROM knowledge_documents"):
        names = [title] if len(title) >= 3 else []
        names.extend(re.findall(r"[A-Za-z\u4e00-\u9fff]+[-_]?\d+(?:[._-]\d+)*", title))
        if any(re.search(re.escape(name) + r"(?![A-Za-z0-9])", question, re.IGNORECASE) for name in names):
            matched.append(document_id)
    return tuple(matched)


def search_knowledge(
    db: sqlite3.Connection,
    user: User,
    question: str,
    embedder: LocalEmbedder,
    store: MilvusVectorStore,
    limit: int = 4,
    queries: list[str] | None = None,
) -> list[SearchHit]:
    if limit < 1:
        raise ValueError("检索数量必须大于 0")
    scopes = SCOPES  # 自定义规则由文档 ID 授权，不再叠加旧角色范围。
    allowed_ids = visible_document_ids(db, user)
    mentioned = mentioned_documents(db, question)
    document_ids = tuple(i for i in allowed_ids if not mentioned or i in mentioned)
    if not document_ids:
        return []
    variants = list(dict.fromkeys([question, *(queries or [])]))[:2]
    rankings: list[list[int]] = []
    cosine_scores: dict[int, float] = {}
    for variant in variants:
        query = embedder.embed_query(variant)
        matches = store.search(query.tolist(), scopes, max(36, limit), document_ids=document_ids)
        rankings.append([match["id"] for match in matches])
        rankings.append(search_keywords(db, variant, scopes, max(36, limit), document_ids=document_ids))
        for match in matches:
            cosine_scores[match["id"]] = max(cosine_scores.get(match["id"], -1.0), match["score"])
    fused = reciprocal_rank_fusion(rankings)
    candidate_ids = list(fused)
    if not candidate_ids:
        return []
    visible = get_visible_chunks_by_ids(db, user, candidate_ids)
    if document_ids:
        visible = {key: value for key, value in visible.items() if value["document_id"] in document_ids}
    hits: list[SearchHit] = []
    for chunk_id in sorted(visible, key=lambda key: (-fused[key], key)):
        hit = _hit(visible[chunk_id], cosine_scores.get(chunk_id, 0.0))
        hit["fusion_score"] = fused[chunk_id]
        hits.append(hit)
        if len(hits) == limit:
            break
    return hits


def with_neighbor_context(db: sqlite3.Connection, user: User, hits: list[SearchHit]) -> list[SearchHit]:
    """短片段最多补一个同章节/同页相邻片段，独立引用，仍受四片段总预算约束。"""
    if os.getenv("RAG_NEIGHBOR_CONTEXT_ENABLED", "true").lower() not in {"true", "1", "yes"}:
        return hits
    primary_ids = {hit["chunk_id"] for hit in hits[:4]}
    for hit in hits[:3]:
        if len(hit["text"]) >= 120 and not hit["text"].rstrip().endswith(("：", ":")):
            continue
        rows = db.execute(
            "SELECT n.id FROM knowledge_chunks c JOIN knowledge_chunks n "
            "ON n.document_id=c.document_id AND abs(n.chunk_order-c.chunk_order)=1 "
            "WHERE c.id=? ORDER BY n.chunk_order DESC", (hit["chunk_id"],),
        ).fetchall()
        visible = get_visible_chunks_by_ids(db, user, [row[0] for row in rows])
        for row in rows:
            if row[0] in primary_ids or row[0] not in visible:
                continue
            neighbor = _hit(visible[row[0]])
            same_heading = bool(hit["heading_path"]) and hit["heading_path"] == neighbor["heading_path"]
            location = hit["locations"][0] if hit["locations"] else {}
            adjacent_location = neighbor["locations"][0] if neighbor["locations"] else {}
            same_page = not hit["heading_path"] and not neighbor["heading_path"] and location.get("page") is not None and location.get("page") == adjacent_location.get("page")
            if same_heading or same_page:
                neighbor["context_neighbor"] = True
                return [*hits[:3], neighbor, *hits[3:]]
    return hits
