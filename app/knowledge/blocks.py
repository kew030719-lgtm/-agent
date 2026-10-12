from dataclasses import dataclass
from typing import Literal


SourceFormat = Literal["pdf", "md", "docx", "doc", "xlsx", "xlsm", "xls"]
BlockKind = Literal["paragraph", "table_row", "code", "image"]
TextSource = Literal["native", "ocr", "vision"]


@dataclass(frozen=True)
class SourceLocation:
    page: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    paragraph_index: int | None = None
    table_index: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    bbox: tuple[float, float, float, float] | None = None


@dataclass(frozen=True)
class ParsedBlock:
    source_format: SourceFormat
    order: int
    kind: BlockKind
    text: str
    location: SourceLocation
    heading_path: tuple[str, ...] = ()
    table_headers: tuple[str, ...] = ()
    text_source: TextSource | None = None
    image_ref: str | None = None