"""为现有 wk50 演示库补登记产品目录，不重建账号、文档或向量。"""
import argparse
import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from app.auth import connect
from app.api.database import initialize
from app.knowledge.catalog import ProductMetadata, register_product


def register_existing(root: Path) -> tuple[int, Path]:
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('profile') != 'wk50':
        raise ValueError('此脚本仅用于 wk50 模拟公司')
    backup = root / ('before_product_catalog_' + datetime.now().strftime('%Y%m%d_%H%M%S_%f') + '.db')
    with closing(connect(root / 'company.db')) as db:
        with closing(sqlite3.connect(backup)) as target:
            db.backup(target)
        backup.chmod(0o600)
        initialize(db)
        total = 0
        with db:
            for doc_id, title, owner, role in db.execute('SELECT d.id,d.title,d.created_by,u.role FROM knowledge_documents d JOIN users u ON u.username=d.created_by'):
                match = re.fullmatch(r'维科产品(0[1-9]|10).+', title)
                if not match:
                    continue
                # 已由管理员登记的内容保留，重复运行不会覆盖。
                if db.execute('SELECT 1 FROM product_documents WHERE document_id=?', (doc_id,)).fetchone():
                    continue
                register_product(db, {'username': owner, 'role': role}, doc_id,
                                 ProductMetadata(company='维科科技', code='WK-P' + match[1], name=title))
                total += 1
    return total, backup


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    total, backup = register_existing(args.root)
    print(f'新增登记 {total} 份产品资料；备份：{backup}')


if __name__ == '__main__':
    main()
