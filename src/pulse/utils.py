"""Small shared helpers."""

from __future__ import annotations

import hashlib
from datetime import date


def stable_id(url: str) -> str:
    """Deterministic short id derived from a review URL."""
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]


def iso_week(d: date) -> str:
    """Zero-padded ISO-ish week label used for the weekly trend (e.g. 2026-W30)."""
    return f"{d.year}-W{(d.timetuple().tm_yday - 1) // 7 + 1:02d}"


def word_count(text: str) -> int:
    return len(text.split())
