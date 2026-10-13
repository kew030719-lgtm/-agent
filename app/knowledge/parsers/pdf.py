from dataclasses import replace
from pathlib import Path

import pymupdf

from app.config import ocr_tessdata_path
from app.knowledge.blocks import ParsedBlock, SourceLocation
from app.knowledge.images import save_pdf_image


def parse_pdf(
    pdf_path: Path,
    data_root: Path,
    document_id: int,
) -> list[ParsedBlock]:
    if document_id < 1:
        raise ValueError("文档 ID 必须大于 0")

    blocks: list[ParsedBlock] = []
    text_flags = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_PRESERVE_IMAGES
    configured_tessdata = ocr_tessdata_path(data_root)
    tessdata = str(configured_tessdata) if configured_tessdata is not None else None

    with pymupdf.open(str(pdf_path)) as document:
        for page_number, page in enumerate(document, start=1):
            native_text = page.get_text("text").strip()
            image_regions = [
                pymupdf.Rect(info["bbox"]) & page.rect
                for info in page.get_image_info()
            ]

            if not image_regions and not native_text and page.get_drawings():
                image_regions = [page.rect]

            image_regions = [
                region for region in image_regions if not region.is_empty
            ]

            if image_regions:
                try:
                    text_page = page.get_textpage_ocr(
                        flags=text_flags,
                        language="chi_sim+eng",
                        dpi=200,
                        full=False,
                        tessdata=tessdata,
                    )
                except Exception as exc:
                    raise RuntimeError(
                        f"PDF 第 {page_number} 页 OCR 失败；"
                        "请检查 PDF、Tesseract 和中文语言包"
                    ) from exc

                text_data = page.get_text(
                    "dict", flags=text_flags, textpage=text_page
                )
            else:
                text_data = page.get_text("dict", flags=text_flags)

            page_blocks: list[ParsedBlock] = []
            saved_images: list[tuple[pymupdf.Rect, str]] = []

            for image_index, region in enumerate(image_regions, start=1):
                png_data = page.get_pixmap(
                    clip=region, dpi=150, alpha=False
                ).tobytes("png")

                image_ref = save_pdf_image(
                    data_root, document_id, page_number, image_index, png_data
                )
                saved_images.append((region, image_ref))

                page_blocks.append(
                    ParsedBlock(
                        source_format="pdf",
                        order=0,
                        kind="image",
                        text="",
                        location=SourceLocation(
                            page=page_number,
                            bbox=tuple(region),
                        ),
                        image_ref=image_ref,
                    )
                )

            for item in text_data["blocks"]:
                if item["type"] != 0:
                    continue

                lines: list[str] = []
                is_ocr = False

                for line in item["lines"]:
                    spans = line["spans"]
                    line_text = "".join(
                        span["text"] for span in spans
                    ).strip()

                    if line_text:
                        lines.append(line_text)

                    if any(
                        span["font"] == "GlyphLessFont"
                        for span in spans
                    ):
                        is_ocr = True

                content = "\n".join(lines)
                if not content:
                    continue

                bbox = tuple(item["bbox"])
                image_ref: str | None = None

                if is_ocr:
                    center = pymupdf.Point(
                        (bbox[0] + bbox[2]) / 2,
                        (bbox[1] + bbox[3]) / 2,
                    )
                    image_ref = next(
                        (
                            ref
                            for region, ref in saved_images
                            if region.contains(center)
                        ),
                        None,
                    )

                    if image_ref is None and len(saved_images) == 1:
                        image_ref = saved_images[0][1]

                page_blocks.append(
                    ParsedBlock(
                        source_format="pdf",
                        order=0,
                        kind="paragraph",
                        text=content,
                        location=SourceLocation(
                            page=page_number,
                            bbox=bbox,
                        ),
                        text_source="ocr" if is_ocr else "native",
                        image_ref=image_ref,
                    )
                )

            page_blocks.sort(
                key=lambda block: (
                    block.location.bbox[1],
                    block.location.bbox[0],
                    0 if block.kind == "image" else 1,
                )
            )

            for block in page_blocks:
                blocks.append(replace(block, order=len(blocks)))

    return blocks
