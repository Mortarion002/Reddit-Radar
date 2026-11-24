from __future__ import annotations

import dataclasses

import pytest

from radar.scorer import (
    DROPPED,
    ScoringConfig,
    build_lead,
    classify,
    contains,
    normalize,
    recency_bonus,
    score,
)

from .helpers import NOW, mention


@pytest.fixture(scope="module")
def cfg():
    from radar.config import REPO_ROOT, load_settings

    s = load_settings(env_file=REPO_ROOT / "nonexistent.env", environ={}, warn=False)
    return ScoringConfig.from_settings(s)


def lead(cfg, *args, **kwargs):
    return build_lead(mention(*args, **kwargs), cfg, NOW)


# ---- spec cases -------------------------------------------------------------------


def test_alternative_search_high(cfg):
    l = lead(cfg, "Survicate alternatives for a small CS team?", "We only run NPS now.")
    assert l.primary_signal == "alternative_search"
    assert l.tier == "High"
    assert l.competitor == "survicate"


def test_competitor_pain_high(cfg):
    l = lead(cfg, "Survicate just doubled our renewal price", "Same plan, twice the cost.")
    assert l.primary_signal == "competitor_pain"
    assert l.tier == "High"


def test_tool_request_high(cfg):
    l = lead(cfg, "What do you all use for NPS surveys?", "B2B SaaS, 400 customers.", sub="CustomerSuccess")
    assert l.primary_signal == "tool_request"
    assert l.tier == "High"


def test_integration_need(cfg):
    l = lead(cfg, "How do I auto-send CSAT when a Zendesk ticket closes?")
    assert l.primary_signal == "integration_need"


def test_delighted_fallout(cfg):
    l = lead(cfg, "Delighted is gone", "We lost our Delighted history after the shutdown", kind="comment")
    assert l.primary_signal == "delighted_fallout"


def test_pmf_founder(cfg):
    l = lead(cfg, "How did you know you hit product market fit?", sub="startups")
    assert l.primary_signal == "pmf_founder"


def test_feedback_pain(cfg):
    l = lead(cfg, "Our CSAT response rate is 4%, ideas?", sub="CustomerService")
    assert l.primary_signal == "feedback_pain"


def test_competitor_mention_low(cfg):
    l = lead(cfg, "Macros for billing tickets", "we use simplesat for this", kind="comment", sub="Zendesk")
    assert l.primary_signal == "competitor_mention"
    assert l.tier == "Low"


def test_brand_mention_high(cfg):
    l = lead(cfg, "Anyone tried elvan.ai?", hours=30, sub="randomsub")
    assert l.primary_signal == "brand_mention"
    assert l.tier == "High"


def test_promo_dropped(cfg):
    l = lead(cfg, "I built a survey tool, roast my landing page", "NPS and CSAT built in")
    assert l.tier == DROPPED and l.drop_reason == "promotional"


def test_job_post_dropped(cfg):
    l = lead(cfg, "[Hiring] Customer Success Manager", "Must have run NPS programs")
    assert l.tier == DROPPED and l.drop_reason == "job_post"


def test_msp_dropped(cfg):
    l = lead(cfg, "Best ConnectWise survey integration for our MSP", "CSAT on closed tickets", sub="msp")
    assert l.tier == DROPPED and l.drop_reason == "msp"


def test_old_mention_dropped(cfg):
    l = lead(cfg, "Looking for an NPS tool?", hours=96)
    assert l.tier == DROPPED and l.drop_reason == "too_old"


def test_enterprise_rfp_penalized_not_high(cfg):
    base = lead(cfg, "Looking for an NPS tool for our team?", "Want three vendors by Q1.", sub="CustomerSuccess")
    ent = lead(
        cfg,
        "Looking for an NPS tool for our global rollout?",
        "Procurement wants an RFP with three vendors by Q1.",
        sub="CustomerSuccess",
    )
    assert ent.primary_signal == "tool_request"
    assert ent.tier != "High"
    assert ent.score < base.score


def test_post_beats_comment(cfg):
    text = ("What do you use for NPS surveys?", "small team")
    post = lead(cfg, *text, kind="post")
    comment = lead(cfg, *text, kind="comment")
    assert post.score > comment.score


