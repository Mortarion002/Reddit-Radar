"""Telegram message formatting: hot alerts, digest, run summary (spec section 9)."""

from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime, tzinfo

from .models import Lead

DIGEST_CAP = 15
SNIPPET_MAX = 400
STATUS_BUTTONS = (("Replied", "replied"), ("Skip", "skipped"), ("Not relevant", "not_relevant"))
TIER_SHORT = {"High": "HIGH", "Medium": "MED", "Low": "LOW", "Dropped": "DROP"}


@dataclass
class RunSummary:
    command: str = "scan"
    emails_read: int = 0
    mentions_found: int = 0
    new_mentions: int = 0
    tiers: dict[str, int] = field(default_factory=dict)
    alerts_sent: int = 0
    parse_errors: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def high(self) -> int:
        return self.tiers.get("High", 0)

    @property
    def medium(self) -> int:
        return self.tiers.get("Medium", 0)


def esc(text: str | None) -> str:
    return html.escape(text or "", quote=False)


def age_label(hours: float) -> str:
    if hours < 1:
        return f"{max(0, int(hours * 60))}m ago"
    if hours < 48:
        return f"{int(hours)}h ago"
    return f"{int(hours // 24)}d ago"


def lead_age_hours(lead: Lead, now: datetime) -> float:
    return max(0.0, (now - lead.mention.received_at).total_seconds() / 3600)


def lead_header(lead: Lead, now: datetime) -> str:
    m = lead.mention
    return (
        f"{TIER_SHORT.get(lead.tier, lead.tier.upper())} {lead.score:.0f} | {lead.primary_signal} | "
        f"r/{m.subreddit} | {m.kind} | {age_label(lead_age_hours(lead, now))}"
    )


def callback_data(status: str, short: str) -> str:
    data = f"s:{status}:{short}"
    if len(data.encode("utf-8")) > 64:
        raise ValueError("callback data over 64 bytes")
    return data


def status_buttons(short: str, chosen: str | None = None) -> list[list[tuple[str, str]]]:
    row = []
    for label, status in STATUS_BUTTONS:
        text = f"* {label}" if status == chosen else label
        row.append((text, callback_data(status, short)))
    return [row]


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SNIPPET_MAX else text[: SNIPPET_MAX - 3].rstrip() + "..."


def format_alert(lead: Lead, now: datetime) -> str:
    m = lead.mention
    lines = [f"<b>{esc(lead_header(lead, now))}</b>", f"Thread: {esc(m.title)}", ""]
    if m.snippet:
        lines += [f'"{esc(_snippet(m.snippet))}"', ""]
    matched = ", ".join(lead.matched_terms) or "-"
    lines.append(f"Keyword: {esc(m.keyword or '-')} | Matched: {esc(matched)}")
    lines.append(esc(m.url))
    return "\n".join(lines)


def format_summary(summary: RunSummary, now: datetime, tz: tzinfo) -> str:
    tiers = summary.tiers
    tier_text = ", ".join(f"{t} {tiers.get(t, 0)}" for t in ("High", "Medium", "Low", "Dropped"))
    lines = [
        f"<b>Reddit Radar {esc(summary.command)}</b> | {now.astimezone(tz).strftime('%d %b %H:%M')}",
        f"Emails {summary.emails_read} | mentions {summary.mentions_found} | new {summary.new_mentions}",
        f"New leads: {tier_text}",
    ]
    if summary.alerts_sent:
        lines.append(f"Hot alerts sent: {summary.alerts_sent}")
    if summary.parse_errors:
        lines.append(f"Parse errors: {summary.parse_errors} (saved to data/unparsed/)")
    return "\n".join(lines)


def format_digest(summary: RunSummary, leads: list[Lead], now: datetime, tz: tzinfo, cap: int = DIGEST_CAP) -> str:
    parts = [format_summary(summary, now, tz)]
    if leads:
        parts.append("")
        parts.append("<b>Leads to look at</b>")
        for index, lead in enumerate(leads[:cap], start=1):
            parts.append(f"{index}. {esc(lead_header(lead, now))}")
            parts.append(esc(lead.mention.title or _snippet(lead.mention.snippet)))
            parts.append(esc(lead.mention.url))
        if len(leads) > cap:
            parts.append(f"+{len(leads) - cap} more: python -m radar leads")
    return "\n".join(parts)
