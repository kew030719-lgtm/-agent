"""固定金标问题的检索评估；baseline 必须在检索修改前运行并保留。"""

import argparse
import importlib.util
import json
import os
import sqlite3
import time
from pathlib import Path
from types import ModuleType

from openai import OpenAI

from app.agent.service import AssistantService
from app.auth import DB_PATH
from app.knowledge.answer import _build_evidence
from app.knowledge.retrieval import search_knowledge
from scripts.company_simulation import configure, save_json


CASES = [
    ("年假怎么申请？", "星河年假制度", "直属主管"),
    ("想休几天带薪假，要走哪些手续？", "星河年假制度", "提交请假单"),
    ("请假单交上去之后谁来批？", "星河年假制度", "直属主管"),
    ("前一问题：年假怎么申请？\n当前追问：那提前多久？", "星河年假制度", "三个工作日"),
    ("薪酬调整需要谁审批？", "星河薪酬审核", "总经理"),
    ("调薪是不是一个人签字就行？", "星河薪酬审核", "共同审批"),
    ("HR-ORCHID-742是什么？", "星河薪酬审核", "HR-ORCHID-742"),
    ("VPN故障怎么办？", "星河VPN维护", "检查网络"),
    ("连不上公司的VPN，先做什么？", "星河VPN维护", "检查网络"),
    ("IT-CEDAR-853是什么？", "星河VPN维护", "IT-CEDAR-853"),
    ("报销需要哪些材料？", "星河财务报销", "支付凭证"),
    ("我垫了钱，找财务报账要交什么东西？", "星河财务报销", "支付凭证"),
    ("FIN-LOTUS-964是什么？", "星河财务报销", "FIN-LOTUS-964"),
    ("消防集合地点在哪里？", "消防集合扫描件", "东侧"),
    ("起火撤离之后大家到哪儿碰头？", "消防集合扫描件", "广场"),
    ("能给我解释一下星河产品44吗？", "星河产品44操作手册", "模块01"),
    ("星河产品44的模块16怎么操作？", "星河产品44操作手册", "模块16"),
    ("V44.16的交付记录在哪里？", "星河产品44操作手册", "V44.16"),
    ("星河产品42的模块08做什么？", "星河产品42操作手册", "模块08"),
    ("V42.8的项目提交后谁检查？", "星河产品42操作手册", "V42.8"),
]


def baseline_module(root: Path) -> ModuleType:
    def load(name: str) -> ModuleType:
        path = root / "recall_baseline_code" / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"recall_baseline_{name}", path)
        if spec is None or spec.loader is None:
            raise ValueError("缺少修改前代码快照，不能重建基准")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    retrieval = load("retrieval")
    retrieval.search_keywords = load("keyword").search_keywords
    return retrieval


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--mode", choices=("baseline", "improved"), required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    output = root / f"recall_{args.mode}.json"
    if output.exists():
        raise ValueError("报告已存在，请先保留或重命名旧报告，避免覆盖基准")
    configure(root)
    service = AssistantService(DB_PATH.parent)
    results = []
    baseline = baseline_module(root) if args.mode == "baseline" else None
    admin = {"username": "sim_admin_001", "role": "admin"}
    try:
        with sqlite3.connect(root / "company.db") as db, OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"], base_url=os.environ["DEEPSEEK_BASE_URL"], timeout=15, max_retries=0,
        ) as client:
            embedder, store, reranker = service.get_embedder(), service.get_store(), service.get_reranker()
            for question, title, fragment in CASES:
                gold = {row[0] for row in db.execute(
                    "SELECT c.id FROM knowledge_chunks c JOIN knowledge_documents d ON d.id=c.document_id WHERE d.title=? AND instr(c.text,?)>0", (title, fragment))}
                if not gold:
                    raise ValueError(f"金标没有对应片段：{title}/{fragment}")
                started = time.perf_counter()
                queries = [question]
                if args.mode == "improved":
                    from app.knowledge.query import rewrite_queries
                    queries = rewrite_queries(client, question)
                    hits = search_knowledge(db, admin, question, embedder, store, limit=36, queries=queries)
                    ranked = list(hits)
                else:
                    hits = baseline.search_knowledge(db, admin, question, embedder, store, limit=12)
                    ranked = [h for h in hits if h["score"] >= 0.55]
                if ranked and reranker:
                    scores = reranker.scores(question, [h["text"] for h in ranked])
                    ranked = [h for _, h in sorted(zip(scores, ranked), key=lambda pair: pair[0], reverse=True)]
                if args.mode == "improved":
                    from app.knowledge.retrieval import with_neighbor_context
                    ranked = with_neighbor_context(db, admin, ranked)
                _, evidence = _build_evidence(ranked)
                candidate_ids = [h["chunk_id"] for h in hits]
                evidence_ids = [h["chunk_id"] for h in evidence]
                row = {"question": question, "gold_ids": sorted(gold), "queries": queries,
                       "candidate_ids": candidate_ids, "evidence_ids": evidence_ids,
                       "recall_at_12": len(gold.intersection(candidate_ids[:12])) / len(gold),
                       "candidate_recall": len(gold.intersection(candidate_ids)) / len(gold),
                       "evidence_hit": bool(gold.intersection(evidence_ids)), "seconds": round(time.perf_counter() - started, 3)}
                results.append(row)
                print(f"{args.mode} {len(results)}/{len(CASES)} evidence={row['evidence_hit']}: {question}", flush=True)
            report = {"mode": args.mode, "cases": len(results), "candidate_limit": 12 if args.mode == "baseline" else 36,
                      "mean_recall_at_12": sum(r["recall_at_12"] for r in results) / len(results),
                      "mean_candidate_recall": sum(r["candidate_recall"] for r in results) / len(results),
                      "evidence_hit_rate": sum(r["evidence_hit"] for r in results) / len(results), "results": results}
            save_json(output, report)
            print(json.dumps({k: v for k, v in report.items() if k != "results"}, ensure_ascii=False))
    finally:
        service.close()


if __name__ == "__main__":
    main()
