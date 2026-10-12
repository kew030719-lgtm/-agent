"""复用已保存的查询改写，对相同候选比较原批次和新批次的耗时与证据。"""

import argparse
import json
import sqlite3
import time
from pathlib import Path

from app.agent.service import AssistantService
from app.auth import DB_PATH
from app.knowledge.answer import _build_evidence
from app.knowledge.retrieval import search_knowledge, with_neighbor_context
from scripts.company_simulation import configure, save_json


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = root / "rerank_latency_comparison.json"
    if output.exists():
        raise ValueError("报告已存在，请保留旧报告后再运行")
    configure(root)
    cases = json.loads((root / "recall_improved.json").read_text(encoding="utf-8"))["results"]
    service = AssistantService(DB_PATH.parent)
    user = {"username": "sim_admin_001", "role": "admin"}
    results = []
    try:
        reranker = service.get_reranker()
        if reranker is None:
            raise ValueError("必须安装本地重排模型")
        with sqlite3.connect(root / "company.db") as db:
            for number, case in enumerate(cases):
                question = case["question"]
                hits = search_knowledge(db, user, question, service.get_embedder(), service.get_store(), limit=36, queries=case["queries"])
                passages = [hit["text"] for hit in hits]
                row = {"question": question, "candidate_ids": [hit["chunk_id"] for hit in hits], "gold_ids": case["gold_ids"]}
                # 交替执行顺序，降低先后顺序造成的系统性偏差。
                for mode in (("before", "after") if number % 2 == 0 else ("after", "before")):
                    started = time.perf_counter()
                    scores = list(reranker.model.rerank(question, passages, batch_size=64)) if mode == "before" else reranker.scores(question, passages)
                    seconds = time.perf_counter() - started
                    ranked = [hit for _, hit in sorted(zip(scores, hits), key=lambda pair: pair[0], reverse=True)]
                    _, evidence = _build_evidence(with_neighbor_context(db, user, ranked))
                    evidence_ids = [hit["chunk_id"] for hit in evidence]
                    row[mode] = {"seconds": round(seconds, 3), "evidence_ids": evidence_ids, "evidence_hit": bool(set(case["gold_ids"]).intersection(evidence_ids))}
                results.append(row)
                print(json.dumps({"case": number + 1, **row}, ensure_ascii=False), flush=True)
        report = {"cases": len(results), "results": results}
        for mode in ("before", "after"):
            report[mode] = {"mean_seconds": round(sum(row[mode]["seconds"] for row in results) / len(results), 3), "evidence_hit_rate": sum(row[mode]["evidence_hit"] for row in results) / len(results)}
        save_json(output, report)
        print(json.dumps({key: value for key, value in report.items() if key != "results"}, ensure_ascii=False), flush=True)
    finally:
        service.close()


if __name__ == "__main__":
    main()
