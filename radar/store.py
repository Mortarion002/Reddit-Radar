"""SQLite storage at data/radar.db. The DB is the source of truth."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .models import Lead, Mention, short_id

# Each entry upgrades the schema by one version. Never edit a shipped entry; append.
MIGRATIONS: list[str] = [
    # v1: spec section 7 (+ leads.drop_reason)
    """
    CREATE TABLE mentions(
        mention_id TEXT PRIMARY KEY, kind TEXT, post_id TEXT, subreddit TEXT, title TEXT,
        snippet TEXT, url TEXT, author TEXT, keyword TEXT, received_at TEXT,
        first_seen TEXT, source TEXT, email_uid TEXT
    );
    CREATE INDEX idx_mentions_post ON mentions(post_id);
    CREATE INDEX idx_mentions_received ON mentions(received_at);
    CREATE TABLE leads(
        mention_id TEXT PRIMARY KEY REFERENCES mentions(mention_id),
        primary_signal TEXT, signals TEXT, matched_terms TEXT,
        competitor TEXT, score REAL, tier TEXT, scored_at TEXT,
        alerted_at TEXT, digested_at TEXT, exported_at TEXT,
        status TEXT DEFAULT 'new',
        status_at TEXT,
        drop_reason TEXT
    );
    CREATE INDEX idx_leads_tier ON leads(tier);
    CREATE TABLE drafts(
        id INTEGER PRIMARY KEY AUTOINCREMENT, mention_id TEXT, created_at TEXT,
        mentions_elvan INTEGER, draft_text TEXT, notes TEXT, sent_at TEXT, posted INTEGER DEFAULT 0
    );
    CREATE TABLE processed_emails(
        uid TEXT PRIMARY KEY, message_id TEXT, received_at TEXT, mentions_found INTEGER, status TEXT
    );
    CREATE TABLE runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT, command TEXT, started_at TEXT, finished_at TEXT,
        emails_read INTEGER, mentions_found INTEGER, new_mentions INTEGER,
        leads_high INTEGER, leads_medium INTEGER, errors TEXT
    );
    CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT);
    """,
    # v2 (stage 3): short ids for Telegram callback data
    """
    CREATE TABLE short_ids(short_id TEXT PRIMARY KEY, mention_id TEXT NOT NULL);
    """,
    # v3 (stage 5): which Telegram message carried each draft, so a "Replied" tap on it marks it posted
    """
    ALTER TABLE drafts ADD COLUMN message_id INTEGER;
    CREATE INDEX idx_drafts_mention ON drafts(mention_id);
    """,
]

LEAD_STATUSES = ("new", "replied", "skipped", "not_relevant")


def iso(dt: datetime | None) -> str | None:
    """UTC ISO string, so text comparison in SQL orders correctly."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        raise ValueError("naive datetime")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Store:
    def __init__(self, conn: sqlite3.Connection, dry_run: bool = False) -> None:
        self.conn = conn
        self.dry_run = dry_run
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    # ---- lifecycle ---------------------------------------------------------

    @classmethod
    def open(cls, path: Path, dry_run: bool = False) -> "Store":
        """Open the DB. In dry-run mode the DB is copied into memory, so nothing is written."""
        if dry_run:
            mem = sqlite3.connect(":memory:")
            if path.exists():
                src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
                try:
                    src.backup(mem)
                finally:
                    src.close()
            return cls(mem, dry_run=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        return cls(sqlite3.connect(str(path), timeout=30), dry_run=False)

    @classmethod
    def memory(cls) -> "Store":
        return cls(sqlite3.connect(":memory:"))

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _migrate(self) -> None:
        has_state = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='state'"
        ).fetchone()
        version = 0
        if has_state:
            row = self.conn.execute("SELECT value FROM state WHERE key='schema_version'").fetchone()
            version = int(row[0]) if row else 0
        for number, script in enumerate(MIGRATIONS, start=1):
            if number <= version:
                continue
            with self.conn:
                for statement in _split_sql(script):
                    self.conn.execute(statement)
                self.conn.execute(
                    "INSERT INTO state(key, value) VALUES('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(number),),
                )
        if 0 < version < 2:  # leads stored before short_ids existed
            with self.conn:
                ids = [r[0] for r in self.conn.execute("SELECT mention_id FROM leads")]
                self.conn.executemany(
                    "INSERT OR IGNORE INTO short_ids(short_id, mention_id) VALUES(?, ?)",
                    [(short_id(mid), mid) for mid in ids],
                )

    @property
    def schema_version(self) -> int:
        return int(self.get_state("schema_version", "0") or 0)

    # ---- state -------------------------------------------------------------

    def get_state(self, key: str, default: str | None = None) -> str | None:
        row = self.conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set_state(self, key: str, value: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO state(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    # ---- mentions ----------------------------------------------------------

    def upsert_mentions(
        self,
        mentions: Iterable[Mention],
        email_uids: Mapping[str, str] | None = None,
        now: datetime | None = None,
    ) -> list[Mention]:
        """Insert mentions not seen before. Returns only the new ones, in input order."""
        seen_at = iso(now or utcnow())
        new: list[Mention] = []
        with self.conn:
            for m in mentions:
                cur = self.conn.execute(
                    "INSERT OR IGNORE INTO mentions(mention_id, kind, post_id, subreddit, title, snippet, url,"
                    " author, keyword, received_at, first_seen, source, email_uid)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        m.mention_id, m.kind, m.post_id, m.subreddit, m.title, m.snippet, m.url,
                        m.author, m.keyword, iso(m.received_at), seen_at, m.source,
                        (email_uids or {}).get(m.mention_id),
                    ),
                )
                if cur.rowcount:
                    new.append(m)
        return new

    def get_mention(self, mention_id: str) -> Mention | None:
        row = self.conn.execute("SELECT * FROM mentions WHERE mention_id=?", (mention_id,)).fetchone()
        return _row_to_mention(row) if row else None

    def get_mentions(self, since: datetime | None = None) -> list[tuple[Mention, datetime]]:
        """Mentions received since `since`, each with its first_seen time."""
        sql, params = "SELECT * FROM mentions", []
        if since is not None:
            sql += " WHERE received_at >= ?"
            params.append(iso(since))
        rows = self.conn.execute(sql + " ORDER BY received_at", params).fetchall()
        return [(_row_to_mention(r), parse_dt(r["first_seen"]) or utcnow()) for r in rows]

    # ---- leads -------------------------------------------------------------

    def save_lead(self, lead: Lead) -> None:
        """Insert a lead, or update its scoring fields. Status and delivery times are kept."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO leads(mention_id, primary_signal, signals, matched_terms, competitor, score, tier,"
                " scored_at, drop_reason, status) VALUES(?,?,?,?,?,?,?,?,?, 'new')"
                " ON CONFLICT(mention_id) DO UPDATE SET primary_signal=excluded.primary_signal,"
                " signals=excluded.signals, matched_terms=excluded.matched_terms, competitor=excluded.competitor,"
                " score=excluded.score, tier=excluded.tier, scored_at=excluded.scored_at,"
                " drop_reason=excluded.drop_reason",
                (
                    lead.mention_id, lead.primary_signal, json.dumps(lead.signals), json.dumps(lead.matched_terms),
                    lead.competitor, lead.score, lead.tier, iso(lead.scored_at), lead.drop_reason,
                ),
            )
            self.conn.execute(
                "INSERT OR IGNORE INTO short_ids(short_id, mention_id) VALUES(?, ?)",
                (lead.short_id, lead.mention_id),
            )

    def get_lead(self, mention_id: str) -> Lead | None:
        rows = self._lead_query("WHERE l.mention_id = ?", [mention_id])
        return rows[0] if rows else None

    def get_leads(
        self,
        tiers: Sequence[str] | None = None,
        since: datetime | None = None,
        status: str | None = None,
    ) -> list[Lead]:
        clauses: list[str] = []
        params: list[Any] = []
        if tiers:
            clauses.append(f"l.tier IN ({','.join('?' * len(tiers))})")
            params.extend(tiers)
        if since is not None:
            clauses.append("m.received_at >= ?")
            params.append(iso(since))
        if status is not None:
            clauses.append("l.status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        return self._lead_query(where + " ORDER BY l.score DESC, m.received_at DESC", params)

    def _lead_query(self, tail: str, params: Sequence[Any]) -> list[Lead]:
        rows = self.conn.execute(
            "SELECT m.*, l.primary_signal, l.signals, l.matched_terms, l.competitor, l.score, l.tier,"
            " l.scored_at, l.drop_reason, l.alerted_at, l.digested_at, l.exported_at, l.status, l.status_at"
            " FROM leads l JOIN mentions m ON m.mention_id = l.mention_id " + tail,
            list(params),
        ).fetchall()
        return [_row_to_lead(r) for r in rows]

    def set_status(self, mention_id: str, status: str, at: datetime | None = None) -> bool:
        if status not in LEAD_STATUSES:
            raise ValueError(f"unknown status {status!r}")
        with self.conn:
            cur = self.conn.execute(
                "UPDATE leads SET status=?, status_at=? WHERE mention_id=?",
                (status, iso(at or utcnow()), mention_id),
            )
        return cur.rowcount > 0

    def mark(self, column: str, mention_ids: Iterable[str], at: datetime | None = None) -> None:
        if column not in ("alerted_at", "digested_at", "exported_at"):
            raise ValueError(column)
        stamp = iso(at or utcnow())
        with self.conn:
            self.conn.executemany(
                f"UPDATE leads SET {column}=? WHERE mention_id=?", [(stamp, mid) for mid in mention_ids]
            )

    def resolve_short_id(self, short: str) -> str | None:
        row = self.conn.execute("SELECT mention_id FROM short_ids WHERE short_id=?", (short,)).fetchone()
        return row[0] if row else None

    def thread_alerted_since(self, post_id: str, since: datetime, exclude: str | None = None) -> bool:
        """True if any lead in this thread was hot-alerted at or after `since`."""
        row = self.conn.execute(
            "SELECT 1 FROM leads l JOIN mentions m ON m.mention_id = l.mention_id"
            " WHERE m.post_id = ? AND l.alerted_at IS NOT NULL AND l.alerted_at >= ? AND l.mention_id != ?"
            " LIMIT 1",
            (post_id, iso(since), exclude or ""),
        ).fetchone()
        return row is not None

    def undigested_leads(self, since: datetime) -> list[Lead]:
        """High and Medium leads received since `since` that no digest has covered yet."""
        return self._lead_query(
            "WHERE l.tier IN ('High', 'Medium') AND l.digested_at IS NULL AND m.received_at >= ?"
            " ORDER BY l.score DESC, m.received_at DESC",
            [iso(since)],
        )

    def exportable_leads(self, since: datetime, limit: int) -> list[Lead]:
        """High and Medium leads with status new, no draft yet, received since `since`."""
        return self._lead_query(
            "WHERE l.tier IN ('High', 'Medium') AND l.status = 'new' AND m.received_at >= ?"
            " AND NOT EXISTS (SELECT 1 FROM drafts d WHERE d.mention_id = l.mention_id)"
            " ORDER BY l.score DESC, m.received_at DESC LIMIT ?",
            [iso(since), limit],
        )

    def has_draft(self, mention_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM drafts WHERE mention_id=? LIMIT 1", (mention_id,)).fetchone() is not None

    def save_draft(
        self,
        mention_id: str,
        mentions_elvan: bool,
        draft_text: str,
        notes: str,
        created_at: datetime,
        sent_at: datetime | None,
        message_id: int | None,
    ) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO drafts(mention_id, created_at, mentions_elvan, draft_text, notes, sent_at, posted, message_id)"
                " VALUES(?,?,?,?,?,?,0,?)",
                (mention_id, iso(created_at), int(mentions_elvan), draft_text, notes, iso(sent_at), message_id),
            )
        return int(cur.lastrowid)

    def reply_stats(self, since: datetime) -> tuple[int, int]:
        """(replies, of which named Elvan) since `since`, from Aman's Telegram taps.

        A reply is a lead marked "replied". An Elvan mention is a posted draft (Replied tapped on
        the draft message) that names Elvan.
        """
        replies = self.conn.execute(
            "SELECT COUNT(*) FROM leads WHERE status = 'replied' AND status_at >= ?", (iso(since),)
        ).fetchone()[0]
        mentions = self.conn.execute(
            "SELECT COUNT(DISTINCT d.mention_id) FROM drafts d JOIN leads l ON l.mention_id = d.mention_id"
            " WHERE d.posted = 1 AND d.mentions_elvan = 1 AND l.status = 'replied' AND l.status_at >= ?",
            (iso(since),),
        ).fetchone()[0]
        return int(replies), int(mentions)

    def set_draft_posted(self, message_id: int, posted: bool) -> int:
        """Mark the draft carried by Telegram message `message_id` as posted (or not). Returns rows changed."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE drafts SET posted = ? WHERE message_id = ?", (int(posted), message_id)
            )
        return cur.rowcount

    def set_latest_draft_posted(self, mention_id: str, posted: bool) -> int:
        """Fallback when the draft message was resent from delivery_failures and has no stored message_id."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE drafts SET posted = ? WHERE id = (SELECT MAX(id) FROM drafts WHERE mention_id = ?)",
                (int(posted), mention_id),
            )
        return cur.rowcount

    # ---- processed emails ----------------------------------------------------

    def processed_uids(self, uids: Iterable[str]) -> set[str]:
        wanted = list(uids)
        done: set[str] = set()
        for i in range(0, len(wanted), 500):
            chunk = wanted[i : i + 500]
            rows = self.conn.execute(
                f"SELECT uid FROM processed_emails WHERE uid IN ({','.join('?' * len(chunk))})", chunk
            )
            done.update(r[0] for r in rows)
        return done

    def record_email(
        self, uid: str, message_id: str, received_at: datetime | None, mentions_found: int, status: str
    ) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO processed_emails(uid, message_id, received_at, mentions_found, status)"
                " VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET message_id=excluded.message_id,"
                " received_at=excluded.received_at, mentions_found=excluded.mentions_found, status=excluded.status",
                (uid, message_id, iso(received_at), mentions_found, status),
            )

    def last_processed_email_at(self) -> datetime | None:
        """Newest Date of an IMAP email we processed (files from ingest-eml don't count)."""
        row = self.conn.execute(
            "SELECT MAX(received_at) FROM processed_emails WHERE uid NOT LIKE 'file:%'"
        ).fetchone()
        return parse_dt(row[0]) if row and row[0] else None

    def tier_counts(self, mention_ids: Iterable[str]) -> dict[str, int]:
        ids = list(mention_ids)
        counts: dict[str, int] = {}
        for i in range(0, len(ids), 500):
            chunk = ids[i : i + 500]
            for row in self.conn.execute(
                f"SELECT tier, COUNT(*) FROM leads WHERE mention_id IN ({','.join('?' * len(chunk))}) GROUP BY tier",
                chunk,
            ):
                counts[row[0]] = counts.get(row[0], 0) + row[1]
        return counts

    # ---- runs --------------------------------------------------------------

    def start_run(self, command: str, now: datetime | None = None) -> int:
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO runs(command, started_at) VALUES(?, ?)", (command, iso(now or utcnow()))
            )
        return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        *,
        emails_read: int = 0,
        mentions_found: int = 0,
        new_mentions: int = 0,
        leads_high: int = 0,
        leads_medium: int = 0,
        errors: Sequence[str] = (),
        now: datetime | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE runs SET finished_at=?, emails_read=?, mentions_found=?, new_mentions=?,"
                " leads_high=?, leads_medium=?, errors=? WHERE id=?",
                (
                    iso(now or utcnow()), emails_read, mentions_found, new_mentions,
                    leads_high, leads_medium, json.dumps(list(errors)), run_id,
                ),
            )

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()


