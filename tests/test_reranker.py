import unittest
from unittest.mock import Mock, patch

from app.knowledge.reranker import LocalReranker


class RerankerTests(unittest.TestCase):
    def reranker(self) -> LocalReranker:
        reranker = object.__new__(LocalReranker)
        reranker.model = Mock()
        return reranker

    def test_all_passages_are_scored_and_order_is_restored(self) -> None:
        reranker = self.reranker()
        reranker.model.rerank.return_value = iter([0.1, 0.2, 0.3])
        with patch.dict("os.environ", {"RAG_RERANK_BATCH_SIZE": "2"}):
            self.assertEqual(reranker.scores("问题", ["最长的一段", "短", "中等"]), [0.3, 0.1, 0.2])
        reranker.model.rerank.assert_called_once_with("问题", ["短", "中等", "最长的一段"], batch_size=2)

    def test_invalid_scores_or_configuration_are_rejected(self) -> None:
        reranker = self.reranker()
        with patch.dict("os.environ", {"RAG_RERANK_BATCH_SIZE": "1"}):
            for scores in ([1], [1, float("nan")], [float("inf"), 2]):
                reranker.model.rerank.return_value = scores
                with self.assertRaises(ValueError):
                    reranker.scores("问题", ["甲", "乙"])
        for value in ("0", "65", "invalid"):
            with patch.dict("os.environ", {"RAG_RERANK_BATCH_SIZE": value}):
                with self.assertRaises(ValueError):
                    reranker.scores("问题", ["甲"])

    def test_empty_passages_do_not_call_model(self) -> None:
        reranker = self.reranker()
        self.assertEqual(reranker.scores("问题", []), [])
        reranker.model.rerank.assert_not_called()
