from __future__ import annotations

import os
from datetime import timedelta

from radar.housekeeping import run_housekeeping

from .helpers import NOW


def _file(path, age_days: float):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    stamp = (NOW - timedelta(days=age_days)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_prunes_old_files_only(settings):
    old_lead = _file(settings.leads_dir / "2026-10-01_0900.json", 40)
    new_lead = _file(settings.leads_dir / "2026-11-20_0900.json", 5)
    latest = _file(settings.leads_dir / "latest.json", 60)
    old_draft = _file(settings.drafts_dir / "2026-10-01_0900.json", 31)
    new_draft = _file(settings.drafts_dir / "2026-11-24_0900.json", 1)
    old_eml = _file(settings.unparsed_dir / "14.eml", 15)
    new_eml = _file(settings.unparsed_dir / "15.eml", 13)

    result = run_housekeeping(settings, NOW)
    assert result["leads"] == 1 and result["drafts"] == 1 and result["unparsed"] == 1
    assert not old_lead.exists() and not old_draft.exists() and not old_eml.exists()
    assert new_lead.exists() and new_draft.exists() and new_eml.exists()
    assert latest.exists()  # never deleted


def test_dry_run_deletes_nothing(settings, capsys):
    old = _file(settings.leads_dir / "2026-10-01_0900.json", 40)
    result = run_housekeeping(settings, NOW, dry_run=True)
    assert result["leads"] == 1
    assert old.exists()
    assert "[dry-run] would delete" in capsys.readouterr().out


def test_missing_folders_are_fine(settings):
    assert run_housekeeping(settings, NOW) == {"leads": 0, "drafts": 0, "unparsed": 0, "task_log_rotated": 0}


def test_task_log_rotated(settings):
    log = settings.logs_dir / "tasks.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("x" * 1_100_000, encoding="utf-8")
    assert run_housekeeping(settings, NOW)["task_log_rotated"] == 1
    assert not log.exists()
    assert (settings.logs_dir / "tasks.log.1").exists()


def test_db_untouched(settings):
    from radar.store import Store

    with Store.open(settings.db_path) as store:
        store.set_state("k", "v")
    stamp = (NOW - timedelta(days=400)).timestamp()
    os.utime(settings.db_path, (stamp, stamp))
    run_housekeeping(settings, NOW)
    assert settings.db_path.exists()
