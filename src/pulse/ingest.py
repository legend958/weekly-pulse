"""Stage A — Ingest & clean (PRD R1; architecture.md §5 Stage A)."""

from __future__ import annotations

import csv
import re
from datetime import date

from .config import Config
from .models import QualityReport, Review
from .utils import stable_id

EXPECTED_HEADER = [
    "Date", "Rating", "Title", "Review", "App version",
    "Helpful votes", "Developer reply", "Reply date", "Review URL",
]

MIN_ROWS = 30


class IngestError(RuntimeError):
    pass


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def ingest(config: Config) -> tuple[list[Review], QualityReport]:
    with open(config.input_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        header_ok = header == EXPECTED_HEADER
        if not header_ok:
            raise IngestError(
                f"Unexpected CSV header.\n expected: {EXPECTED_HEADER}\n got:      {header}"
            )
        raw = list(reader)

    reviews: list[Review] = []
    seen: set[str] = set()
    duplicates = 0
    empty = 0

    for row in raw:
        text = (row.get("Review") or "").strip()
        if not text:
            empty += 1
            continue

        key = _norm(text)
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)

        try:
            rating = int(row.get("Rating") or 0)
            d = date.fromisoformat((row.get("Date") or "").strip())
        except (ValueError, TypeError):
            continue
        if rating < 1 or rating > 5:
            continue

        url = (row.get("Review URL") or "").strip()
        reviews.append(
            Review(
                id=stable_id(url or text),
                date=d,
                rating=rating,
                title=(row.get("Title") or "").strip(),
                text=text,
                url=url,
                app_version=(row.get("App version") or "").strip(),
                helpful_votes=int(row.get("Helpful votes") or 0),
            )
        )

    if len(reviews) < MIN_ROWS:
        raise IngestError(f"Only {len(reviews)} usable reviews; need >= {MIN_ROWS}.")

    mix: dict[int, int] = {}
    for r in reviews:
        mix[r.rating] = mix.get(r.rating, 0) + 1

    quality = QualityReport(
        rows=len(reviews),
        date_min=min(r.date for r in reviews).isoformat(),
        date_max=max(r.date for r in reviews).isoformat(),
        rating_mix=dict(sorted(mix.items())),
        duplicates_removed=duplicates,
        empty_reviews=empty,
        header_ok=header_ok,
    )
    return reviews, quality
