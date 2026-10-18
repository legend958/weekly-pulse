"""Stage D2 — Fee Explainer generator (PRD R10–R14; architecture.md §5)."""

from __future__ import annotations

from datetime import date

from .config import Config
from .llm import LLMClient
from .models import FeeExplainer, Insights

# Indicative facts sheet. An LLM backend would phrase these; the deterministic
# path emits them directly. Amounts are indicative and flagged as such.
FEE_BULLETS = [
    "Brokerage is Groww's own charge: a flat ₹20 per executed stock order; mutual fund investments have no brokerage.",
    "Stamp duty is a state government charge, applied only on the buy side of a trade and not on the sell side.",
    "Exchange transaction charges are levied by NSE/BSE on both buy and sell turnover of every order.",
    "SEBI turnover fees are a small statutory charge collected on the total turnover of each trade.",
    "GST is charged at 18% on brokerage plus exchange transaction charges and SEBI fees (not on stamp duty).",
    "DP (depository) charges apply when shares are sold and delivered from your demat account, typically per scrip per day.",
]

OFFICIAL_LINKS = ["https://groww.in/pricing", "https://groww.in/help"]


def build_fee_explainer(
    insights: Insights, config: Config, llm: LLMClient | None = None
) -> FeeExplainer | None:
    fee = insights.fee_confusion
    if not fee.detected:
        return None

    bullets = list(FEE_BULLETS)
    links = [l for l in OFFICIAL_LINKS if any(l.startswith(f"https://{h}") for h in config.fee_link_allowlist)]

    return FeeExplainer(
        bullets=bullets[:6],
        links=links[:2],
        last_checked=date.today().isoformat(),
        source_review_ids=fee.evidence_ids,
    )


def render_fee_explainer(ex: FeeExplainer) -> str:
    lines = [f"Understanding trading charges — {ex.last_checked}", ""]
    for b in ex.bullets:
        lines.append(f"- {b}")
    lines.append("")
    lines.append("Sources:")
    for l in ex.links:
        lines.append(f"- {l}")
    lines.append("")
    lines.append(f"Last checked: {ex.last_checked}")
    return "\n".join(lines)
