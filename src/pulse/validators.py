"""Stage E — Deterministic validation gate (PRD AC1–AC6; architecture.md §5)."""

from __future__ import annotations

from urllib.parse import urlparse

from .config import Config
from .models import (
    FeeExplainer,
    Insights,
    ProductPulse,
    Review,
    ThemeSet,
    ValidationReport,
)
from .utils import word_count


def validate(
    reviews: list[Review],
    theme_set: ThemeSet,
    insights: Insights,
    pulse: ProductPulse,
    fee_explainer: FeeExplainer | None,
    config: Config,
) -> ValidationReport:
    failures: list[str] = []
    by_id = {r.id: r for r in reviews}

    # AC1 — theme cap
    if len(theme_set.themes) > config.max_themes:
        failures.append(f"AC1: {len(theme_set.themes)} themes exceeds cap {config.max_themes}")

    # AC2 — top 3 ranked
    top = insights.top_themes
    if len(top) != 3:
        failures.append(f"AC2: expected 3 top themes, got {len(top)}")
    if top and list(top) != sorted(top, key=lambda t: t.score, reverse=True):
        failures.append("AC2: top themes are not ordered by score")

    # AC3 — quotes verbatim + traceable
    if len(insights.quotes) != 3:
        failures.append(f"AC3: expected 3 quotes, got {len(insights.quotes)}")
    for q in insights.quotes:
        src = by_id.get(q.review_id)
        if src is None:
            failures.append(f"AC3: quote references unknown review {q.review_id}")
            continue
        if q.text not in src.text:
            failures.append(f"AC3: quote not verbatim in review {q.review_id}")
        if not q.url:
            failures.append(f"AC3: quote for {q.review_id} has no URL")

    # AC4 — fee explainer grounded in reviews
    fee = insights.fee_confusion
    if fee.detected:
        if fee_explainer is None:
            failures.append("AC4: fee confusion detected but no explainer generated")
        elif not fee.evidence_ids:
            failures.append("AC4: fee explainer has no source reviews")
    elif fee_explainer is not None:
        failures.append("AC4: explainer generated without a detected fee confusion")

    # AC5 — pulse shape
    if word_count(pulse.text) > config.pulse_max_words:
        failures.append(f"AC5: pulse is {word_count(pulse.text)} words (> {config.pulse_max_words})")
    for key in ("themes", "quotes", "observation", "actions"):
        if not pulse.sections.get(key):
            failures.append(f"AC5: pulse missing section '{key}'")
    if len(pulse.sections.get("actions", [])) != 3:
        failures.append("AC5: expected exactly 3 action ideas")

    # AC6 — explainer shape + link allowlist
    if fee_explainer is not None:
        if len(fee_explainer.bullets) != 6:
            failures.append(f"AC6: expected 6 bullets, got {len(fee_explainer.bullets)}")
        if len(fee_explainer.links) != 2:
            failures.append(f"AC6: expected 2 links, got {len(fee_explainer.links)}")
        if not fee_explainer.last_checked:
            failures.append("AC6: missing 'Last checked' date")
        for link in fee_explainer.links:
            host = urlparse(link).hostname or ""
            if not any(host == h or host.endswith("." + h) for h in config.fee_link_allowlist):
                failures.append(f"AC6: link host not allowlisted: {link}")

    return ValidationReport(ok=not failures, failures=failures)
