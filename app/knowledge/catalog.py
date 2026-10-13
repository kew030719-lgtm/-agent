"""产品按公司与编号去重；登记信息继承对应文档的阅读权限。"""

import sqlite3
from pydantic import BaseModel, ConfigDict, Field

from app.auth import User
from app.knowledge.access import require_document_manager, visible_document_ids


class ProductMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    company: str = Field(min_length=1, max_length=100)
    code: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    name: str = Field(min_length=1, max_length=200)


def ensure_catalog_schema(db: sqlite3.Connection) -> None:
    db.execute("""CREATE TABLE IF NOT EXISTS product_documents (
        document_id INTEGER PRIMARY KEY REFERENCES knowledge_documents(id) ON DELETE CASCADE,
        company TEXT NOT NULL, code TEXT NOT NULL, name TEXT NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_product_identity ON product_documents(company,code)")


def register_product(db: sqlite3.Connection, actor: User, document_id: int, product: ProductMetadata) -> None:
    require_document_manager(db, actor, document_id)
    db.execute("INSERT INTO product_documents(document_id,company,code,name) VALUES (?,?,?,?) "
               "ON CONFLICT(document_id) DO UPDATE SET company=excluded.company,code=excluded.code,name=excluded.name",
               (document_id, product.company, product.code.upper(), product.name))


def visible_products(db: sqlite3.Connection, user: User) -> list[dict[str, object]]:
    allowed = set(visible_document_ids(db, user))
    products: dict[tuple[str, str], dict[str, object]] = {}
    for doc_id, company, code, name in db.execute("SELECT document_id,company,code,name FROM product_documents ORDER BY document_id"):
        if doc_id not in allowed:
            continue
        item = products.setdefault((company, code), {"company": company, "code": code, "name": name, "source_ids": []})
        item["source_ids"].append(doc_id)
    return sorted(products.values(), key=lambda p: (p["company"], p["code"]))


def answer_catalog(db: sqlite3.Connection, user: User, action: str, company: str = "") -> tuple[str, list[int]]:
    products = [item for item in visible_products(db, user) if not company or item["company"] == company]
    subject = f"{company}的" if company else ""
    if not products:
        return f"当前账号没有可查看的{subject}已登记产品。目录未登记或资料不可见，都可能造成这种情况；不能据此断定公司没有产品。", []
    lines = [f"当前账号可查看的{subject}已登记产品共 {len(products)} 个（按公司与产品编号去重）。"]
    if action == "list_products":
        lines.extend(f"{index}. {p['name']}（{p['code']}，{p['company']}）" for index, p in enumerate(products[:50], 1))
        if len(products) > 50:
            lines.append("以上展示前 50 个产品。")
    lines.append("这是当前可见目录的数量，不保证等于公司全部产品数量。")
    lines.append("来源：产品登记目录及当前有权阅读的关联资料。")
    return "\n".join(lines), sorted({i for p in products for i in p["source_ids"]})
