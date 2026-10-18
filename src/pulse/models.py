"""Data contracts for the Weekly Product Pulse pipeline (see architecture.md §4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal


# --------------------------------------------------------------------------- #
# Stage A — ingest
# --------------------------------------------------------------------------- #
@dataclass
class Review:
    id: str
    date: date
    rating: int
    title: str
    text: str
    url: str
    app_version: str
    helpful_votes: int


@dataclass
class QualityReport:
    rows: int
    date_min: str
    date_max: str
    rating_mix: dict[int, int]
    duplicates_removed: int
    empty_reviews: int
    header_ok: bool


# --------------------------------------------------------------------------- #
# Stage B — clustering
# --------------------------------------------------------------------------- #
@dataclass
class Theme:
    name: str
    summary: str
    review_ids: list[str]
    count: int
    avg_rating: float

    @property
    def score(self) -> float:
        """Volume weighted by severity (lower avg rating => higher severity)."""
        return self.count * (6.0 - self.avg_rating)


@dataclass
class ThemeSet:
    themes: list[Theme]
    background_praise: int
    background_other: int


# --------------------------------------------------------------------------- #
# Stage C — insights
# --------------------------------------------------------------------------- #
@dataclass
class Quote:
    text: str
    review_id: str
    url: str
    rating: int
    date: str
    theme: str


@dataclass
class FeeConfusion:
    detected: bool
    label: str
    evidence_ids: list[str]
    mention_count: int


@dataclass
class Insights:
    top_themes: list[Theme]
    quotes: list[Quote]
    fee_confusion: FeeConfusion
    weekly_trend: dict[str, dict[str, int]]
    weeks: list[str]
    period_end: str = ""


# --------------------------------------------------------------------------- #
# Stage D — generation
# --------------------------------------------------------------------------- #
@dataclass
class ProductPulse:
    text: str
    sections: dict


@dataclass
class FeeExplainer:
    bullets: list[str]
    links: list[str]
    last_checked: str
    source_review_ids: list[str]


# --------------------------------------------------------------------------- #
# Stage E — validation
# --------------------------------------------------------------------------- #
@dataclass
class ValidationReport:
    ok: bool
    failures: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Stage F/G — approval + publish
# --------------------------------------------------------------------------- #
ApprovalState = Literal["draft", "pending_approval", "approved", "rejected", "published"]


# The two n8n outcomes (Stage G).
Outcome = Literal["append_note", "create_email_draft"]


@dataclass
class PublishRecord:
    tool: str
    artifact: Literal["pulse", "fee_explainer"]
    destination: str
    published_at: str
    outcome: str = ""            # "append_note" | "create_email_draft" | "error"
    detail: str = ""             # free-text from the MCP tool (e.g. draft id)


@dataclass
class RunResult:
    run_id: str
    quality: QualityReport
    theme_set: ThemeSet
    insights: Insights
    pulse: ProductPulse
    fee_explainer: FeeExplainer | None
    validation: ValidationReport
    approval_state: ApprovalState
    publish_records: list[PublishRecord]
    reviewer: str = ""
