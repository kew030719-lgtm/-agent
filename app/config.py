import os
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_config() -> None:
    """加载项目 .env；终端已有的环境变量优先。"""
    load_dotenv(PROJECT_ROOT / ".env", override=False)


def deepseek_model() -> str:
    return os.getenv("DEEPSEEK_MODEL", "deepseek-flash")
