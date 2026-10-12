"""Download verified Tesseract language data for PDF OCR."""

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from urllib.request import urlopen


DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "knowledge" / "tessdata"
LANGUAGES = {
    "chi_sim": "a5fcb6f0db1e1d6d8522f39db4e848f05984669172e584e8d76b6b3141e1f730",
    "eng": "7d4322bd2a7749724879683fc3912cb542f19906c83bcc1a52132556427170b2",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for part in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()


def prepare_language(name: str, expected_hash: str) -> None:
    target = DATA_DIR / f"{name}.traineddata"
    if target.is_file() and sha256_file(target) == expected_hash:
        print(f"{name}: 已就绪")
        return

    url = (
        "https://raw.githubusercontent.com/tesseract-ocr/"
        f"tessdata_fast/main/{name}.traineddata"
    )
    with tempfile.NamedTemporaryFile(
        dir=DATA_DIR, prefix=f".{name}-", delete=False
    ) as temporary:
        temporary_path = Path(temporary.name)
        try:
            with urlopen(url, timeout=120) as response:
                shutil.copyfileobj(response, temporary)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
    try:
        if sha256_file(temporary_path) != expected_hash:
            raise RuntimeError(f"{name} 语言包校验失败")
        temporary_path.chmod(0o600)
        os.replace(temporary_path, target)
    finally:
        temporary_path.unlink(missing_ok=True)
    print(f"{name}: 已下载并校验")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    DATA_DIR.chmod(0o700)
    for name, expected_hash in LANGUAGES.items():
        prepare_language(name, expected_hash)


if __name__ == "__main__":
    main()
