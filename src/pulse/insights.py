"""Stage C — Insight extraction (PRD R3/R4/R5; architecture.md §5 Stage C)."""

from __future__ import annotations

import re
from collections import defaultdict

from .config import Config
from .models import FeeConfusion, Insights, Quote, Review, Theme, ThemeSet
from .utils import iso_week

FEE_KEYWORDS = re.compile(
    r"(?i)\b(brokerage|charges?|fees?|GST|stamp duty|deduct(?:ion|ions|ed)|DP charges?|hidden charges?)\b"
)
BROKERAGE_20 = re.compile(r"(?i)\b(Rs|₹|INR)?\s*20\b.*brokerage|brokerage.*\b(Rs|₹|INR)?\s*20\b")


def _top_themes(theme_set: ThemeSet, n: int = 3) -> list[Theme]:
    ranked = sorted(theme_set.themes, key=lambda t: t.score, reverse=True)
    return ranked[:n]


def _pick_quotes(top: list[Theme], by_id: dict[str, Review]) -> list[Quote]:
    quotes: list[Quote] = []
    used: set[str] = set()
    for theme in top:
        members = [by_id[i] for i in theme.review_ids if i in by_id]
        # Prefer critical reviews, then most helpful, then substantive length.
        members.sort(
            key=lambda r: (r.rating > 3, -r.helpful_votes, abs(len(r.text) - 150))
        )
        chosen = next((m for m in members if m.id not in used), None)
        if chosen is None:
            continue
        used.add(chosen.id)
        quotes.append(
            Quote(
                text=chosen.text,
                review_id=chosen.id,
                url=chosen.url,
                rating=chosen.rating,
                date=chosen.date.isoformat(),
                theme=theme.name,
            )
        )
    return quotes


def _detect_fee_confusion(
    reviews: list[Review], config: Config
) -> FeeConfusion:
    fee_reviews = [r for r in reviews if FEE_KEYWORDS.search(r.text)]
    count = len(fee_reviews)
    if count < config.fee_min_mentions:
        return FeeConfusion(False, "", [], count)

    label = "₹20 brokerage vs. total charges"
    if not any(BROKERAGE_20.search(r.text) for r in fee_reviews):
        label = "Unclear or high trading charges"

    fee_reviews.sort(key=lambda r: (r.rating, -r.helpful_votes))
    evidence = [r.id for r in fee_reviews[:5]]
    return FeeConfusion(True, label, evidence, count)


def _weekly_trend(reviews: list[Review], theme_names: list[str]) -> tuple[dict, list[str]]:
    # Map each review to its theme via a fresh classification to keep insights
    # independent of any single ThemeSet instance.
    from .cluster import classify

    trend: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in reviews:
        name = classify(r.text)
        if name is None:
            name = "praise/other"
        trend[iso_week(r.date)][name] += 1
    weeks = sorted(trend.keys())
    return {w: dict(trend[w]) for w in weeks}, weeks


def extract_insights(
    reviews: list[Review], theme_set: ThemeSet, config: Config
) -> Insights:
    by_id = {r.id: r for r in reviews}
    top = _top_themes(theme_set, 3)
    quotes = _pick_quotes(top, by_id)
    fee = _detect_fee_confusion(reviews, config)
    weekly, weeks = _weekly_trend(reviews, [t.name for t in theme_set.themes])
    return Insights(
        top_themes=top,
        quotes=quotes,
        fee_confusion=fee,
        weekly_trend=weekly,
        weeks=weeks,
        period_end=max(r.date for r in reviews).isoformat() if reviews else "",
    )
