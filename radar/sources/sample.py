"""Load mentions from a local JSON file (testing and manual adds). Never touches the network.

File shape: either a list of items, or {"_note": "...", "mentions": [items]}.
Each item: url, title, snippet, keyword, received_at, optional author and source.
received_at is ISO 8601 (timezone-aware) or a relative offset like "-2h", "-30m", "-4d".
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..models import Mention

_OFFSET_RE = re.compile(r"^-\s*(\d+(?:\.\d+)?)\s*([mhd])$", re.IGNORECASE)
_UNITS = {"m": "minutes", "h": "hours", "d": "days"}


class SampleError(ValueError):
    pass


def parse_received_at(value: Any, now: datetime) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SampleError(f"received_at must be a string, got {value!r}")
    text = value.strip()
    match = _OFFSET_RE.match(text)
    if match:
        amount, unit = float(match.group(1)), match.group(2).lower()
        return now - timedelta(**{_UNITS[unit]: amount})
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SampleError(f"bad received_at {value!r}") from exc
    if dt.tzinfo is None:
        raise SampleError(f"received_at {value!r} has no timezone")
    return dt


def load_mentions(path: Path, now: datetime | None = None) -> list[Mention]:
    current = now or datetime.now(timezone.utc)
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SampleError(f"{path}: invalid JSON ({exc})") from exc
    items = data.get("mentions", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise SampleError(f"{path}: expected a list of mentions")
    mentions: list[Mention] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise SampleError(f"{path}: item {index} is not an object")
        try:
            mentions.append(
                Mention.from_url(
                    item["url"],
                    title=str(item.get("title", "")),
                    snippet=str(item.get("snippet", "")),
                    keyword=str(item.get("keyword", "")),
                    received_at=parse_received_at(item.get("received_at"), current),
                    author=item.get("author"),
                    source=str(item.get("source", "sample")),
                )
            )
        except KeyError as exc:
            raise SampleError(f"{path}: item {index} is missing {exc}") from exc
        except ValueError as exc:
            raise SampleError(f"{path}: item {index}: {exc}") from exc
    return mentions
