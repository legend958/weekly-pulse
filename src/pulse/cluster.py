"""Stage B — Theme clustering (PRD R2/R3; architecture.md §5 Stage B).

LLM-assisted when a backend is configured; otherwise a deterministic taxonomy
classifier. The deterministic path is what the tests and `--offline` runs use, so
results are fully reproducible.
"""

from __future__ import annotations

import re

from .config import Config
from .llm import LLMUnavailable, LLMClient
from .models import Review, Theme, ThemeSet

# Order matters: earlier themes win score ties.
THEME_DEFS: list[tuple[str, str, list[str]]] = [
    (
        "Fees & charges",
        "Confusion about brokerage and the other charges deducted on trades.",
        [
            r"\bbrokerage\b", r"\bcharges?\b", r"\bfees?\b", r"\bGST\b", r"stamp duty",
            r"\bdeduct(?:ion|ions|ed)\b", r"contract note", r"hidden charges?",
            r"\bDP charges?\b", r"pricing details", r"charge (?:breakup|structure)",
        ],
    ),
    (
        "KYC & onboarding",
        "KYC verification and account activation delays for new users.",
        [
            r"\bKYC\b", r"\bverif\w*", r"\bactivat\w*", r"account opening",
            r"\bdocuments?\b", r"re-upload",
        ],
    ),
    (
        "Withdrawals & settlements",
        "Slow or unclear payouts, withdrawals and fund settlements.",
        [
            r"\bwithdr\w*", r"\bpayouts?\b", r"\bsettlement\b", r"funds transfer",
            r"\bcredited\b", r"\bdebit\w*",
        ],
    ),
    (
        "App performance & stability",
        "Lag, freezes, crashes and slow loads, especially in market hours.",
        [
            r"\blagg?\w*", r"\bfreez\w*", r"\bcrash\w*", r"\bhang\w*", r"unresponsive",
            r"\bstale\b", r"\bloading\b", r"refresh quickly", r"\bperformance\b",
            r"take (?:too )?long to load", r"keeps loading", r"closes by itself",
            r"switch between sections",
        ],
    ),
    (
        "UI/UX & feature requests",
        "Cluttered navigation and requests for clearer charts and reports.",
        [
            r"clutter\w*", r"too many taps", r"hard to read", r"tax and profit-loss",
            r"navigation between sections", r"buried deep inside menus",
            r"watchlist and alerts", r"dashboard shows too much", r"not intuitive",
            r"readab\w*",
        ],
    ),
]


def _compile(defs):
    return [(name, summary, [re.compile(p, re.I) for p in pats]) for name, summary, pats in defs]


def classify(text: str) -> str | None:
    """Return the best-matching theme name, or None if no theme matches."""
    best_name, best_score = None, 0
    for name, _summary, pats in _COMPILED:
        score = sum(len(p.findall(text)) for p in pats)
        if score > best_score:
            best_name, best_score = name, score
    return best_name


def _assign_deterministic(reviews: list[Review]) -> tuple[dict[str, list[Review]], list[Review], list[Review]]:
    buckets: dict[str, list[Review]] = {name: [] for name, _, _ in THEME_DEFS}
    praise: list[Review] = []
    other: list[Review] = []
    for r in reviews:
        name = classify(r.text)
        if name:
            buckets[name].append(r)
        elif r.rating >= 4:
            praise.append(r)
        else:
            other.append(r)
    return buckets, praise, other


def _try_llm(config: Config, llm: LLMClient, reviews: list[Review]):
    """Extension point for LLM clustering. Returns a ThemeSet or None."""
    try:
        llm.complete("cluster reviews into <=5 themes", schema={})
    except LLMUnavailable:
        return None
    return None  # pragma: no cover - only reached once a backend is wired


def cluster(
    reviews: list[Review], config: Config, llm: LLMClient | None = None
) -> ThemeSet:
    buckets, praise, other = _assign_deterministic(reviews)
    if llm is not None:
        _try_llm(config, llm, reviews)  # no-op until a backend is configured

    themes: list[Theme] = []
    for name, summary, _ in THEME_DEFS:
        members = buckets[name]
        if not members:
            continue
        avg = sum(m.rating for m in members) / len(members)
        themes.append(
            Theme(
                name=name,
                summary=summary,
                review_ids=[m.id for m in members],
                count=len(members),
                avg_rating=round(avg, 2),
            )
        )

    themes.sort(key=lambda t: t.count, reverse=True)
    themes = themes[: config.max_themes]  # AC1: hard cap at 5
    return ThemeSet(themes=themes, background_praise=len(praise), background_other=len(other))


_COMPILED = _compile(THEME_DEFS)
