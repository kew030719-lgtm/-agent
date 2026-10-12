"""真实HTTP验收召回改进，包含资料不足和角色隔离，不修改知识原文。"""

import argparse
import json
import time
from pathlib import Path

import httpx

from scripts.company_simulation import HEADERS, conversation, login, save_json, send


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8002")
    args = parser.parse_args()
    accounts = json.loads((args.root / "accounts.json").read_text(encoding="utf-8"))
    results = []
    with httpx.Client(base_url=args.base_url, headers=HEADERS, timeout=180) as client:
        login(client, accounts[0])
        cid = conversation(client)
        for question, markers in [
            ("想休几天带薪假，要走哪些手续？", ("请假单", "主管")),
            ("那提前多久？", ("三个工作日",)),
            ("公司消防撤离后，按制度到哪里集合？", ("东侧", "广场")),
            ("能给我解释一下星河产品44吗？", ("产品44", "项目交付")),
            ("V42.8的项目提交后谁检查？", ("管理员",)),
            ("每个员工有多久的年假？", ()),
            ("公司的火星驻地补贴有多少钱？", ()),
        ]:
            started = time.perf_counter()
            response = send(client, cid, question)
            payload = response.json()
            answer = payload.get("answer", "")
            passed = response.status_code == 200
            if markers:
                passed = passed and bool(payload.get("feedback_allowed")) and all(marker in answer for marker in markers)
            else:
                passed = passed and not payload.get("feedback_allowed") and "已转人工" in answer
            results.append({"question": question, "passed": passed, "seconds": round(time.perf_counter() - started, 2), "response": payload})
            print(f"{'PASS' if passed else 'FAIL'} {question}: {answer}", flush=True)
        login(client, next(a for a in accounts if a["username"] == "sim_employee_001"))
        payload = send(client, conversation(client), "能给我解释一下星河产品44吗？").json()
        results.append({"question": "普通员工查询finance范围产品44", "passed": not payload.get("feedback_allowed") and "已转人工" in payload.get("answer", ""), "response": payload})
    report = {"passed": sum(bool(row["passed"]) for row in results), "total": len(results), "results": results}
    output = args.root / ("recall_live_" + time.strftime("%Y%m%d_%H%M%S") + ".json")
    save_json(output, report)
    print(f"真实问答：{report['passed']}/{report['total']}；{output}")
    if report["passed"] != report["total"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
