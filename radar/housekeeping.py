"""Housekeeping: prune old leads/drafts/unparsed files and rotate the task log. The DB keeps everything."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path

from .config import Settings

log = logging.getLogger("radar.housekeeping")

KEEP_LEADS_DAYS = 30
KEEP_DRAFTS_DAYS = 30
KEEP_UNPARSED_DAYS = 14
TASK_LOG_MAX_BYTES = 1_000_000


def _prune(folder: Path, pattern: str, older_than: datetime, keep: set[str], dry_run: bool) -> int:
    if not folder.exists():
        return 0
    removed = 0
    cutoff = older_than.timestamp()
    for path in folder.glob(pattern):
        if not path.is_file() or path.name in keep:
            continue
        if path.stat().st_mtime < cutoff:
            removed += 1
            if dry_run:
                print(f"[dry-run] would delete {path}")
            else:
                path.unlink()
    return removed


def rotate_task_log(settings: Settings, dry_run: bool = False) -> bool:
    """data/logs/tasks.log is appended by the PowerShell scripts; keep one old copy."""
    path = settings.logs_dir / "tasks.log"
    if not path.exists() or path.stat().st_size < TASK_LOG_MAX_BYTES:
        return False
    if dry_run:
        print(f"[dry-run] would rotate {path}")
        return True
    backup = path.with_suffix(".log.1")
    backup.unlink(missing_ok=True)
    path.rename(backup)
    return True


def run_housekeeping(settings: Settings, now: datetime, dry_run: bool = False) -> dict[str, int]:
    result = {
        "leads": _prune(settings.leads_dir, "*.json", now - timedelta(days=KEEP_LEADS_DAYS), {"latest.json"}, dry_run),
        "drafts": _prune(settings.drafts_dir, "*.json", now - timedelta(days=KEEP_DRAFTS_DAYS), set(), dry_run),
        "unparsed": _prune(settings.unparsed_dir, "*", now - timedelta(days=KEEP_UNPARSED_DAYS), set(), dry_run),
        "task_log_rotated": int(rotate_task_log(settings, dry_run)),
    }
    if any(result.values()):
        log.info("Housekeeping: %s", result)
    return result
