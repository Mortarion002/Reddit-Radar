from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from radar.cli import main
from radar.sources.sample import SampleError, load_mentions, parse_received_at

from .conftest import FIXTURES

SAMPLE = FIXTURES / "mentions_sample.json"


def _count(db, table):
    conn = sqlite3.connect(db)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


def test_sample_file_is_marked_synthetic():
    data = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert next(iter(data)) == "_note"
    assert data["_note"] == "synthetic test data"
    assert len(data["mentions"]) >= 25


def test_sample_loads_with_relative_times():
    now = datetime(2026, 11, 25, tzinfo=timezone.utc)
    mentions = load_mentions(SAMPLE, now=now)
    assert {m.kind for m in mentions} == {"post", "comment"}
    assert all(m.received_at <= now for m in mentions)
    assert parse_received_at("-2h", now) == now - timedelta(hours=2)
    assert parse_received_at("-30m", now) == now - timedelta(minutes=30)
    assert parse_received_at("2026-11-24T10:00:00+05:30", now).utcoffset() == timedelta(hours=5, minutes=30)


def test_bad_sample_raises(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text('[{"title": "no url"}]', encoding="utf-8")
    with pytest.raises(SampleError):
        load_mentions(bad)
    bad.write_text('[{"url": "https://example.com/x", "received_at": "-1h"}]', encoding="utf-8")
    with pytest.raises(SampleError):
        load_mentions(bad)


def test_import_twice_no_duplicates(settings, capsys):
    assert main(["import", "--file", str(SAMPLE)], settings=settings) == 0
    first = _count(settings.db_path, "mentions")
    assert first == _count(settings.db_path, "leads") >= 25
    assert main(["import", "--file", str(SAMPLE)], settings=settings) == 0
    assert _count(settings.db_path, "mentions") == first
    out = capsys.readouterr().out
    assert "0 new" in out.splitlines()[-1]
    assert _count(settings.db_path, "runs") == 2


def test_import_dry_run_writes_nothing(settings, capsys):
    assert main(["import", "--file", str(SAMPLE), "--dry-run"], settings=settings) == 0
    assert not settings.db_path.exists()
    assert "[dry-run]" in capsys.readouterr().out


def test_import_dry_run_on_existing_db(settings):
    assert main(["import", "--file", str(SAMPLE)], settings=settings) == 0
    before = _count(settings.db_path, "runs")
    assert main(["import", "--file", str(SAMPLE), "--dry-run"], settings=settings) == 0
    assert _count(settings.db_path, "runs") == before


def test_leads_and_rescore_commands(settings, capsys):
    main(["import", "--file", str(SAMPLE)], settings=settings)
    capsys.readouterr()
    assert main(["leads", "--tier", "High,Medium"], settings=settings) == 0
    out = capsys.readouterr().out
    assert "High" in out and "Low " not in out
    assert main(["rescore", "--days", "7"], settings=settings) == 0
    assert "0 changed" in capsys.readouterr().out
