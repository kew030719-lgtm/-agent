import re
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.knowledge.blocks import ParsedBlock, SourceLocation


def parse_word(path: Path) -> list[ParsedBlock]:
    document = Document(str(path))
    blocks: list[ParsedBlock] = []
    headings: list[str] = []
    paragraph_index = 0
    table_index = 0

    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            paragraph_index += 1
            text = item.text.strip()
            if not text:
                continue
            style_id = item.style.style_id if item.style else ""
            match = re.fullmatch(r"Heading([1-6])", style_id)
            if match:
                level = int(match.group(1))
                headings = headings[:level - 1] + [text]
                continue
            blocks.append(ParsedBlock(
                source_format="docx", order=len(blocks), kind="paragraph",
                text=text,
                location=SourceLocation(paragraph_index=paragraph_index),
                heading_path=tuple(headings), text_source="native",
            ))
        elif isinstance(item, Table):
            table_index += 1
            rows = [tuple(cell.text.strip() for cell in row.cells)
                    for row in item.rows]
            if not rows:
                continue
            if len(rows) > 1:
                headers = tuple(
                    value or f"列{column}"
                    for column, value in enumerate(rows[0], start=1)
                )
                data_rows = enumerate(rows[1:], start=2)
            else:
                headers = tuple(
                    f"列{column}" for column in range(1, len(rows[0]) + 1)
                )
                data_rows = enumerate(rows, start=1)
            for row_number, values in data_rows:
                content = "；".join(
                    f"{name}：{value}"
                    for name, value in zip(headers, values) if value
                )
                if content:
                    blocks.append(ParsedBlock(
                        source_format="docx", order=len(blocks),
                        kind="table_row", text=content,
                        location=SourceLocation(
                            table_index=table_index,
                            row_start=row_number, row_end=row_number,
                        ),
                        heading_path=tuple(headings), table_headers=headers,
                        text_source="native",
                    ))
    return blocks
