from __future__ import annotations

from datetime import timedelta, timezone

from radar.digest import (
    DIGEST_CAP,
    RunSummary,
    age_label,
    callback_data,
    format_alert,
    format_digest,
    lead_header,
    status_buttons,
)
from radar.models import short_id
from radar.scorer import ScoringConfig, build_lead

from .helpers import NOW, mention

IST = timezone(timedelta(hours=5, minutes=30))


def make_lead(settings, *args, **kwargs):
    return build_lead(mention(*args, **kwargs), ScoringConfig.from_settings(settings), NOW)


def test_alert_format(settings):
    lead = make_lead(
        settings,
        "What are people moving to after Survicate's price change?",
        "we're on a legacy plan and survicate wants to double it",
        sub="CustomerSuccess",
        kind="comment",
        hours=40 / 60,
        keyword="survicate",
    )
    text = format_alert(lead, NOW)
    first = text.splitlines()[0]
    assert first.startswith("<b>HIGH ")
    assert "| competitor_pain | r/CustomerSuccess | comment | 40m ago" in first
    assert "Thread: What are people moving to after Survicate's price change?" in text
    assert '"we\'re on a legacy plan and survicate wants to double it"' in text
    assert "Keyword: survicate | Matched: survicate" in text
    assert text.rstrip().endswith(lead.mention.url)


def test_post_vs_comment_label(settings):
    post = make_lead(settings, "Survicate pricing too expensive", kind="post")
    comment = make_lead(settings, "Survicate pricing too expensive", kind="comment")
    assert "| post |" in lead_header(post, NOW)
    assert "| comment |" in lead_header(comment, NOW)


def test_html_escaped(settings):
    lead = make_lead(settings, "Is <script> & NPS > CSAT?", "a <b>bold</b> & claim")
    text = format_alert(lead, NOW)
    assert "<script>" not in text
    assert "&lt;script&gt; &amp; NPS &gt; CSAT?" in text
    assert "&lt;b&gt;bold&lt;/b&gt;" in text


def test_callback_data_under_64_bytes():
    sid = short_id("comment:" + "z" * 12)
    for row in status_buttons(sid):
        for label, data in row:
            assert len(data.encode()) < 64
            assert data.startswith("s:") and data.endswith(sid)
    assert callback_data("not_relevant", sid) == f"s:not_relevant:{sid}"
    assert [label for label, _ in status_buttons(sid)[0]] == ["Replied", "Skip", "Not relevant"]


def test_chosen_button_marked():
    labels = [label for label, _ in status_buttons("abc", chosen="skipped")[0]]
    assert labels == ["Replied", "* Skip", "Not relevant"]


def test_digest_cap(settings):
    leads = [make_lead(settings, f"Our CSAT response rate is low {i}?", sub="CustomerService") for i in range(20)]
    summary = RunSummary(emails_read=3, mentions_found=25, new_mentions=20, tiers={"Medium": 20})
    text = format_digest(summary, leads, NOW, IST)
    assert text.count("reddit.com") == DIGEST_CAP
    assert "+5 more" in text
    assert "Emails 3 | mentions 25 | new 20" in text
    assert "Medium 20" in text


def test_digest_without_leads_is_summary_only(settings):
    text = format_digest(RunSummary(new_mentions=1, tiers={"High": 1}, alerts_sent=1), [], NOW, IST)
    assert "Hot alerts sent: 1" in text
    assert "Leads to look at" not in text


def test_age_labels():
    assert age_label(0.5) == "30m ago"
    assert age_label(3.2) == "3h ago"
    assert age_label(50) == "2d ago"
