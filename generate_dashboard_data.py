"""Build the static dashboard payload consumed by ``public/index.html``.

The dashboard shows the most recent run that was actually **published** through
Stage G (MCP), so the Google Docs / Gmail links it shows always correspond to a
real publish. If no run has a publish log yet, the newest run is used instead
and the page simply shows no MCP destinations.

    python generate_dashboard_data.py

Writes ``public/data.json``.
"""

import json
from pathlib import Path

OUTPUT_DIR = Path("output")
PUBLIC_DIR = Path("public")

ARTIFACTS = {
    "pulse": "pulse.md",
    "fee_explainer": "fee_explainer.md",
    "themes": "themes.json",
    "insights": "insights.json",
    "approval": "approval.json",
    "run_meta": "run_meta.json",
}


def _run_dirs():
    """Run directories that look like a completed pipeline run, newest first."""
    if not OUTPUT_DIR.is_dir():
        return []
    runs = [
        p
        for p in OUTPUT_DIR.iterdir()
        if p.is_dir() and (p / "run_meta.json").exists()
    ]
    return sorted(runs, key=lambda p: p.name, reverse=True)


def _publish_log(run_dir: Path):
    """Return the publish records of a run, preferring the original publish log.

    A run can have two logs: ``publish_log.json`` (the first publish) and
    ``publish_log.republish.json`` (a later ``--mcp-only`` republish). Both are
    merged so the page shows every destination produced for that run.
    """
    records = []
    for name in ("publish_log.json", "publish_log.republish.json"):
        path = run_dir / name
        if not path.is_file():
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                loaded = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(loaded, list):
            records.extend(r for r in loaded if isinstance(r, dict) and r.get("destination"))
    return records


def _load(run_dir: Path) -> dict:
    data = {"run_id": run_dir.name}
    for key, filename in ARTIFACTS.items():
        path = run_dir / filename
        if not path.is_file():
            continue
        with open(path, encoding="utf-8") as fh:
            content = fh.read()
        if filename.endswith(".json"):
            try:
                data[key] = json.loads(content)
            except json.JSONDecodeError:
                continue
        else:
            data[key] = content

    records = _publish_log(run_dir)
    data["publish_log"] = records
    data["published"] = bool(records)
    return data


def _dedupe(records):
    """Keep the most recent destination per outcome (publish, then republish)."""
    latest = {}
    for record in records:
        key = record.get("outcome") or record.get("tool")
        latest[key] = record
    return list(latest.values())


def _previous_dashboard():
    """The dashboard currently on disk, or ``{}``.

    A web build runs the pipeline without ``--decision approve``, so the fresh
    run has no MCP destinations. Carrying the last real publish over keeps the
    Docs/Gmail links on the page pointing at genuine published artifacts.
    """
    path = PUBLIC_DIR / "data.json"
    if not path.is_file():
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            previous = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {}
    return previous if isinstance(previous, dict) else {}


def build():
    runs = _run_dirs()
    if not runs:
        raise SystemExit(
            f"No pipeline runs found in {OUTPUT_DIR!r}. "
            "Run `python run_pipeline.py --offline` before building the dashboard."
        )

    # Prefer the newest run that was published through MCP so the Docs/Gmail
    # links on the page always point at a real publish.
    for run_dir in runs:
        data = _load(run_dir)
        if data["published"]:
            break
    else:
        data = _load(runs[0])

    data["publish_log"] = _dedupe(data["publish_log"])
    data["published"] = bool(data["publish_log"])

    if not data["published"]:
        previous = _previous_dashboard()
        carried = _dedupe(
            [r for r in previous.get("publish_log") or []
             if isinstance(r, dict) and r.get("destination")]
        )
        if carried:
            data["publish_log"] = carried
            data["published"] = True
            data["published_run_id"] = previous.get("run_id", "")
            # The approval belongs to the run that was actually published.
            if previous.get("approval"):
                data["approval"] = previous["approval"]
            print(f"Carried forward {len(carried)} MCP destination(s) from {data['published_run_id']}")

    PUBLIC_DIR.mkdir(exist_ok=True)
    with open(PUBLIC_DIR / "data.json", "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)

    print(f"Run: {data['run_id']} (published={data['published']}, "
          f"{len(data['publish_log'])} destination(s))")
    print(f"Wrote {PUBLIC_DIR / 'data.json'}")
    return data


if __name__ == "__main__":
    build()
