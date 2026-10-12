from pathlib import Path

from markdown_it import MarkdownIt

from app.knowledge.blocks import ParsedBlock, SourceLocation


def parse_markdown(path: Path) -> list[ParsedBlock]:
    source = path.read_text(encoding="utf-8-sig")
    source_lines = source.splitlines()
    tokens = MarkdownIt("js-default").parse(source)
    blocks: list[ParsedBlock] = []
    headings: list[str] = []
    in_table = False
    in_header = False
    table_index = 0
    headers: tuple[str, ...] = ()
    cells: list[str] = []
    row_line = 0

    for index, token in enumerate(tokens):
        if token.type == "heading_open":
            level = int(token.tag[1])
            title = tokens[index + 1].content.strip()
            headings = headings[:level - 1] + [title]
        elif token.type == "table_open":
            in_table = True
            table_index += 1
            headers = ()
        elif token.type == "table_close":
            in_table = False
        elif token.type == "thead_open":
            in_header = True
        elif token.type == "thead_close":
            in_header = False
        elif token.type == "tr_open" and in_table:
            cells = []
            row_line = token.map[0] + 1 if token.map else 0
        elif token.type == "inline" and in_table:
            cells.append(token.content.strip())
        elif token.type == "tr_close" and in_table:
            if in_header:
                headers = tuple(cells)
            elif any(cells):
                names = headers or tuple(
                    f"列{column}" for column in range(1, len(cells) + 1)
                )
                content = "；".join(
                    f"{names[column - 1] if column <= len(names) else f'列{column}'}：{value}"
                    for column, value in enumerate(cells, start=1)
                    if value
                )
                if 1 <= row_line <= len(source_lines):
                    content += "\n原始行：" + source_lines[row_line - 1].strip()
                blocks.append(ParsedBlock(
                    source_format="md", order=len(blocks), kind="table_row",
                    text=content,
                    location=SourceLocation(
                        line_start=row_line or None,
                        line_end=row_line or None,
                        table_index=table_index,
                    ),
                    heading_path=tuple(headings), table_headers=names,
                    text_source="native",
                ))
        elif token.type in {"fence", "code_block"}:
            text = token.content.strip()
            if text:
                blocks.append(ParsedBlock(
                    source_format="md", order=len(blocks), kind="code",
                    text=text,
                    location=SourceLocation(
                        line_start=token.map[0] + 1 if token.map else None,
                        line_end=token.map[1] if token.map else None,
                    ),
                    heading_path=tuple(headings), text_source="native",
                ))
        elif token.type == "paragraph_open" and not in_table:
            text = tokens[index + 1].content.strip()
            if text:
                blocks.append(ParsedBlock(
                    source_format="md", order=len(blocks), kind="paragraph",
                    text=text,
                    location=SourceLocation(
                        line_start=token.map[0] + 1 if token.map else None,
                        line_end=token.map[1] if token.map else None,
                    ),
                    heading_path=tuple(headings), text_source="native",
                ))
    return blocks
