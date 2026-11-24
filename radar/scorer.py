"""Signal classification and scoring (spec section 6). Pure functions, no IO."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from typing import Any, Iterable, Mapping

from .config import Settings
from .models import Lead, Mention

BASE_SCORES: dict[str, int] = {
    "brand_mention": 50,
    "tool_request": 40,
    "competitor_pain": 40,
    "alternative_search": 38,
    "integration_need": 30,
    "delighted_fallout": 30,
    "pmf_founder": 25,
    "feedback_pain": 20,
    "competitor_mention": 12,
    "none": 0,
}
SIGNAL_ORDER = list(BASE_SCORES)  # tie-break: table order in the spec

TITLE_BONUS = 15
SNIPPET_BONUS = 8
QUESTION_BONUS = 10
SECONDARY_EACH = 5
SECONDARY_MAX = 10
POST_BONUS = 5
GENERIC_PENALTY = 15
ENTERPRISE_PENALTY = 10

DROPPED = "Dropped"


# ---- text matching -------------------------------------------------------------


def normalize(text: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace. 'SurveySparrow's' -> 'surveysparrow s'."""
    lowered = text.lower().replace("’", "'").replace("‘", "'")
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


@lru_cache(maxsize=4096)
def _phrase_pattern(phrase: str) -> re.Pattern[str] | None:
    tokens = normalize(phrase).split()
    if not tokens:
        return None
    head = r"\s+".join(re.escape(t) for t in tokens[:-1])
    last = re.escape(tokens[-1]) + r"(?:s|es)?"
    body = (head + r"\s+" + last) if head else last
    return re.compile(rf"\b{body}\b")


def contains(text: str, phrase: str) -> bool:
    """Word-boundary match of `phrase` in already-normalized `text`; the last word may be plural."""
    if not text:
        return False
    pattern = _phrase_pattern(phrase)
    return bool(pattern and pattern.search(text))


def _stem(phrase: str) -> str:
    norm = normalize(phrase)
    return norm[:-1] if norm.endswith("s") and len(norm) > 3 else norm


def find(text: str, phrases: Iterable[str]) -> list[str]:
    """Phrases found in normalized text. Spelling/plural variants of one phrase are reported once."""
    out: list[str] = []
    stems: set[str] = set()
    for phrase in phrases:
        stem = _stem(phrase)
        if stem not in stems and contains(text, phrase):
            out.append(phrase)
            stems.add(stem)
    return out


# ---- config ----------------------------------------------------------------------


