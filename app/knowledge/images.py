from pathlib import Path


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def save_pdf_image(
    data_root: Path,
    document_id: int,
    page: int,
    image_index: int,
    png_data: bytes,
) -> str:
    if document_id < 1 or page < 1 or image_index < 1:
        raise ValueError("文档 ID、页码和图片序号必须大于 0")
    if not png_data.startswith(PNG_SIGNATURE):
        raise ValueError("图片数据必须是 PNG 格式")

    relative_path = (
        Path("knowledge")
        / "images"
        / str(document_id)
        / f"page-{page:04d}-img-{image_index:02d}.png"
    )
    image_path = data_root / relative_path
    image_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    image_path.write_bytes(png_data)
    image_path.chmod(0o600)
    return relative_path.as_posix()
