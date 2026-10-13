"""为已有 50 人模拟公司补齐组织档案，不重建账号、余额或知识库。"""

import argparse
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from app.auth import connect
from app.organization import JOB_LEVELS, set_organization, validate_organization


def assign_organization(db: sqlite3.Connection, accounts: list[dict[str, Any]]) -> None:
    if len(accounts) != 50 or {a["username"] for a in accounts} != {
        row[0] for row in db.execute("SELECT username FROM users")
    }:
        raise ValueError("仅适用于账号与清单完全一致的 50 人模拟公司")
    if db.execute("SELECT 1 FROM users WHERE department_id IS NOT NULL OR manager_username IS NOT NULL OR job_level<>'employee' OR display_name<>'' LIMIT 1").fetchone():
        raise ValueError("已有组织档案，请在前端编辑，避免覆盖手工调整")
    departments = dict(db.execute("SELECT name,id FROM departments"))
    chairman, general = "wk_admin_01", "wk_admin_02"
    if not {chairman, general}.issubset({a["username"] for a in accounts}):
        raise ValueError("缺少维科模拟公司管理账号")
    grouped: dict[str, list[dict[str, Any]]] = {}
    for account in accounts:
        grouped.setdefault(account["department"], []).append(account)
    with db:
        for department, people in grouped.items():
            for index, account in enumerate(people):
                if department == "管理部":
                    level = "chairman" if account["username"] == chairman else "general_manager"
                    manager = None if level == "chairman" else chairman
                else:
                    level = "department_manager" if index == 0 else "team_lead" if index == 1 else "employee"
                    manager = general if index == 0 else people[0 if index == 1 else 1]["username"]
                account.update({"display_name": account["name"], "department_id": departments[department],
                                "job_level": level, "manager_username": manager})
                set_organization(db, account["username"], {
                    "display_name": account["display_name"], "department_id": account["department_id"],
                    "job_level": level, "manager_username": manager,
                })
        validate_organization(db)


def write_accounts(root: Path, accounts: list[dict[str, Any]]) -> None:
    path = root / "accounts.json"
    path.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")
    path.chmod(0o600)
    rows = ["# 维科科技模拟公司组织档案", "", "50 人模拟数据；原账号、密码和业务权限保持不变。", "",
            "管理部设董事长和总经理；其他 7 个部门各设部门经理、组长及普通员工。职级不自动授予系统管理权。", "",
            "| 账号 | 姓名 | 部门 | 职级 | 直属上级 | 权限角色 |", "| --- | --- | --- | --- | --- | --- |"]
    rows.extend(f"| {a['username']} | {a['display_name']} | {a['department']} | {JOB_LEVELS[a['job_level']]} | {a['manager_username'] or '无'} | {a['role']} |" for a in accounts)
    path = root / "ORGANIZATION.md"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    path.chmod(0o600)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root: Path = args.root.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("profile") != "wk50":
        raise ValueError("只允许修改 wk50 模拟公司")
    accounts = json.loads((root / "accounts.json").read_text(encoding="utf-8"))
    backup = root / ("before_organization_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".db")
    with closing(sqlite3.connect(root / "company.db")) as source, closing(sqlite3.connect(backup)) as target:
        source.backup(target)
    backup.chmod(0o600)
    with closing(connect(root / "company.db")) as db:
        assign_organization(db, accounts)
    write_accounts(root, accounts)
    print(f"已更新 50 人组织档案。备份：{backup}")


if __name__ == "__main__":
    main()
