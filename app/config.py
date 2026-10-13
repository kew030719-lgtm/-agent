import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_config() -> None:
    """加载项目 .env；终端已有的环境变量优先。"""
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def deepseek_model() -> str:
    return os.getenv("DEEPSEEK_MODEL", "deepseek-flash")


def configured_database_path(override: Path | None = None) -> Path:
    """API、CLI 与 Worker 使用相同数据库配置；显式参数优先。"""
    from app.auth import DB_PATH
    value = os.getenv("API_DATABASE_PATH", "").strip()
    return (override if override is not None else Path(value) if value else DB_PATH).expanduser().resolve()


def model_data_root(data_root: Path) -> Path:
    """模型缓存可共享，文档和数据库仍归属各自的数据目录。"""
    value = os.getenv("INGESTION_MODEL_ROOT", "").strip()
    return (Path(value) if value else data_root).expanduser().resolve()


def ocr_tessdata_path(data_root: Path) -> Path | None:
    """显式语言包目录优先，未设置时保留本地目录/系统探测逻辑。"""
    value = os.getenv("OCR_TESSDATA_PATH", "").strip()
    if value:
        return Path(value).expanduser().resolve()
    local = data_root / "knowledge" / "tessdata"
    return local.resolve() if local.is_dir() else None
