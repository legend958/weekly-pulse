"""Orchestrator: run the Weekly Product Pulse pipeline end to end.

Usage (from the repo root):
    python run_pipeline.py --offline --decision approve
    python -m pulse.run_pipeline --offline            # from src/, PYTHONPATH=src

    # re-publish an existing run through Stage G only (no CSV re-analysis);
    # endpoint/token/notes doc are read from .env.local
    python run_pipeline.py --mcp-only [--from-run <run_id>]

See architecture.md §2 for the stage diagram.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from dataclasses import asdict
from datetime import date, datetime, timezone

from .approval import decide
from .cluster import cluster
from .config import Config, load_env_file
from .fee_explainer import build_fee_explainer, render_fee_explainer
from .ingest import ingest
from .insights import extract_insights
from .llm import get_llm
from .models import (
    FeeConfusion,
    FeeExplainer,
    Insights,
    ProductPulse,
    PublishRecord,
    Quote,
    RunResult,
    Theme,
)
from .pulse import generate_pulse
from .publish_mcp import publish
from .validators import validate


def _run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{hashlib.sha1(stamp.encode()).hexdigest()[:6]}"


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _json_default(o):
    """Serialize Stage A types (``datetime.date``) for the JSONL artifact."""
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def run(config: Config, decision: str | None, quiet: bool = False) -> RunResult:
    run_id = _run_id()
    out = os.path.join(config.output_dir, run_id)
    os.makedirs(out, exist_ok=True)

    # Stage A
    reviews, quality = ingest(config)
    # Stage B
    llm = get_llm(config) if config.llm_backend != "offline" else None
    theme_set = cluster(reviews, config, llm)
    # Stage C
    insights = extract_insights(reviews, theme_set, config)
    # Stage D
    pulse = generate_pulse(insights, config, llm)
    fee_explainer = build_fee_explainer(insights, config, llm)
    # Stage E
    validation = validate(reviews, theme_set, insights, pulse, fee_explainer, config)

    # Artifacts (pre-approval)
    # Stage A output + quality report as JSONL (architecture §9)
    review_lines = [
        json.dumps({"type": "review", **asdict(r)}, ensure_ascii=False, default=_json_default)
        for r in reviews
    ]
    review_lines.append(
        json.dumps({"type": "quality_report", **asdict(quality)}, default=_json_default)
    )
    _write(os.path.join(out, "reviews.normalized.jsonl"), "\n".join(review_lines) + "\n")
    _write(os.path.join(out, "themes.json"), json.dumps([asdict(t) for t in theme_set.themes], indent=2))
    _write(os.path.join(out, "insights.json"), json.dumps(asdict(insights), indent=2))
    _write(os.path.join(out, "pulse.md"), pulse.text)
    if fee_explainer is not None:
        _write(os.path.join(out, "fee_explainer.md"), render_fee_explainer(fee_explainer))
    _write(os.path.join(out, "validation.json"), json.dumps(asdict(validation), indent=2))
    _write(
        os.path.join(out, "run_meta.json"),
        json.dumps(
            {
                "run_id": run_id,
                "backend": config.llm_backend,
                "model_id": config.model_id,
                "seed": config.seed,
                "input_csv": config.input_csv,
                "mcp": {
                    "transport": config.mcp_transport,
                    "endpoint": config.mcp_endpoint,
                    "tool_append_note": config.mcp_tool_append_note,
                    "tool_email_draft": config.mcp_tool_email_draft,
                },
                "quality": asdict(quality),
            },
            indent=2,
        ),
    )

    # Stage F
    state, reviewer = decide(config, decision, validation, os.path.join(out, "approval.json"))

    # Stage G
    records = publish(state, pulse, fee_explainer, config, run_id) if validation.ok else []
    _write(os.path.join(out, "publish_log.json"), json.dumps([asdict(r) for r in records], indent=2))

    result = RunResult(
        run_id=run_id,
        quality=quality,
        theme_set=theme_set,
        insights=insights,
        pulse=pulse,
        fee_explainer=fee_explainer,
        validation=validation,
        approval_state=state,
        publish_records=records,
        reviewer=reviewer,
    )

    if not quiet:
        _print_summary(result, out)
    return result


# --------------------------------------------------------------------------- #
# Re-publish (Stage G only) — `--mcp-only`
# --------------------------------------------------------------------------- #
_LAST_CHECKED_RE = re.compile(r"^Last checked:\s*(.+)$", re.MULTILINE)


def _load_insights(run_dir: str) -> Insights:
    """Rebuild the Stage C contract from its saved ``asdict`` dump."""
    with open(os.path.join(run_dir, "insights.json"), encoding="utf-8") as f:
        raw = json.load(f)
    return Insights(
        top_themes=[Theme(**t) for t in raw["top_themes"]],
        quotes=[Quote(**q) for q in raw["quotes"]],
        fee_confusion=FeeConfusion(**raw["fee_confusion"]),
        weekly_trend=raw["weekly_trend"],
        weeks=raw["weeks"],
        period_end=raw.get("period_end", ""),
    )


def _latest_run_id(output_dir: str) -> str:
    """Newest run id under `output_dir` (run ids sort chronologically)."""
    if not os.path.isdir(output_dir):
        return ""
    runs = [
        name
        for name in os.listdir(output_dir)
        if os.path.isfile(os.path.join(output_dir, name, "approval.json"))
    ]
    return sorted(runs)[-1] if runs else ""


def load_run_artifacts(
    run_dir: str, config: Config
) -> tuple[ProductPulse, FeeExplainer | None]:
    """Rebuild Stage D's publishable payloads from a run's saved artifacts.

    Stage G only reads ``pulse.text`` and the fee explainer, so a saved run can
    be republished without re-reading the CSV. The explainer is rebuilt from
    ``insights.json`` (fully deterministic offline) and keeps the ``last_checked``
    date recorded in ``fee_explainer.md`` — a republish never rewrites it.
    """
    with open(os.path.join(run_dir, "pulse.md"), encoding="utf-8") as f:
        # `sections` is runtime-only and never serialized; Stage G ignores it.
        pulse = ProductPulse(text=f.read(), sections={})

    fee_md = os.path.join(run_dir, "fee_explainer.md")
    if not os.path.isfile(fee_md):
        return pulse, None
    fee_explainer = build_fee_explainer(_load_insights(run_dir), config)
    if fee_explainer is None:
        return pulse, None
    with open(fee_md, encoding="utf-8") as f:
        match = _LAST_CHECKED_RE.search(f.read())
    if match:
        fee_explainer.last_checked = match.group(1).strip()
    return pulse, fee_explainer


def republish(run_id: str, config: Config, quiet: bool = False) -> list[PublishRecord]:
    """Re-publish a previous run's artifacts through Stage G, skipping Stages A-F.

    The run's saved artifacts are reloaded, the two publishable payloads rebuilt
    and ``publish()`` called again. The recorded approval is re-checked first, so
    ``--mcp-only`` can never be used to slip past the Stage F gate.

    New records go to ``publish_log.republish.json`` beside the original
    ``publish_log.json``, keeping the first publish auditable.
    """
    run_dir = os.path.join(config.output_dir, run_id)
    required = ("approval.json", "insights.json", "pulse.md")
    missing = [n for n in required if not os.path.isfile(os.path.join(run_dir, n))]
    if missing:
        raise RuntimeError(
            f"{run_dir!r} is not a Weekly Product Pulse run directory "
            f"(missing: {', '.join(missing)})"
        )

    with open(os.path.join(run_dir, "approval.json"), encoding="utf-8") as f:
        approval = json.load(f)
    state = str(approval.get("state", ""))
    validation_ok = bool(approval.get("validation_ok", False))
    if state != "approved" or not validation_ok:
        raise RuntimeError(
            f"run {run_id} is not publishable (state={state!r}, "
            f"validation_ok={validation_ok!r}); re-publishing requires an "
            "approved run"
        )

    pulse, fee_explainer = load_run_artifacts(run_dir, config)

    records = publish(state, pulse, fee_explainer, config, run_id)
    _write(
        os.path.join(run_dir, "publish_log.republish.json"),
        json.dumps([asdict(r) for r in records], indent=2),
    )

    if not quiet:
        print(f"Republish {run_id}  ->  {run_dir}")
        print(f"Published: {len(records)} artifact(s)")
        for p in records:
            mark = "!" if p.outcome == "error" else "-"
            print(f"  {mark} {p.outcome or p.artifact} via {p.tool} -> {p.destination or p.detail}")
    return records


def _print_summary(r: RunResult, out: str) -> None:
    print(f"Run {r.run_id}  ->  {out}")
    print(f"Reviews: {r.quality.rows}  ({r.quality.date_min}..{r.quality.date_max})")
    print("Themes:")
    for t in r.theme_set.themes:
        print(f"  - {t.name}: {t.count} (avg {t.avg_rating:.2f})")
    fee = r.insights.fee_confusion
    print(f"Fee confusion: {'detected -> ' + fee.label if fee.detected else 'none'} ({fee.mention_count} mentions)")
    print(f"Validation: {'OK' if r.validation.ok else 'FAILED: ' + '; '.join(r.validation.failures)}")
    print(f"Approval: {r.approval_state} (reviewer={r.reviewer})")
    print(f"Published: {len(r.publish_records)} artifact(s)")
    for p in r.publish_records:
        mark = "!" if p.outcome == "error" else "-"
        print(f"  {mark} {p.outcome or p.artifact} via {p.tool} -> {p.destination or p.detail}")


def main(argv: list[str] | None = None) -> int:
    # Windows consoles default to cp1252; the pipeline emits ₹ and ★.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description="Weekly Product Pulse pipeline")
    parser.add_argument("--input", dest="input_csv", default=None, help="reviews CSV path")
    parser.add_argument("--output-dir", dest="output_dir", default=None)
    parser.add_argument("--offline", action="store_true", help="force deterministic mode")
    parser.add_argument("--backend", choices=["offline", "gateway", "direct"], default=None)
    parser.add_argument("--decision", choices=["approve", "reject", "pending"], default=None)
    parser.add_argument("--auto-approve", action="store_true")
    parser.add_argument("--mcp-transport", choices=["mock", "http"], default=None,
                        help="mock (offline) or http (live n8n MCP server)")
    parser.add_argument("--mcp-endpoint", default=None, help="n8n MCP Server Trigger URL")
    parser.add_argument("--mcp-token", default=None, help="Bearer token for the MCP endpoint")
    parser.add_argument("--mcp-only", action="store_true",
                        help="re-publish a previous run's artifacts; skips Stages A-F")
    parser.add_argument("--from-run", dest="from_run", default=None,
                        help="run id to re-publish (default: newest run under --output-dir)")
    parser.add_argument("--env-file", default=".env.local",
                        help="secrets file read before the environment (default: .env.local)")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    load_env_file(args.env_file)
    config = Config.from_env()
    if args.input_csv:
        config.input_csv = args.input_csv
    if args.output_dir:
        config.output_dir = args.output_dir
    if args.backend:
        config.llm_backend = args.backend
    if args.offline:
        config.llm_backend = "offline"
    if args.auto_approve:
        config.allow_auto_approve = True
    if args.mcp_transport:
        config.mcp_transport = args.mcp_transport
    if args.mcp_endpoint:
        config.mcp_endpoint = args.mcp_endpoint
    if args.mcp_token:
        config.mcp_token = args.mcp_token

    if args.mcp_only:
        run_id = args.from_run or _latest_run_id(config.output_dir)
        if not run_id:
            print(f"No previous run found under {config.output_dir!r}.", file=sys.stderr)
            return 1
        try:
            records = republish(run_id, config, quiet=args.quiet)
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"FAILED: {exc}", file=sys.stderr)
            return 1
        return 1 if any(r.outcome == "error" for r in records) else 0

    result = run(config, args.decision, quiet=args.quiet)
    if not result.validation.ok:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
