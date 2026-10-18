"""Stage D1 — Weekly Product Pulse generator (PRD R6–R9; architecture.md §5)."""

from __future__ import annotations

from .config import Config
from .llm import LLMClient
from .models import Insights, ProductPulse
from .utils import word_count

ACTION_MAP = {
    "App performance & stability": (
        "Run a performance-hardening sprint: fix portfolio/order-screen freezes "
        "and chart load times during market hours."
    ),
    "Fees & charges": (
        "Add a pre-trade charge breakdown (brokerage + statutory charges) on the "
        "order screen and link the fee explainer in-app."
    ),
    "KYC & onboarding": (
        "Add a KYC status tracker that always shows the exact reason and next step "
        "when verification is pending or rejected."
    ),
    "Withdrawals & settlements": (
        "Show an expected-credit date for each withdrawal and push proactive "
        "status updates to the user."
    ),
    "UI/UX & feature requests": (
        "Simplify navigation and ship the clearer tax/profit-loss report users "
        "keep requesting."
    ),
}

GENERIC_ACTION = "Investigate the theme and ship one focused improvement next sprint."


def _observation(insights: Insights) -> str:
    fee = insights.fee_confusion
    top = insights.top_themes[0].name if insights.top_themes else "n/a"
    if fee.detected:
        weeks = insights.weeks
        series = [insights.weekly_trend[w].get("Fees & charges", 0) for w in weeks]
        if series:
            first = series[0]
            peak = max(series)
            return (
                f"Fee/charge complaints are climbing — from {first}/week early on to "
                f"{peak}/week at peak — while '{top}' remains the largest single theme."
            )
    return f"'{top}' is the largest theme this period; no recurring fee confusion was detected."


def generate_pulse(insights: Insights, config: Config, llm: LLMClient | None = None) -> ProductPulse:
    """Deterministic pulse. (An LLM backend would be wired in via `llm`.)"""
    themes = insights.top_themes
    quotes = insights.quotes
    actions = [ACTION_MAP.get(t.name, GENERIC_ACTION) for t in themes][:3]

    week = insights.period_end or (insights.weeks[-1] if insights.weeks else "n/a")

    lines: list[str] = [f"Weekly Product Pulse — week ending {week}", ""]
    lines.append("Top themes (by volume and severity):")
    for i, t in enumerate(themes, 1):
        lines.append(f"{i}. {t.name} — {t.count} reviews (avg {t.avg_rating:.2f}★)")
    lines.append("")
    lines.append("User quotes:")
    for q in quotes:
        lines.append(f'- "{q.text}" ({q.rating}★, {q.date})')
    lines.append("")
    lines.append(f"Key observation: {_observation(insights)}")
    lines.append("")
    lines.append("Action ideas:")
    for i, a in enumerate(actions, 1):
        lines.append(f"{i}. {a}")

    text = "\n".join(lines)

    # Enforce the word cap defensively: shorten quotes if needed.
    def _fit(txt: str) -> str:
        if word_count(txt) <= config.pulse_max_words:
            return txt
        trimmed = []
        for q in quotes:
            words = q.text.split()
            short = " ".join(words[:18]) + ("…" if len(words) > 18 else "")
            trimmed.append(f'- "{short}" ({q.rating}★, {q.date})')
        rebuilt = lines[:]
        start = rebuilt.index("User quotes:")
        rebuilt[start + 1:start + 1 + len(quotes)] = trimmed
        return "\n".join(rebuilt)

    text = _fit(text)

    sections = {
        "themes": [f"{t.name} ({t.count})" for t in themes],
        "quotes": [q.text for q in quotes],
        "observation": _observation(insights),
        "actions": actions,
    }
    return ProductPulse(text=text, sections=sections)
