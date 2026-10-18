"""Stage F — Human approval gate (PRD R16; architecture.md §5 Stage F).

State machine: draft -> pending_approval -> approved -> published
                                 \\-> rejected -> draft

Publishing is impossible unless the state is `approved`. Auto-approval is
explicitly opt-in and requires both `--auto-approve` and ALLOW_AUTO_APPROVE=1.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

from .config import Config
from .models import ApprovalState, ValidationReport


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def decide(
    config: Config,
    decision: str | None,
    validation: ValidationReport,
    approval_path: str,
) -> tuple[ApprovalState, str]:
    """Return (state, reviewer). Writes the approval record to `approval_path`."""

    if not validation.ok:
        state, reviewer = "rejected", "validator"
    elif decision in ("approve", "approved"):
        state, reviewer = "approved", "cli"
    elif decision in ("reject", "rejected"):
        state, reviewer = "rejected", "cli"
    elif decision in ("pending", "pending_approval"):
        state, reviewer = "pending_approval", "cli"
    elif config.allow_auto_approve and os.environ.get("ALLOW_AUTO_APPROVE") == "1":
        state, reviewer = "approved", "auto"
    else:
        try:
            if sys.stdin is not None and sys.stdin.isatty():
                ans = input("Approve this week's pulse + fee explainer? [y/N] ").strip().lower()
                state, reviewer = ("approved", "interactive") if ans == "y" else ("pending_approval", "interactive")
            else:
                state, reviewer = "pending_approval", "none"
        except EOFError:
            state, reviewer = "pending_approval", "none"

    record = {
        "state": state,
        "reviewer": reviewer,
        "decided_at": _now(),
        "validation_ok": validation.ok,
        "failures": validation.failures,
    }
    os.makedirs(os.path.dirname(approval_path), exist_ok=True)
    with open(approval_path, "w", encoding="utf-8") as f:
        json.dump(record, f, indent=2)
    return state, reviewer  # type: ignore[return-value]
