#!/usr/bin/env python3
"""History & Session Migration and Rollback CLI.

Usage:
  python scripts/migrate_history.py --migrate [--history-dir history-xml]
  python scripts/migrate_history.py --rollback [--history-dir history-xml]
  python scripts/migrate_history.py --status [--history-dir history-xml]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.sqlite_history import LegacyHistoryMigrator, SQLiteHistoryStore  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate or roll back Screens-Trans-Chatbot history and sessions.")
    parser.add_argument("--history-dir", default=str(PROJECT_ROOT / "history-xml"), help="Path to history-xml directory")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--migrate", action="store_true", help="Migrate legacy XML/JSON history to SQLite FTS5")
    group.add_argument("--rollback", action="store_true", help="Restore legacy XML/JSON files from pre-migration backup")
    group.add_argument("--status", action="store_true", help="Show current migration and SQLite store status")
    args = parser.parse_args(argv)

    history_dir = Path(args.history_dir)
    history_dir.mkdir(parents=True, exist_ok=True)
    store = SQLiteHistoryStore(history_dir / "stc_history_v2.sqlite3")
    migrator = LegacyHistoryMigrator(history_dir, sqlite_store=store)

    try:
        if args.migrate:
            result = migrator.migrate()
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        if args.rollback:
            result = migrator.rollback()
            print(json.dumps(result, indent=2, ensure_ascii=False))
            return 0
        status_file = history_dir / "migration_status.json"
        status_data = json.loads(status_file.read_text(encoding="utf-8")) if status_file.is_file() else {"status": "not_migrated"}
        status_data["sqlite_record_count"] = store.count()
        status_data["pi_sdk_sessions_dir_exists"] = (history_dir / "pi-sdk-sessions").is_dir()
        print(json.dumps(status_data, indent=2, ensure_ascii=False))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