def _strs(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(str(v) for v in value)


@dataclass(frozen=True)
class ScoringConfig:
    priority_competitors: tuple[str, ...] = ()
    tracked_competitors: tuple[str, ...] = ()
    ambiguous_competitors: tuple[str, ...] = ()
    brand: tuple[str, ...] = ()
    product_terms: tuple[str, ...] = ()
    generic_terms: tuple[str, ...] = ()
    integrations: tuple[str, ...] = ()
    request_phrases: tuple[str, ...] = ()
    switch_phrases: tuple[str, ...] = ()
    pain_phrases: tuple[str, ...] = ()
    feedback_pain_phrases: tuple[str, ...] = ()
    delighted_fallout_phrases: tuple[str, ...] = ()
    pmf_phrases: tuple[str, ...] = ()
    promo_phrases: tuple[str, ...] = ()
    job_phrases: tuple[str, ...] = ()
    msp_terms: tuple[str, ...] = ()
    staffing_terms: tuple[str, ...] = ()
    enterprise_terms: tuple[str, ...] = ()
    question_starts: tuple[str, ...] = ("how", "what", "which", "any", "is there")
    subreddit_bonus: Mapping[str, float] = field(default_factory=dict)
    blocklist: frozenset[str] = frozenset()
    high_threshold: int = 70
    medium_threshold: int = 45
    max_age_hours: int = 72

    @classmethod
    def from_dicts(
        cls,
        signals: Mapping[str, Any],
        subreddits: Mapping[str, Any],
        high: int = 70,
        medium: int = 45,
        max_age_hours: int = 72,
    ) -> "ScoringConfig":
        comps = signals.get("competitors") or {}
        keys = (
            "brand product_terms generic_terms integrations request_phrases switch_phrases pain_phrases "
            "feedback_pain_phrases delighted_fallout_phrases pmf_phrases promo_phrases job_phrases msp_terms "
            "staffing_terms enterprise_terms ambiguous_competitors"
        ).split()
        lists = {k: _strs(signals.get(k)) for k in keys}
        qs = _strs(signals.get("question_starts")) or ("how", "what", "which", "any", "is there")
        bonus = {str(k).lower(): float(v) for k, v in (subreddits.get("bonus") or {}).items()}
        block = frozenset(str(s).lower().removeprefix("r/") for s in (subreddits.get("blocklist") or []))
        return cls(
            priority_competitors=_strs(comps.get("priority")),
            tracked_competitors=_strs(comps.get("tracked")),
            question_starts=qs,
            subreddit_bonus=bonus,
            blocklist=block,
            high_threshold=high,
            medium_threshold=medium,
            max_age_hours=max_age_hours,
            **lists,
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "ScoringConfig":
        return cls.from_dicts(
            settings.signals,
            settings.subreddits,
            settings.high_threshold,
            settings.medium_threshold,
            settings.max_lead_age_hours,
        )


# ---- classification ------------------------------------------------------------


@dataclass(slots=True)
class Classification:
    primary: str
    signals: list[str]  # ordered by base score, primary first
    matched_terms: list[str]
    competitor: str | None
    terms_by_signal: dict[str, list[str]] = field(default_factory=dict)
    key_terms: dict[str, list[str]] = field(default_factory=dict)  # the phrases that triggered each signal
    has_specific_context: bool = False


def _dedupe(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item not in out:
            out.append(item)
    return out


def classify(mention: Mention, cfg: ScoringConfig) -> Classification:
    text = normalize(mention.text)
    brand = find(text, cfg.brand)
    product = find(text, cfg.product_terms)
    generic = find(text, cfg.generic_terms)
    fallout = find(text, cfg.delighted_fallout_phrases)
    pmf = find(text, cfg.pmf_phrases)

    ambiguous = {normalize(a) for a in cfg.ambiguous_competitors}
    ambiguity_ok = bool(product or generic or fallout)
    priority = [c for c in find(text, cfg.priority_competitors) if normalize(c) not in ambiguous or ambiguity_ok]
    tracked = [c for c in find(text, cfg.tracked_competitors) if normalize(c) not in ambiguous or ambiguity_ok]
    competitors = priority + tracked
    tool_context = product or generic or competitors

    integ = find(text, cfg.integrations)
    req = find(text, cfg.request_phrases)
    switch = find(text, cfg.switch_phrases)
    pain = find(text, cfg.pain_phrases)
    fpain = find(text, cfg.feedback_pain_phrases)

    # signal -> (key terms that trigger it, supporting context terms)
    found: dict[str, tuple[list[str], list[str]]] = {}
    if brand:
        found["brand_mention"] = (brand, [])
    if req and tool_context:
        found["tool_request"] = (req, product or competitors or generic)
    if competitors and pain:
        found["competitor_pain"] = (competitors + pain, [])
    if switch and (competitors or product):
        found["alternative_search"] = (switch + competitors, [] if competitors else product)
    if integ and (product or generic):
        found["integration_need"] = (integ, product or generic)
    delighted_named = any(normalize(c) == "delighted" for c in competitors)
    if delighted_named and fallout:
        found["delighted_fallout"] = (["delighted"] + fallout, [])
    if pmf:
        found["pmf_founder"] = (pmf, [])
    if fpain and (product or generic):
        found["feedback_pain"] = (fpain, product or generic)
    if competitors and not ({"competitor_pain", "alternative_search", "delighted_fallout"} & found.keys()):
        found["competitor_mention"] = (competitors, [])

    ordered = sorted(found, key=lambda s: (-BASE_SCORES[s], SIGNAL_ORDER.index(s)))
    primary = ordered[0] if ordered else "none"
    return Classification(
        primary=primary,
        signals=ordered,
        matched_terms=_dedupe(t for s in ordered for t in found[s][0] + found[s][1]),
        competitor=(priority or tracked or [None])[0],
        terms_by_signal={s: found[s][0] + found[s][1] for s in ordered},
        key_terms={s: found[s][0] for s in ordered},
        has_specific_context=bool(product or competitors or brand or pmf),
    )


# ---- scoring -----------------------------------------------------------------------


@dataclass(slots=True)
class ScoreResult:
    score: float
    tier: str
    drop_reason: str | None = None


def age_hours(mention: Mention, now: datetime) -> float:
    return (now - mention.received_at).total_seconds() / 3600


def recency_bonus(hours: float) -> int:
    if hours < 2:
        return 12
    if hours < 12:
        return 8
    if hours < 24:
        return 4
    return 0


def asks_question(mention: Mention, cfg: ScoringConfig) -> bool:
    if "?" in mention.text:
        return True
    for part in (normalize(mention.title), normalize(mention.snippet)):
        for start in cfg.question_starts:
            word = normalize(start)
            if word and (part == word or part.startswith(word + " ")):
                return True
    return False


def drop_reason(mention: Mention, cls: Classification, cfg: ScoringConfig, now: datetime) -> str | None:
    text = normalize(mention.text)
    if age_hours(mention, now) > cfg.max_age_hours:
        return "too_old"
    if find(text, cfg.promo_phrases):
        return "promotional"
    if find(text, cfg.job_phrases):
        return "job_post"
    if cls.primary == "brand_mention":
        return None
    if find(text, cfg.msp_terms):
        return "msp"
    if find(text, cfg.staffing_terms):
        return "staffing"
    if mention.subreddit.lower() in cfg.blocklist:
        return "blocked_subreddit"
    return None


def tier_for(score: float, cfg: ScoringConfig) -> str:
    if score >= cfg.high_threshold:
        return "High"
    if score >= cfg.medium_threshold:
        return "Medium"
    return "Low"


def evaluate(mention: Mention, cls: Classification, cfg: ScoringConfig, now: datetime) -> ScoreResult:
    reason = drop_reason(mention, cls, cfg, now)
    if reason:
        return ScoreResult(0.0, DROPPED, reason)

    total = float(BASE_SCORES[cls.primary])
    primary_terms = cls.key_terms.get(cls.primary, [])
    if primary_terms:  # where the triggering phrase sits: title or snippet only
        title = normalize(mention.title)
        total += TITLE_BONUS if any(contains(title, t) for t in primary_terms) else SNIPPET_BONUS
    if asks_question(mention, cfg):
        total += QUESTION_BONUS
    total += min(SECONDARY_MAX, SECONDARY_EACH * max(0, len(cls.signals) - 1))
    total += cfg.subreddit_bonus.get(mention.subreddit.lower(), 0.0)
    total += recency_bonus(age_hours(mention, now))
    if mention.kind == "post":
        total += POST_BONUS

    is_brand = cls.primary == "brand_mention"
    enterprise = False
    if not is_brand:
        if not cls.has_specific_context:
            total -= GENERIC_PENALTY
        if find(normalize(mention.text), cfg.enterprise_terms):
            total -= ENTERPRISE_PENALTY
            enterprise = True

    total = max(0.0, min(100.0, total))
    if is_brand:
        total = max(total, float(cfg.high_threshold))
        return ScoreResult(total, "High")
    if enterprise:
        # Enterprise teams aren't Elvan's buyer: never High, whatever the other bonuses add up to.
        total = min(total, float(cfg.high_threshold - 1))
    return ScoreResult(total, tier_for(total, cfg))


def score(mention: Mention, cls: Classification, cfg: ScoringConfig, now: datetime) -> tuple[float, str]:
    result = evaluate(mention, cls, cfg, now)
    return result.score, result.tier


def build_lead(mention: Mention, cfg: ScoringConfig, now: datetime) -> Lead:
    cls = classify(mention, cfg)
    result = evaluate(mention, cls, cfg, now)
    return Lead(
        mention=mention,
        primary_signal=cls.primary,
        signals=cls.signals,
        matched_terms=cls.matched_terms,
        competitor=cls.competitor,
        score=result.score,
        tier=result.tier,
        scored_at=now,
        drop_reason=result.drop_reason,
    )
