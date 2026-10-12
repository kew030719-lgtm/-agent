from pathlib import Path

from app.knowledge.blocks import ParsedBlock
from app.knowledge.parsers.excel import parse_excel
from app.knowledge.parsers.markdown import parse_markdown
from app.knowledge.parsers.pdf import parse_pdf
from app.knowledge.parsers.word import parse_word


def parse_file(
    path: Path,
    data_root: Path,
    document_id: int,
) -> list[ParsedBlock]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return parse_pdf(path, data_root, document_id)
    if suffix == ".md":
        return parse_markdown(path)
    if suffix == ".docx":
        return parse_word(path)
    if suffix in {".xlsx", ".xlsm", ".xls"}:
        return parse_excel(path)
    if suffix == ".doc":
        raise ValueError("旧版 .doc 请先转换为 .docx")
    raise ValueError(f"不支持的知识文件格式：{suffix or '(无扩展名)'}")
