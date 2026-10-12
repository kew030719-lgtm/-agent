import math
import os
from pathlib import Path

from fastembed.rerank.cross_encoder import TextCrossEncoder


MODEL_NAME = "BAAI/bge-reranker-v2-m3-int8"


class LocalReranker:
    def __init__(self, data_root: Path, download: bool = False) -> None:
        model_root = data_root / "knowledge" / "models"
        hub_root = data_root / "knowledge" / "hf_cache"
        model_root.mkdir(parents=True, exist_ok=True)
        hub_root.mkdir(parents=True, exist_ok=True)
        os.environ["HF_HOME"] = str(hub_root.resolve())
        os.environ["HF_HUB_DISABLE_XET"] = "1"
        self.model = TextCrossEncoder(
            model_name=MODEL_NAME,
            cache_dir=str(model_root),
            local_files_only=not download,
        )

    def scores(self, question: str, passages: list[str]) -> list[float]:
        if not passages:
            return []
        batch_size = int(os.getenv("RAG_RERANK_BATCH_SIZE", "1"))
        if not 1 <= batch_size <= 64:
            raise ValueError("RAG_RERANK_BATCH_SIZE 必须在 1 到 64 之间")
        # 长度相近的片段同批处理，减少 padding；最终恢复调用方的片段顺序。
        order = sorted(range(len(passages)), key=lambda index: len(passages[index]))
        sorted_values = [float(value) for value in self.model.rerank(
            question, [passages[index] for index in order], batch_size=batch_size,
        )]
        values = [0.0] * len(passages)
        if len(sorted_values) != len(passages):
            raise ValueError("重排模型返回了无效分数")
        for index, value in zip(order, sorted_values):
            values[index] = value
        if len(values) != len(passages) or not all(map(math.isfinite, values)):
            raise ValueError("重排模型返回了无效分数")
        return values
