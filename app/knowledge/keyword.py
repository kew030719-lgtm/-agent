import re
import sqlite3


def _query_terms(question: str) -> list[str]:
    terms: list[str] = []
    for match in re.finditer(r"[\u4e00-\u9fff]{3,}|[A-Za-z0-9_]+(?:[.\-/][A-Za-z0-9_]+)*", question):
        word = match.group().lower()
        if "\u4e00" <= word[0] <= "\u9fff":
            terms.extend(word[index:index + 3] for index in range(len(word) - 2))
        elif len(word) >= 3:
            terms.append(word)
    return list(dict.fromkeys(terms))[:16]


def search_keywords(
    db: sqlite3.Connection,
    question: str,
    scopes: tuple[str, ...],
    limit: int,
    document_ids: tuple[int, ...] = (),
) -> list[int]:
    terms = _query_terms(question)
    if not terms or not scopes or limit < 1:
        return []
    expression = " OR ".join(f'"{term}"' for term in terms)
    marks = ", ".join("?" for _ in scopes)
    document_filter = ""
    if document_ids:
        document_filter = " AND d.id IN (" + ",".join("?" for _ in document_ids) + ")"
    rows = db.execute(
        "SELECT c.id FROM knowledge_fts "
        "JOIN knowledge_chunks AS c ON c.id = knowledge_fts.rowid "
        "JOIN knowledge_documents AS d ON d.id = c.document_id "
        f"WHERE knowledge_fts MATCH ? AND d.scope IN ({marks}){document_filter} "
        "ORDER BY bm25(knowledge_fts) LIMIT ?",
        (expression, *scopes, *document_ids, limit),
    ).fetchall()
    return [int(row[0]) for row in rows]
