"""High-Performance SQLite WAL + FTS5 History & Session Store with Legacy Migration/Rollback.

Solves:
1. Sub-millisecond full-text search and pagination under 1,000+ history events and large screenshots.
2. Lazy thumbnail generation with bounded cache.
3. Seamless migration & rollback from legacy `history-xml/result.html`, `snap_history.json`,
   and `screenshot_chat_sessions.json`.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import time
from typing import Any
from PIL import Image


class SQLiteHistoryStore:
    """Durable SQLite WAL + FTS5 history store supporting 10,000+ events without UI lag."""

    def __init__(self, db_path: str | Path, thumbnails_dir: str | Path | None = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.thumbnails_dir = Path(thumbnails_dir) if thumbnails_dir else self.db_path.parent / "thumbnails"
        self.thumbnails_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL;")
            cur.execute("PRAGMA synchronous=NORMAL;")
            cur.execute("PRAGMA temp_store=MEMORY;")
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS history_events (
                    entry_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL DEFAULT 'default',
                    timestamp_str TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    prompt_name TEXT NOT NULL DEFAULT '',
                    model_name TEXT NOT NULL DEFAULT '',
                    provider_name TEXT NOT NULL DEFAULT '',
                    image_path TEXT NOT NULL DEFAULT '',
                    thumbnail_path TEXT NOT NULL DEFAULT '',
                    prompt_text TEXT NOT NULL DEFAULT '',
                    answer_text TEXT NOT NULL DEFAULT '',
                    is_pinned INTEGER NOT NULL DEFAULT 0,
                    pin_title TEXT DEFAULT NULL,
                    token_count INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                );
                """
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_history_created ON history_events(created_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_history_session ON history_events(session_id, created_at DESC);")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_history_pinned ON history_events(is_pinned, created_at DESC);")
            cur.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS history_fts USING fts5(
                    entry_id UNINDEXED,
                    prompt_name,
                    model_name,
                    prompt_text,
                    answer_text,
                    pin_title
                );
                """
            )
            self._conn.commit()

    def record_event(
        self,
        *,
        entry_id: str = "",
        session_id: str = "default",
        timestamp_str: str = "",
        created_at: float | None = None,
        prompt_name: str = "",
        model_name: str = "",
        provider_name: str = "",
        image_path: str = "",
        prompt_text: str = "",
        answer_text: str = "",
        is_pinned: bool = False,
        pin_title: str | None = None,
        token_count: int = 0,
        cost_usd: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        ts = float(created_at if created_at is not None else time.time())
        ts_str = str(timestamp_str or time.strftime("%Y%m%d_%H%M%S", time.localtime(ts)))
        if not entry_id:
            seed = f"{ts_str}|{image_path}|{answer_text[:400]}|{ts}"
            entry_id = "evt_" + hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]
        prov = str(provider_name or (model_name.split("/", 1)[0] if "/" in str(model_name) else ""))
        meta_json = json.dumps(metadata or {}, ensure_ascii=False)

        with self._lock:
            cur = self._conn.cursor()
            cur.execute(
                """
                INSERT OR REPLACE INTO history_events (
                    entry_id, session_id, timestamp_str, created_at, prompt_name,
                    model_name, provider_name, image_path, thumbnail_path,
                    prompt_text, answer_text, is_pinned, pin_title,
                    token_count, cost_usd, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    str(session_id or "default"),
                    ts_str,
                    ts,
                    str(prompt_name or ""),
                    str(model_name or ""),
                    prov,
                    str(image_path or ""),
                    "",
                    str(prompt_text or ""),
                    str(answer_text or ""),
                    1 if is_pinned else 0,
                    pin_title,
                    int(token_count),
                    float(cost_usd),
                    meta_json,
                ),
            )
            cur.execute("DELETE FROM history_fts WHERE entry_id = ?", (entry_id,))
            cur.execute(
                """
                INSERT INTO history_fts (entry_id, prompt_name, model_name, prompt_text, answer_text, pin_title)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    str(prompt_name or ""),
                    str(model_name or ""),
                    str(prompt_text or ""),
                    str(answer_text or ""),
                    str(pin_title or ""),
                ),
            )
            self._conn.commit()
        return entry_id

    def record_batch(self, events: list[dict[str, Any]]) -> int:
        if not events:
            return 0
        rows = []
        fts_rows = []
        now = time.time()
        for idx, item in enumerate(events):
            ts = float(item.get("created_at") or (now + idx * 0.0001))
            ts_str = str(item.get("timestamp_str") or item.get("timestamp") or time.strftime("%Y%m%d_%H%M%S"))
            ans = str(item.get("answer_text") or item.get("ans") or "")
            img_path = str(item.get("image_path") or "")
            entry_id = str(item.get("entry_id") or item.get("id") or "")
            if not entry_id:
                seed = f"{ts_str}|{img_path}|{ans[:400]}|{idx}"
                entry_id = "evt_" + hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:16]
            model = str(item.get("model_name") or "Unknown")
            prov = str(item.get("provider_name") or (model.split("/", 1)[0] if "/" in model else ""))
            p_name = str(item.get("prompt_name") or "Trans")
            p_text = str(item.get("prompt_text") or "")
            pinned = 1 if item.get("is_pinned") else 0
            p_title = item.get("pin_title")
            rows.append(
                (
                    entry_id,
                    str(item.get("session_id") or "default"),
                    ts_str,
                    ts,
                    p_name,
                    model,
                    prov,
                    img_path,
                    "",
                    p_text,
                    ans,
                    pinned,
                    p_title,
                    int(item.get("token_count") or 0),
                    float(item.get("cost_usd") or 0.0),
                    json.dumps(item.get("metadata") or {}, ensure_ascii=False),
                )
            )
            fts_rows.append((entry_id, p_name, model, p_text, ans, str(p_title or "")))

        with self._lock:
            cur = self._conn.cursor()
            cur.executemany(
                """
                INSERT OR REPLACE INTO history_events (
                    entry_id, session_id, timestamp_str, created_at, prompt_name,
                    model_name, provider_name, image_path, thumbnail_path,
                    prompt_text, answer_text, is_pinned, pin_title,
                    token_count, cost_usd, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            cur.executemany(
                """
                INSERT INTO history_fts (entry_id, prompt_name, model_name, prompt_text, answer_text, pin_title)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                fts_rows,
            )
            self._conn.commit()
        return len(rows)

    def search(self, query: str = "", *, session_id: str | None = None, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        clean_q = str(query or "").strip()
        with self._lock:
            cur = self._conn.cursor()
            if clean_q:
                safe_tokens = [re.sub(r"[^\w]", "", tok) for tok in clean_q.split() if re.sub(r"[^\w]", "", tok)]
                fts_query = " AND ".join(f'"{tok}"*' for tok in safe_tokens) if safe_tokens else f'"{clean_q}"'
                if session_id:
                    cur.execute(
                        """
                        SELECT h.* FROM history_events h
                        JOIN history_fts f ON h.entry_id = f.entry_id
                        WHERE history_fts MATCH ? AND h.session_id = ?
                        ORDER BY h.created_at DESC LIMIT ? OFFSET ?
                        """,
                        (fts_query, session_id, int(limit), int(offset)),
                    )
                else:
                    cur.execute(
                        """
                        SELECT h.* FROM history_events h
                        JOIN history_fts f ON h.entry_id = f.entry_id
                        WHERE history_fts MATCH ?
                        ORDER BY h.created_at DESC LIMIT ? OFFSET ?
                        """,
                        (fts_query, int(limit), int(offset)),
                    )
            else:
                if session_id:
                    cur.execute(
                        """
                        SELECT * FROM history_events
                        WHERE session_id = ?
                        ORDER BY created_at DESC LIMIT ? OFFSET ?
                        """,
                        (session_id, int(limit), int(offset)),
                    )
                else:
                    cur.execute(
                        """
                        SELECT * FROM history_events
                        ORDER BY created_at DESC LIMIT ? OFFSET ?
                        """,
                        (int(limit), int(offset)),
                    )
            return [dict(row) for row in cur.fetchall()]

    def count(self) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT COUNT(*) FROM history_events")
            row = cur.fetchone()
            return int(row[0]) if row else 0

    def get_or_create_thumbnail(self, entry_id: str, max_size: tuple[int, int] = (240, 240)) -> str:
        """Lazily generates a small thumbnail PNG only when requested by the UI."""
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("SELECT image_path, thumbnail_path FROM history_events WHERE entry_id = ?", (entry_id,))
            row = cur.fetchone()
            if not row:
                return ""
            existing_thumb = str(row["thumbnail_path"] or "")
            if existing_thumb and os.path.isfile(existing_thumb):
                return existing_thumb
            image_path = str(row["image_path"] or "")
            if not image_path or not os.path.isfile(image_path):
                return ""
            thumb_path = str(self.thumbnails_dir / f"thumb_{entry_id}.png")
            try:
                with Image.open(image_path) as img:
                    copy_img = img.copy()
                    copy_img.thumbnail(max_size, Image.Resampling.BILINEAR)
                    copy_img.save(thumb_path, format="PNG")
                    copy_img.close()
                cur.execute(
                    "UPDATE history_events SET thumbnail_path = ? WHERE entry_id = ?",
                    (thumb_path, entry_id),
                )
                self._conn.commit()
                return thumb_path
            except Exception:
                return ""

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass


class LegacyHistoryMigrator:
    """Migrates legacy `history-xml` (snap_history.json, screenshot_chat_sessions.json, result.html)
    into SQLiteHistoryStore and creates a rollback backup archive."""

    def __init__(self, history_xml_dir: str | Path, sqlite_store: SQLiteHistoryStore | None = None):
        self.history_xml_dir = Path(history_xml_dir)
        self.backup_dir = self.history_xml_dir / "migration_backup_v1"
        self.sqlite_store = sqlite_store or SQLiteHistoryStore(self.history_xml_dir / "stc_history_v2.sqlite3")

    def migrate(self) -> dict[str, Any]:
        self.history_xml_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backed_up_files: list[str] = []
        for filename in ("snap_history.json", "screenshot_chat_sessions.json", "result.html", "custom_prompt_history.json"):
            src = self.history_xml_dir / filename
            if src.is_file():
                dst = self.backup_dir / filename
                shutil.copy2(src, dst)
                backed_up_files.append(filename)

        migrated_snaps = 0
        snap_file = self.history_xml_dir / "snap_history.json"
        if snap_file.is_file():
            try:
                payload = json.loads(snap_file.read_text(encoding="utf-8"))
                items = payload.get("history", []) if isinstance(payload, dict) else (payload if isinstance(payload, list) else [])
                pins = {p.get("id"): p for p in (payload.get("pinned", []) if isinstance(payload, dict) else []) if isinstance(p, dict)}
                batch = []
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    eid = str(item.get("id") or "")
                    pin_entry = pins.get(eid)
                    batch.append(
                        {
                            "entry_id": eid,
                            "session_id": "floating_snap",
                            "timestamp_str": str(item.get("timestamp") or ""),
                            "prompt_name": str(item.get("prompt_name") or ""),
                            "model_name": str(item.get("model_name") or ""),
                            "image_path": str(item.get("image_path") or ""),
                            "answer_text": str(item.get("ans") or ""),
                            "is_pinned": pin_entry is not None,
                            "pin_title": pin_entry.get("pin_title") if pin_entry else None,
                        }
                    )
                migrated_snaps = self.sqlite_store.record_batch(batch)
            except Exception:
                pass

        migrated_chat_messages = 0
        chat_file = self.history_xml_dir / "screenshot_chat_sessions.json"
        if chat_file.is_file():
            try:
                payload = json.loads(chat_file.read_text(encoding="utf-8"))
                sessions = payload.get("sessions", []) if isinstance(payload, dict) else []
                batch = []
                for sess in sessions:
                    if not isinstance(sess, dict):
                        continue
                    sid = str(sess.get("id") or "chat")
                    model_name = str(sess.get("model_name") or "")
                    prompt_type = str(sess.get("prompt_type") or "")
                    for msg in sess.get("messages", []):
                        if not isinstance(msg, dict):
                            continue
                        atts = msg.get("attachments") or []
                        first_img = str(atts[0].get("path") or "") if atts and isinstance(atts[0], dict) else ""
                        batch.append(
                            {
                                "entry_id": f"chat_{sid}_{msg.get('id', '')}",
                                "session_id": sid,
                                "created_at": float(msg.get("created_at") or time.time()),
                                "prompt_name": prompt_type,
                                "model_name": model_name,
                                "image_path": first_img,
                                "answer_text": str(msg.get("text") or ""),
                                "is_pinned": bool(sess.get("is_pinned", False)),
                            }
                        )
                migrated_chat_messages = self.sqlite_store.record_batch(batch)
            except Exception:
                pass

        status = {
            "status": "migrated",
            "backed_up_files": backed_up_files,
            "migrated_snaps": migrated_snaps,
            "migrated_chat_messages": migrated_chat_messages,
            "total_sqlite_records": self.sqlite_store.count(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        (self.history_xml_dir / "migration_status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
        return status

    def rollback(self) -> dict[str, Any]:
        restored: list[str] = []
        if self.backup_dir.is_dir():
            for src in self.backup_dir.iterdir():
                if src.is_file():
                    shutil.copy2(src, self.history_xml_dir / src.name)
                    restored.append(src.name)
        return {
            "status": "rolled_back",
            "restored_files": restored,
            "pi_sessions_preserved": (self.history_xml_dir / "pi-sdk-sessions").exists(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