# ---- helpers -----------------------------------------------------------------


def _split_sql(script: str) -> list[str]:
    return [s.strip() for s in script.split(";") if s.strip()]


def _row_to_mention(row: sqlite3.Row) -> Mention:
    return Mention(
        mention_id=row["mention_id"],
        kind=row["kind"],
        post_id=row["post_id"],
        subreddit=row["subreddit"],
        title=row["title"] or "",
        snippet=row["snippet"] or "",
        url=row["url"],
        author=row["author"],
        keyword=row["keyword"] or "",
        received_at=parse_dt(row["received_at"]) or utcnow(),
        source=row["source"] or "f5bot",
    )


def _row_to_lead(row: sqlite3.Row) -> Lead:
    return Lead(
        mention=_row_to_mention(row),
        primary_signal=row["primary_signal"],
        signals=json.loads(row["signals"] or "[]"),
        matched_terms=json.loads(row["matched_terms"] or "[]"),
        competitor=row["competitor"],
        score=float(row["score"] or 0),
        tier=row["tier"],
        scored_at=parse_dt(row["scored_at"]) or utcnow(),
        drop_reason=row["drop_reason"],
        alerted_at=parse_dt(row["alerted_at"]),
        digested_at=parse_dt(row["digested_at"]),
        exported_at=parse_dt(row["exported_at"]),
        status=row["status"] or "new",
        status_at=parse_dt(row["status_at"]),
    )
