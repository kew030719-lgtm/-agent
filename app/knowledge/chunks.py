from dataclasses import dataclass, replace
from typing import Iterable

from app.knowledge.blocks import (
    BlockKind,
    ParsedBlock,
    SourceFormat,
    SourceLocation,
    TextSource,
)


@dataclass(frozen=True)
class KnowledgeChunk:
    order: int
    text: str
    source_format: SourceFormat
    kind: BlockKind
    heading_path: tuple[str, ...]
    locations: tuple[SourceLocation, ...]
    image_refs: tuple[str, ...]
    text_source: TextSource | None


def _group_key(block: ParsedBlock) -> tuple[object, ...]:
    location = block.location
    return (
        block.source_format,
        block.kind,
        block.heading_path,
        location.page,
        location.sheet_name,
        location.table_index,
        block.text_source,
    )


def _split_long_text(text: str, limit: int, overlap: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + limit, len(text))
        if end < len(text):
            candidates = [
                text.rfind(mark, start + limit // 2, end)
                for mark in ("\n", "。", "；", ". ", "; ")
            ]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1
        piece = text[start:end].strip()
        if piece:
            parts.append(piece)
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return parts


def _make_chunk(blocks: list[ParsedBlock], order: int) -> KnowledgeChunk:
    first = blocks[0]
    heading = " > ".join(first.heading_path)
    prefix = f"标题：{heading}\n" if heading else ""
    return KnowledgeChunk(
        order=order,
        text=prefix + "\n".join(block.text for block in blocks),
        source_format=first.source_format,
        kind=first.kind,
        heading_path=first.heading_path,
        locations=tuple(block.location for block in blocks),
        image_refs=tuple(dict.fromkeys(
            block.image_ref for block in blocks if block.image_ref
        )),
        text_source=first.text_source,
    )


def chunk_blocks(
    blocks: Iterable[ParsedBlock],
    max_chars: int = 320,
    overlap: int = 40,
) -> list[KnowledgeChunk]:
    if max_chars < 100 or overlap < 0 or overlap >= max_chars // 2:
        raise ValueError("切片大小或重叠长度无效")

    chunks: list[KnowledgeChunk] = []
    current: list[ParsedBlock] = []
    current_size = 0
    current_key: tuple[object, ...] | None = None

    for block in sorted(blocks, key=lambda item: item.order):
        if block.kind == "image" or not block.text.strip():
            continue
        heading_size = len(" > ".join(block.heading_path)) + 4
        body_limit = max(60, max_chars - heading_size)
        pieces = (
            [block.text]
            if block.kind == "table_row"
            else _split_long_text(block.text, body_limit, overlap)
        )
        for piece in pieces:
            part = replace(block, text=piece)
            key = _group_key(part)
            if current and (
                key != current_key
                or current_size + len(piece) + 1 > body_limit
            ):
                chunks.append(_make_chunk(current, len(chunks)))
                current = []
                current_size = 0
            current.append(part)
            current_size += len(piece) + 1
            current_key = key

    if current:
        chunks.append(_make_chunk(current, len(chunks)))
    return chunks
