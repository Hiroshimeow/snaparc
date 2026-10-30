#!/usr/bin/env python3
"""Automated Performance, Memory, Capture Latency, 1,200+ History & Token Cost Benchmark Suite."""

from __future__ import annotations

import json
import os
from pathlib import Path
import resource
import shutil
import statistics
import sys
import tempfile
import time
from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.action_context import ActionContextBuilder  # noqa: E402
from src.core.dpi_capture import (  # noqa: E402
    DpiCoordinateEngine,
    MonitorDescriptor,
    VirtualScreenTopology,
    WindowMoveSafeCapturer,
)
from src.core.ocr_pipeline import HybridOcrEngine, ScreenshotComparator  # noqa: E402
from src.core.privacy_gate import PrivacyConsentGate  # noqa: E402
from src.core.secret_store import SecretStore  # noqa: E402
from src.core.skills_mcp import SkillRegistry  # noqa: E402
from src.core.sqlite_history import SQLiteHistoryStore  # noqa: E402
from src.utils.ConfigManager import ConfigManager  # noqa: E402


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return round(ordered[idx], 3)


def run_benchmarks() -> dict:
    temp_root = Path(tempfile.mkdtemp(prefix="stc_bench_"))
    try:
        # 1. Cold start measurement
        t0 = time.perf_counter()
        cfg = ConfigManager(str(temp_root / "stc.json"))
        secret_store = SecretStore(temp_root / "secrets.dpapi.json")
        consent_gate = PrivacyConsentGate()
        skills = SkillRegistry()
        db_path = temp_root / "stc_history_v2.sqlite3"
        history_store = SQLiteHistoryStore(db_path, thumbnails_dir=temp_root / "thumbs")
        cold_start_ms = round((time.perf_counter() - t0) * 1000.0, 3)

        # 2. Memory RSS
        ru = resource.getrusage(resource.RUSAGE_SELF)
        rss_mb = round(ru.ru_maxrss / 1024.0, 2)

        # 3. Multi-monitor High-DPI Capture & Crop Latency (50 iterations on 5760x2160 virtual desktop)
        topology = VirtualScreenTopology.detect(
            custom_monitors=[
                MonitorDescriptor("MON_LEFT", -1920, 0, 0, 1080, dpi_x=96, dpi_y=96, is_primary=False),
                MonitorDescriptor("MON_PRIMARY_4K", 0, 0, 3840, 2160, dpi_x=144, dpi_y=144, is_primary=True),
            ]
        )
        synthetic_desktop = Image.new("RGB", (5760, 2160), color=(24, 32, 48))
        draw = ImageDraw.Draw(synthetic_desktop)
        draw.rectangle((1920 + 200, 200, 1920 + 1200, 800), fill=(245, 245, 245))
        draw.line((1920 + 240, 260, 1920 + 900, 260), fill=(15, 23, 42), width=6)
        draw.line((1920 + 240, 320, 1920 + 1050, 320), fill=(15, 23, 42), width=6)

        capturer = WindowMoveSafeCapturer(
            str(temp_root / "snaps"),
            topology_provider=lambda: topology,
        )
        capture_latencies_ms = []
        last_frame = None
        for i in range(40):
            c_start = time.perf_counter()
            last_frame = capturer.capture_region_at_action_time(
                left=-400 + (i * 5),
                top=120,
                right=800 + (i * 5),
                bottom=620,
                timestamp_str=f"bench_{i:03d}",
                save_to_disk=(i < 5),
                full_screen_image=synthetic_desktop,
            )
            capture_latencies_ms.append((time.perf_counter() - c_start) * 1000.0)
        synthetic_desktop.close()

        # 4. Offline OCR & Screenshot Diff Latency
        ocr_engine = HybridOcrEngine()
        ocr_latencies_ms = []
        sample_img = last_frame.to_pil()
        for _ in range(15):
            o_start = time.perf_counter()
            ocr_res = ocr_engine.extract_text(sample_img, mode="offline")
            ocr_latencies_ms.append((time.perf_counter() - o_start) * 1000.0)

        sample_img_b = sample_img.copy()
        draw_b = ImageDraw.Draw(sample_img_b)
        draw_b.rectangle((40, 40, 180, 110), fill=(220, 38, 38))
        comp = ScreenshotComparator(ocr_engine)
        t_diff0 = time.perf_counter()
        diff_res = comp.compare(sample_img, sample_img_b)
        diff_latency_ms = round((time.perf_counter() - t_diff0) * 1000.0, 3)
        sample_img.close()
        sample_img_b.close()

        # 5. 1,200+ History Events Insert, FTS5 Search & Pagination Latency
        batch_events = [
            {
                "entry_id": f"bench_evt_{idx:04d}",
                "session_id": f"sess_{idx % 12}",
                "timestamp_str": f"20261021_{idx:06d}",
                "created_at": time.time() - (1200 - idx),
                "prompt_name": "Trans" if idx % 2 == 0 else "Expert",
                "model_name": "provider/example-model",
                "image_path": last_frame.saved_path if idx == 0 else "",
                "prompt_text": f"Analyze screenshot region #{idx} for multi-monitor DPI scaling",
                "answer_text": (
                    f"Translated result #{idx}: Hệ thống trợ lý đa phương thức Windows xử lý "
                    f"chính xác vùng chụp màn hình {idx} với keyword_target_{idx % 50}."
                ),
                "is_pinned": (idx % 100 == 0),
                "token_count": 380,
                "cost_usd": 0.00012,
            }
            for idx in range(1200)
        ]
        t_ins0 = time.perf_counter()
        inserted_count = history_store.record_batch(batch_events)
        batch_insert_1200_ms = round((time.perf_counter() - t_ins0) * 1000.0, 3)

        search_latencies_ms = []
        for q_idx in range(30):
            t_s0 = time.perf_counter()
            hits = history_store.search(f"keyword_target_{q_idx}", limit=25)
            search_latencies_ms.append((time.perf_counter() - t_s0) * 1000.0)
            assert len(hits) >= 20

        page_latencies_ms = []
        for offset in (0, 100, 500, 1000):
            t_p0 = time.perf_counter()
            page = history_store.search("", limit=50, offset=offset)
            page_latencies_ms.append((time.perf_counter() - t_p0) * 1000.0)
            assert len(page) == 50

        db_size_kb = round(db_path.stat().st_size / 1024.0, 2)

        history_store.close()

        return {
            "environment": {
                "platform": sys.platform,
                "python_version": sys.version.split()[0],
                "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
            "cold_start_ms": cold_start_ms,
            "memory_rss_mb": rss_mb,
            "capture_crop_5760x2160": {
                "iterations": len(capture_latencies_ms),
                "p50_ms": percentile(capture_latencies_ms, 50),
                "p95_ms": percentile(capture_latencies_ms, 95),
                "mean_ms": round(statistics.mean(capture_latencies_ms), 3),
            },
            "offline_ocr": {
                "iterations": len(ocr_latencies_ms),
                "p50_ms": percentile(ocr_latencies_ms, 50),
                "p95_ms": percentile(ocr_latencies_ms, 95),
                "engine_used": ocr_res.engine_used,
            },
            "screenshot_diff_compare_ms": diff_latency_ms,
            "changed_pixel_ratio": diff_res.changed_pixel_ratio,
            "sqlite_fts5_1200_events": {
                "events_indexed": inserted_count,
                "batch_insert_1200_ms": batch_insert_1200_ms,
                "fts5_search_p50_ms": percentile(search_latencies_ms, 50),
                "fts5_search_p95_ms": percentile(search_latencies_ms, 95),
                "pagination_p50_ms": percentile(page_latencies_ms, 50),
                "db_size_kb": db_size_kb,
            },
        }
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


if __name__ == "__main__":
    report = run_benchmarks()
    print(json.dumps(report, indent=2, ensure_ascii=False))