def test_title_match_beats_snippet_only(cfg):
    in_title = lead(cfg, "Survicate pricing is too expensive", "thoughts")
    in_snippet = lead(cfg, "Thoughts on this", "Survicate pricing is too expensive")
    assert in_title.primary_signal == in_snippet.primary_signal == "competitor_pain"
    assert in_title.score > in_snippet.score


def test_recency_one_hour_beats_twenty(cfg):
    fresh = lead(cfg, "Survicate pricing is too expensive", hours=1)
    stale = lead(cfg, "Survicate pricing is too expensive", hours=20)
    assert fresh.score > stale.score
    assert recency_bonus(1) == 12 and recency_bonus(5) == 8 and recency_bonus(20) == 4 and recency_bonus(30) == 0


def test_plurals_match():
    assert contains(normalize("Best NPS tools for B2B"), "nps tool")
    assert contains(normalize("any alternatives?"), "alternative")


def test_possessive_matches(cfg):
    l = lead(cfg, "SurveySparrow's pricing is wild", kind="comment")
    assert "surveysparrow" in l.matched_terms
    assert l.primary_signal == "competitor_pain"


def test_blocklisted_subreddit_dropped(cfg):
    blocked = dataclasses.replace(cfg, blocklist=frozenset({"memes"}))
    l = build_lead(mention("What do you use for NPS surveys?", sub="memes"), blocked, NOW)
    assert l.tier == DROPPED and l.drop_reason == "blocked_subreddit"


# ---- extra cases --------------------------------------------------------------------


def test_word_boundaries():
    assert not contains(normalize("our services team"), "ces")
    assert contains(normalize("CES score after support"), "ces")
    assert contains(normalize("check out elvan.ai today"), "elvan.ai")


def test_brand_ignores_enterprise_and_msp(cfg):
    l = lead(cfg, "Anyone tried elvan.ai for our MSP?", "procurement wants an RFP", sub="msp")
    assert l.tier == "High"


def test_promo_beats_brand(cfg):
    l = lead(cfg, "We launched an elvan.ai integration", "check out my plugin")
    assert l.tier == DROPPED


def test_generic_only_penalized(cfg):
    generic = classify(mention("Looking for a survey for my bakery"), cfg)
    specific = classify(mention("Looking for an NPS survey for my bakery"), cfg)
    assert not generic.has_specific_context and specific.has_specific_context
    g, _ = score(mention("Looking for a survey for my bakery"), generic, cfg, NOW)
    s, _ = score(mention("Looking for an NPS survey for my bakery"), specific, cfg, NOW)
    assert s - g >= 15


def test_ambiguous_delighted_needs_context(cfg):
    c = classify(mention("I was delighted with the trip", kind="comment"), cfg)
    assert c.competitor is None and c.primary == "none"
    c2 = classify(mention("Delighted for NPS?", kind="comment"), cfg)
    assert c2.competitor == "delighted"


def test_question_bonus(cfg):
    q = lead(cfg, "Survicate pricing too expensive?")
    s = lead(cfg, "Survicate pricing too expensive")
    assert q.score - s.score == 10


def test_secondary_signals_capped(cfg):
    c = classify(
        mention(
            "Survicate alternatives? pricing too expensive",
            "looking for an NPS tool with Zendesk, our response rate is low",
        ),
        cfg,
    )
    assert len(c.signals) >= 4
    many, _ = score(mention("x"), c, cfg, NOW)
    assert many <= 100


def test_subreddit_bonus(cfg):
    cs = lead(cfg, "Survicate pricing too expensive", sub="CustomerSuccess")
    other = lead(cfg, "Survicate pricing too expensive", sub="randomsub")
    assert cs.score - other.score == 8


def test_priority_competitor_preferred(cfg):
    c = classify(mention("Typeform vs Simplesat pricing"), cfg)
    assert c.competitor == "simplesat"


def test_no_signal_is_low(cfg):
    l = lead(cfg, "Weekend plans", "nothing relevant here", sub="randomsub", kind="comment", hours=30)
    assert l.primary_signal == "none"
    assert l.tier == "Low"


def test_terms_deduped(cfg):
    c = classify(mention("Survicate alternatives?"), cfg)
    assert c.matched_terms.count("alternative") + c.matched_terms.count("alternatives") == 1
