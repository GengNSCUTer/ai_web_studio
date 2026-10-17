"""部署前显式升级；非空历史库必须提供已完成的备份。"""

import argparse
import json
from pathlib import Path
from sqlalchemy import inspect
from app.core.database import engine
from app.core.migrations import require_current_schema, upgrade_database


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["upgrade", "check"])
    parser.add_argument("--adopt-legacy", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args()
    if args.action == "check":
        require_current_schema(engine)
    else:
        with engine.connect() as connection:
            populated = bool(set(inspect(connection).get_table_names()) - {"alembic_version"})
        if populated:
            if not args.backup_dir or not (args.backup_dir / "database.dump").is_file():
                parser.error("已有业务表，升级前必须指定 --backup-dir 指向已完成的备份")
            manifest = json.loads((args.backup_dir / "manifest.json").read_text(encoding="utf-8"))
            if not manifest.get("complete") or manifest.get("database") != engine.url.database:
                parser.error("备份不完整或不是当前数据库的备份")
        upgrade_database(engine, adopt_legacy=args.adopt_legacy)
        require_current_schema(engine)
    print(json.dumps({"schema": "current"}))


if __name__ == "__main__":
    main()
