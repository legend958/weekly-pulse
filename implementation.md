# Implementation Guide: Weekly Product Pulse

**Status:** Reference guide
**Date:** 2026-10-16
**Companion docs:** `prd.md` (requirements R1–R17, acceptance criteria AC1–AC8),
`architecture.md` (stages A–G, data contracts, config) — this guide is the
**build order** for that architecture.
**Runtime:** Python 3.10+ · **standard library only** · no network or API keys needed
for a full offline run.

---

## 1. How to use this guide

The work is split into **12 dependency-ordered phases**. Each phase is a
self-contained increment: when it is done you can run something and check it.

Two ways to use it:

- **Greenfield replay** — build the phases in order in a clean clone. Each phase
  lists the files to create and the exact command that proves it works.
- **Checklist / audit** — the reference implementation already exists in this repo,
  so walk the phases as a review checklist. The **Status** column tells you what is
  already done and what is still open.

Every phase has the same shape:

| Section | Meaning |
|---|---|
| **Goal** | the single outcome of the phase |
| **Refs** | architecture stages and PRD requirement/AC numbers |
| **Depends on** | phases that must exist first |
| **Deliverable** | files/artifacts produced |
| **Tasks** | ordered checklist |
| **Implementation notes** | the load-bearing decisions and traps |
| **Verify** | command(s) + expected result |
| **Done when** | acceptance condition |
| **Status** | ✅ in repo · ⏳ open |

**Status legend:** ✅ done and verified · ⏳ not yet done.

---

## 2. Phase overview

| # | Phase | Arch stage | PRD / AC | Depends on | Size | Status |
|---|---|---|---|---|---|---|
| 0 | Foundations, contracts & config | — | all | — | S | ✅ |
| 1 | Dataset & fixtures | — | data profile | 0 | M | ✅ |
| 2 | Ingest & clean | A | R1 | 0,1 | S | ✅ |
| 3 | Theme clustering | B | R2, R3 / AC1 | 2 | M | ✅ |
| 4 | Insight extraction | C | R3, R4, R5 / AC2–AC4 | 3 | M | ✅ |
| 5 | Generators (pulse + fee explainer) | D1, D2 | R6–R14 / AC5, AC6 | 4 | M | ✅ |
| 6 | Validation gate | E | AC1–AC6 | 5 | S | ✅ |
| 7 | Human approval gate | F | R16 | 6 | S | ✅ |
| 8 | MCP publish (mock → n8n) | G | R15, R17 | 7 | L | ✅ |
| 9 | Orchestrator, CLI & artifacts | — | all | 2–8 | S | ✅ |
| 10 | Tests & reproducibility | — | AC8 | 9 | M | ✅ |
| 11 | Live n8n rollout | G | R15, R17 | 8 | M | ⏳ |

Phases 0–10 are implemented in this repo and pass offline (`18` tests). **Phase 11
is the only open phase** — it is blocked on your n8n workflow, not on code.

---

## 3. Environment & conventions

- **Run everything from the repo root** (`Weekly Product Pulse/`).
- **Import style:** the package lives in `src/pulse/`; entry points put `src/` on
  `sys.path` and import as `from pulse.<module> import ...`. Keep this convention in
  every script so relative imports (`from .config import Config`) keep working.
- **Windows console is cp1252.** The `₹` and `★` characters must not crash stdout:
  entry points reconfigure stdout to UTF-8 (`sys.stdout.reconfigure(encoding="utf-8")`).
  Files are always written UTF-8 regardless of console. **Ad-hoc `python -c` snippets
  do not get that protection** — either set `$env:PYTHONIOENCODING="utf-8"` first, or
  print ASCII only. The snippets in this guide are written to avoid `₹`/`★`.
- **Determinism:** dataset seed is `20261016`; the default `llm_backend` is
  `offline`, so a run is byte-stable without keys. `--offline` forces this.
- **Write isolation:** external writes happen *only* in Stage G, *only* when the
  approval state is `approved` (architecture §2 design principles).

---

## Phase 0 — Foundations, contracts & config

**Goal.** An importable package with all data contracts and runtime config, so every
later phase can be written and tested against fixed interfaces.
**Refs.** architecture §3 (components), §4 (data contracts), §7 (config).
**Depends on.** — · **Size.** S

**Deliverable.** `src/pulse/__init__.py`, `src/pulse/models.py`,
`src/pulse/config.py`, `src/pulse/utils.py`, root shim `run_pipeline.py`,
`.gitignore`.

**Tasks**

- [ ] Create the package dir `src/pulse/` with `__init__.py`.
- [ ] `models.py` — the dataclasses from architecture §4: `Review`,
      `QualityReport`, `Theme` (+ `score` property), `ThemeSet`, `Quote`,
      `FeeConfusion`, `Insights`, `ProductPulse`, `FeeExplainer`,
      `ValidationReport`, `PublishRecord`, `RunResult`, and the `ApprovalState` /
      `Outcome` `Literal` aliases.
- [ ] `config.py` — `Config` dataclass + `from_env()`.
- [ ] `utils.py` — `stable_id(url)`, `iso_week(date)`, `word_count(text)`.
- [ ] Root shim `run_pipeline.py` — insert `src/` on `sys.path`, call
      `pulse.run_pipeline.main`.
- [ ] `.gitignore` — `output/`, `__pycache__/`, `*.pyc`, `.env`.

**Implementation notes**

- **stdlib dataclasses, not pydantic.** Zero install friction; the pipeline runs
  anywhere Python 3.10+ exists. Contracts are `@dataclass` with
  `from __future__ import annotations`.
- **`Theme.score = count * (6.0 - avg_rating)`** encodes PRD R3's "volume ×
  severity". Keep it a *property* so every stage that sorts themes agrees.
- **Defaults that make the pipeline runnable with no setup:**
  `llm_backend="offline"`, `mcp_transport="mock"`. Live behaviour is opt-in.
- All tunables (theme cap, word cap, fee link allowlist, MCP tool names and
  endpoint, timeout, auto-approve) live in `Config` and are overridable via
  `PULSE_*` env vars — nothing is hardcoded downstream.

**Verify**

```powershell
python -c "import sys; sys.path.insert(0,'src'); import pulse.models, pulse.config; print('contracts ok')"
```

**Done when.** Contracts import cleanly and `Config.from_env()` reads `PULSE_*`
variables. **Status.** ✅

---

## Phase 1 — Dataset & fixtures

**Goal.** One reproducible reviews CSV covering 8–12 weeks, plus ground-truth stats
the later stages are validated against.
**Refs.** PRD §3 data profile (R1 input), architecture §10 (golden fixture).
**Depends on.** 0 · **Size.** M

**Deliverable.** `groww-reviews.csv`, `docs/dataset_stats.json`,
`docs/groww-reviews.original.csv`, `scripts/generate_mock_reviews.py`.

**Tasks**

- [ ] Back up any original CSV to `docs/groww-reviews.original.csv`.
- [ ] Write `scripts/generate_mock_reviews.py` with `SEED = 20261016`.
- [ ] Emit the **exact 9-column Play Store header** — no extra columns.
- [ ] Generate ~700 rows over 12 weeks (2026-07-24 → 2026-10-16) across **five
      issue themes** + a praise/background band.
- [ ] Plant the **three verbatim fee quotes** (below) and make weekly fee mentions
      **rise** over the period.
- [ ] Write `docs/dataset_stats.json` (counts, averages, `weekly_fee_mentions`).

**Implementation notes (traps)**

- **Reproducibility must cover the review IDs.** Build `Review URL` from a stable
  per-row index with `uuid.uuid5`, **never** `uuid.uuid4` — otherwise every
  regeneration rewrites the URL column and the dataset is not reproducible from
  `SEED`. With uuid5, running the generator twice yields a byte-identical CSV.
- **Anchor output paths to the repo root** (derived from `__file__`), not the
  current working directory, so the script writes `groww-reviews.csv` and
  `docs/dataset_stats.json` wherever it is invoked from.
- **Header is fixed:** `Date, Rating, Title, Review, App version, Helpful votes,
  Developer reply, Reply date, Review URL`. The source file must **not** carry a
  theme column — themes are derived, never read.
- **Review texts must be ~unique** (target: ~697 distinct of 700) so theme counts
  reflect real volume, not templated duplicates.
- **Fee theme has `min rating = 2`** — no 1★ fee reviews; keeps rating/text
  coherent.
- **Plant these exact quotes** (used by AC3/fee explainer):
  - `Brokerage is only Rs 20, but the other charges quietly take away a big part of my profit, and nothing in the app explains how much I will actually pay.`
  - `I was shocked to see how much was deducted from my small trade after all the extra charges were added on top of the brokerage.`
  - `Please show me a clear breakup of every charge before I place an order, because right now the final cost is always a surprise.`
- Target profile: 700 rows; 5★ 53 · 4★ 139 · 3★ 301 · 2★ 143 · 1★ 64 (avg 2.96);
  fee mentions ≈150, rising ≈5/wk (W30) → ≈19/wk (W40).

**Verify**

```powershell
python scripts/generate_mock_reviews.py
python scripts/verify_dataset.py
```

> Re-running the generator must produce an **identical file** — compare
> `Get-FileHash groww-reviews.csv` across two runs. This is the AC8 reproducibility
> guard for the dataset itself.

**Done when.** `groww-reviews.csv` has 700 rows spanning the date range, fee
mentions rise over time, and `docs/dataset_stats.json` matches the PRD §3 table.
**Status.** ✅

---

## Phase 2 — Stage A: Ingest & Clean

**Goal.** CSV → `list[Review]` + `QualityReport`, failing loudly on schema drift.
**Refs.** architecture §5 Stage A · PRD R1.
**Depends on.** 0, 1 · **Size.** S

**Deliverable.** `src/pulse/ingest.py`.

**Tasks**

- [ ] Define `EXPECTED_HEADER` (the 9 columns) and `IngestError`.
- [ ] `ingest(config) -> tuple[list[Review], QualityReport]`:
      parse with `csv.DictReader` (UTF-8); **validate the header exactly**; parse
      `Date`; coerce `Rating` to int 1–5; strip whitespace; drop empty `Review`.
- [ ] Dedupe near-identical texts (normalized whitespace + lowercase key); count
      duplicates removed.
- [ ] Derive stable `Review.id` = short hash of `Review URL`.
- [ ] Abort with a clear `IngestError` if fewer than 30 rows survive.
- [ ] Emit `QualityReport` (rows, date range, rating mix, duplicates, empties,
      `header_ok`).

**Implementation notes**

- `_norm()` (lowercase + collapse whitespace) is used **only for the dedupe key** —
  never overwrite `Review.text`, because quotes must stay verbatim (AC3).
- Dedupe is 3 rows in this dataset → 697 reviews. Don't "fix" that number; it is
  expected.

**Verify**

```powershell
python -c "import sys; sys.path.insert(0,'src'); from pulse.config import Config; from pulse.ingest import ingest; rs,q=ingest(Config()); print(len(rs), q.rows, q.duplicates_removed, q.header_ok)"
```

**Done when.** ~697 reviews, `header_ok=True`, duplicates ≈3. **Status.** ✅

---

## Phase 3 — Stage B: Theme Clustering

**Goal.** Assign every review to one of ≤5 issue themes; track praise separately.
**Refs.** architecture §5 Stage B · PRD R2, R3 · AC1.
**Depends on.** 2 · **Size.** M

**Deliverable.** `src/pulse/cluster.py`.

**Tasks**

- [ ] `THEME_DEFS` — the five issue themes, each with a summary + keyword regex:
      `Fees & charges` · `KYC & onboarding` · `Withdrawals & settlements` ·
      `App performance & stability` · `UI/UX & feature requests`.
- [ ] `classify(text) -> str | None` — word-boundary keyword match, one theme or
      `None`.
- [ ] `_assign_deterministic(reviews)` — returns buckets + praise + other.
- [ ] `cluster(reviews, config, llm)` — build `Theme` objects, sort by count, cap at
      `config.max_themes`, return `ThemeSet`.
- [ ] `_try_llm(...)` — a no-op hook until a real LLM backend is configured.

**Implementation notes (traps — this is the fragile stage)**

- **Praise is not a theme.** 4–5★ reviews with no issue content go to
  `ThemeSet.background_praise` and are excluded from the theme list, so the
  **issue-theme cap of 5** holds (AC1).
- **Regex must use word boundaries.** `fee` must not match `feedback`; include the
  plural `deductions`.
- **Withdrawal matcher must be a stem:** `\bwithdr\w*` so `withdrew`, `withdrawal`,
  `withdrawing` all land in the same theme.
- Add the non-obvious performance phrasings users actually use, e.g. *"closes by
  itself"* and *"switch between sections"*.
- **List order breaks ties:** in `THEME_DEFS`, earlier themes win equal scores. The
  final ordering is by count descending, so the top 3 here are `App performance &
  stability`, `Fees & charges`, `KYC & onboarding` (fees is **#2**).
- Classification is the **single source of theme counts**; if counts drift, fix the
  regex here, not the dataset.

**Verify**

```powershell
python scripts/diag_classify.py
```

**Expected** (within dedupe tolerance of `docs/dataset_stats.json`): performance
≈159–160, fees ≈149–150, KYC 120, withdrawals 115, UI/UX ≈114–115.

**Done when.** Exactly ≤5 themes with those counts, praise excluded. **Status.** ✅

---

## Phase 4 — Stage C: Insight Extraction

**Goal.** Top-3 themes, three verbatim quotes, one data-derived fee confusion, and a
weekly trend.
**Refs.** architecture §5 Stage C · PRD R3, R4, R5 · AC2–AC4.
**Depends on.** 3 · **Size.** M

**Deliverable.** `src/pulse/insights.py`.

**Tasks**

- [ ] `_top_themes(theme_set, n=3)` — top 3 by `Theme.score` desc.
- [ ] `_pick_quotes(top, by_id)` — one verbatim quote per top theme; prefer high
      `Helpful votes`, mid-length text; carry `review_id` + `url`.
- [ ] `_detect_fee_confusion(reviews, config)` — fee vocabulary (word-boundary),
      require ≥ `config.fee_min_mentions`, label
      `"₹20 brokerage vs total charges"`, collect `evidence_ids`.
- [ ] `_weekly_trend(reviews, theme_names)` — per-week counts for the observation.
- [ ] `extract_insights(reviews, theme_set, config) -> Insights`.

**Implementation notes**

- **Quotes are copied, never paraphrased** (AC3) — take `Review.text` verbatim and
  keep `review_id`/`url` for traceability.
- **Fee confusion must be derived from content** (AC4). If the vocabulary hits
  nothing, return `FeeConfusion(detected=False, …)`; the explainer is then skipped —
  never fabricate a fee.
- Keep `weekly_trend` keyed by theme name so the pulse's "key observation" can cite
  the rise (W30→W40).

**Verify**

```powershell
python -c "import sys; sys.path.insert(0,'src'); from pulse.config import Config; from pulse.ingest import ingest; from pulse.cluster import cluster; from pulse.insights import extract_insights; rs,_=ingest(Config()); ins=extract_insights(rs, cluster(rs, Config()), Config()); print([t.name for t in ins.top_themes]); print('FEE', ins.fee_confusion.detected, ins.fee_confusion.mention_count, len(ins.fee_confusion.evidence_ids))"
```

> Note: the label itself contains `₹`, so this snippet prints `detected`/counts
> rather than the label — that keeps it safe on the cp1252 console.

**Expected:** `['App performance & stability', 'Fees & charges', 'KYC & onboarding']`
(fees is **#2**, per the agreed ranking); `FEE True 149 <n>` — `mention_count` is
≈149 while `evidence_ids` is a small capped sample of those.

**Done when.** 3 ordered top themes, 3 verbatim quotes, fee confusion with evidence,
populated `weekly_trend`. **Status.** ✅

---

## Phase 5 — Stage D: Generators (Pulse + Fee Explainer)

**Goal.** A ≤250-word pulse with four sections, and a 6-bullet fee explainer tied to
the detected confusion.
**Refs.** architecture §5 Stages D1/D2 · PRD R6–R14 · AC5, AC6.
**Depends on.** 4 · **Size.** M

**Deliverable.** `src/pulse/pulse.py`, `src/pulse/fee_explainer.py`.

**Tasks**

- [ ] `generate_pulse(insights, config, llm=None) -> ProductPulse` — sections
      `themes`, `quotes`, `observation`, `actions`.
- [ ] `ACTION_MAP` — a concrete action per theme, plus a generic fallback; **exactly
      three** actions.
- [ ] `_observation(insights)` — one factual line from the weekly trend.
- [ ] `build_fee_explainer(insights, config, llm=None) -> FeeExplainer | None` —
      returns `None` when no fee confusion; else 6 bullets from the facts sheet
      (₹20 brokerage/order; stamp duty; exchange transaction charges; SEBI turnover
      fees; GST; DP/delivery charges), 2 allowlisted links, `last_checked`, and
      `source_review_ids`.
- [ ] `render_fee_explainer(ex)` — Markdown renderer (bullets, sources, `Last
      checked:`).

**Implementation notes**

- **Exactly 6 bullets and exactly 2 links** — the facts sheet and the link list are
  fixed-length on purpose (AC6); don't derive the count dynamically.
- **Both outputs trace to the same reviews** (R14): the explainer consumes the
  `FeeConfusion` from Stage C, and its `source_review_ids` are those evidence IDs.
- **Neutral, facts-only tone** — no apology, no upsell (R11).
- The generative path takes **only `Insights`**, never the raw corpus, to shrink the
  hallucination surface (architecture §6).

**Verify**

```powershell
python -c "import sys; sys.path.insert(0,'src'); from pulse.config import Config; from pulse.utils import word_count; from pulse.run_pipeline import run; r=run(Config(), 'pending', quiet=True); print('words', word_count(r.pulse.text)); print('bullets', len(r.fee_explainer.bullets), 'links', len(r.fee_explainer.links))"
```

**Done when.** Pulse ≤250 words with all four sections and 3 actions; explainer has 6
bullets, 2 links, `Last checked`. **Status.** ✅

---

## Phase 6 — Stage E: Validation Gate

**Goal.** A deterministic pass/fail gate over AC1–AC6 that runs *before* any human
sees the drafts.
**Refs.** architecture §5 Stage E · AC1–AC6.
**Depends on.** 5 · **Size.** S

**Deliverable.** `src/pulse/validators.py`.

**Tasks**

- [ ] `validate(reviews, theme_set, insights, pulse, fee_explainer, config) ->
      ValidationReport`.
- [ ] Implement each rule from the architecture table: theme cap; top-3 ordered;
      quotes verbatim + URL; fee grounded (and explainer absent when no fee); pulse
      word cap + 4 sections + 3 actions; explainer 6 bullets / 2 links / last-checked;
      **link allowlist**; word-boundary fee terms.

**Implementation notes**

- **Fail closed:** any failure means `ok=False`, and the orchestrator then performs
  **zero** publishes.
- The allowlist check parses the host and accepts `groww.in` and its subdomains —
  so a fabricated link fails the run rather than reaching a customer.

**Verify**

```powershell
python -c "import sys; sys.path.insert(0,'src'); from pulse.config import Config; from pulse.run_pipeline import run; r=run(Config(), 'pending', quiet=True); print(r.validation.ok, r.validation.failures)"
```

**Done when.** `ok=True` with no failures on the real dataset, and a deliberately
corrupted draft flips it to `ok=False`. **Status.** ✅

---

## Phase 7 — Stage F: Human Approval Gate

**Goal.** A hard state machine that gates all publishing, plus a persisted approval
artifact.
**Refs.** architecture §5 Stage F · PRD R16.
**Depends on.** 6 · **Size.** S

**Deliverable.** `src/pulse/approval.py`.

**Tasks**

- [ ] `decide(config, decision, validation, approval_path) -> (state, reviewer)`.
- [ ] States: `draft → pending_approval → approved → published`; `rejected → draft`.
- [ ] Write `approval.json` (state, reviewer, `decided_at`, `validation_ok`,
      `failures`).
- [ ] Interactive `y/N` prompt **only** on a TTY; otherwise default to
      `pending_approval`.
- [ ] `--auto-approve` requires **both** the flag and `ALLOW_AUTO_APPROVE=1`.

**Implementation notes (traps)**

- **Validation failure ⇒ `rejected` (reviewer `validator`)**, regardless of the flag.
- A non-TTY stdin (CI, pipes) must **not** hang or silently approve — it returns
  `pending_approval` (handle `EOFError` too).
- Treat an explicit `pending` decision as `pending_approval` so the CLI flag, the
  interactive prompt and the auto path all produce the same states.

**Verify**

```powershell
python -c "import sys; sys.path.insert(0,'src'); from pulse.config import Config; from pulse.models import ValidationReport; from pulse.approval import decide; print(decide(Config(), 'approve', ValidationReport(True), 'output/_probe/approval.json')[0]); print(decide(Config(), 'pending', ValidationReport(True), 'output/_probe/approval.json')[0]); print(decide(Config(), None, ValidationReport(False), 'output/_probe/approval.json')[0])"
```

**Expected:** `approved`, `pending_approval`, `rejected`.

**Done when.** Publishing is impossible unless the state is exactly `approved`.
**Status.** ✅

---

## Phase 8 — Stage G: MCP Publish (mock → live n8n)

**Goal.** Two approval-gated MCP outcomes — append to the notes doc, create a Gmail
**draft** — with an offline mock and a live n8n client, and a recorded destination
per call.
**Refs.** architecture §5 Stage G · PRD R15, R17 · `docs/mcp-n8n.md`.
**Depends on.** 7 · **Size.** L

**Deliverable.** `src/pulse/publish_mcp.py`, `scripts/mock_mcp_server.py`,
`docs/mcp-n8n.md`, `docs/n8n-weekly-pulse-mcp.json`.

**Tasks**

- [ ] `MCPClient` base with `call_tool(name, args) -> {"destination", "detail",
      "raw"}`.
- [ ] `MockMCPClient` — records calls, returns synthetic destinations (tests/CI).
- [ ] `N8NMCPClient` — streamable-HTTP JSON-RPC: `initialize` →
      `notifications/initialized` → `tools/call`; send `Authorization: Bearer`; echo
      `Mcp-Session-Id`; accept `application/json` **or** `text/event-stream`.
- [ ] Destination parsing — read `structuredContent` **or** a JSON text block, then
      fall back to scanning for an `https://` URL, then to a
      `<outcome>://<run_id>` placeholder.
- [ ] `publish(state, pulse, fee_explainer, config, run_id) -> list[PublishRecord]`
      — **hard gate** (`state != "approved"` returns `[]`); two outcomes:
      `append_note` (`pulse` artifact) and `create_email_draft` (`fee_explainer`
      artifact).
- [ ] Bounded **retry with exponential backoff** around each tool call
      (`Config.mcp_max_retries`, `Config.mcp_backoff_seconds`); surface the final
      error only after the last attempt.
- [ ] `_compose_packet`, `_note_arguments`, `_email_arguments`.
- [ ] Local stand-in server `scripts/mock_mcp_server.py` (`--once` prints the exact
      tool-call payloads).
- [ ] Contract doc `docs/mcp-n8n.md` + importable workflow
      `docs/n8n-weekly-pulse-mcp.json`.

**Implementation notes**

- **One outcome failing must not block the other** — record `outcome="error"` with the
  message and continue.
- **Retries are bounded and idempotent-friendly.** Every call carries `run_id`, the
  documented dedupe key on the n8n side, so a retry after a timeout cannot
  double-append silently. Backoff is exponential (`base * 2**attempt`), and the
  final `MCPError` is surfaced as `outcome="error"`.
- **The email outcome is a draft only, never a send** (R16 keeps a human in the loop).
- Tool arguments for the note are `title`, `content` (pulse ⧺ `---` ⧺ fee explainer),
  `run_id`, and optional `target`; for the draft, `subject`, `body`, `run_id`, and
  optional `to`. Extra keys are harmless — n8n's tool schema ignores unknown
  arguments.
- Live n8n specifics (verified against n8n source): tools are bound to arguments via
  `$fromAI('key')` in the node fields; the trigger connects tool nodes over the
  `ai_tool` connection; use a **Bearer** credential.

**Verify (offline)**

```powershell
python -c "import sys; sys.path.insert(0,'src'); from pulse.config import Config; from pulse.run_pipeline import run; r=run(Config(), 'approve', quiet=True); print([(p.outcome, p.tool, p.destination) for p in r.publish_records])"
```

**Expected:** two records — `append_note via append_to_note` and
`create_email_draft via create_email_draft`.

**Verify (local live-style server)**

```powershell
python scripts/mock_mcp_server.py --once
```

**Done when.** Approval yields exactly two records; `pending`/`rejected`/validation
failure yield zero. **Status.** ✅

---

## Phase 9 — Orchestrator, CLI & artifacts

**Goal.** One command wires A→G, writes all run artifacts, and exposes the CLI flags.
**Refs.** architecture §9 (artifacts), §13 (running).
**Depends on.** 2–8 · **Size.** S

**Deliverable.** `src/pulse/run_pipeline.py` + root shim.

**Tasks**

- [ ] `run(config, decision, quiet) -> RunResult` — call stages in order.
- [ ] `run_id = <UTC timestamp>-<short hash>`; write to `output/<run-id>/`.
- [ ] Write artifacts: `reviews.normalized.jsonl` (Stage A: one review per line +
      a trailing `{"type": "quality_report", …}` line), `themes.json`,
      `insights.json`, `pulse.md`, `fee_explainer.md`, `validation.json`,
      `run_meta.json`, `approval.json`, `publish_log.json`.
- [ ] `main(argv)` — argparse: `--offline`, `--decision
      approve|reject|pending`, `--auto-approve`, `--input`, `--output-dir`,
      `--mcp-transport mock|http`, `--mcp-endpoint`, `--mcp-token`, `--quiet`.
- [ ] Print a summary; reconfigure stdout to UTF-8.

**Implementation notes**

- **Ordering guarantees the gate:** artifacts are written *before* approval so a
  rejected run still leaves a reviewable draft; publishing happens only after
  `decide`, and only when `validation.ok` **and** state is `approved`.
- `run_meta.json` records backend, model, seed, input, MCP transport/endpoint and
  tool names — this is the AC8 reproducibility record.

**Verify**

```powershell
python run_pipeline.py --offline --decision approve
python run_pipeline.py --offline --decision pending
```

**Done when.** One command runs end-to-end and the second produces no
`PublishRecord`s. **Status.** ✅

---

## Phase 10 — Tests & reproducibility

**Goal.** Lock the behavior offline so later changes can't silently break an AC.
**Refs.** architecture §10 · AC8.
**Depends on.** 9 · **Size.** M

**Deliverable.** `tests/test_pipeline.py`.

**Tasks**

- [ ] Unit: `ingest` (header/dedupe/quality), `validators` (every AC rule, incl.
      word-boundary fee matching), `insights` (fee detection on fixtures).
- [ ] Golden: full `--offline` run vs `docs/dataset_stats.json` (≈150 fee rows, 5
      themes, stable top 3).
- [ ] Contract: `len(themes) <= 5`; 3 verbatim quotes present in source; 6 bullets /
      2 links / `Last checked`; pulse ≤250 words.
- [ ] Approval: publish impossible unless `approved`; `rejected` ⇒ 0 records.
- [ ] Integration: a fake HTTP MCP server exercising handshake, bearer auth, session
      echo, error surfacing, and text-block destination parsing.

**Verify**

```powershell
python -m unittest discover -s tests
```

**Done when.** All tests green with no network and no keys. **Status.** ✅ (20 tests)

---

## Phase 11 — Live n8n rollout ⏳

**Goal.** Point Stage G at the real n8n MCP Server Trigger and confirm both outcomes
land.
**Refs.** architecture §5 Stage G · `docs/mcp-n8n.md`.
**Depends on.** 8 · **Size.** M

**Tasks**

- [ ] **Fast path (no UI clicking):** `python scripts/setup_n8n_mcp.py` provisions
      everything through the n8n public API — Bearer credential, workflow, publish —
      using `PULSE_N8N_BASE_URL` / `PULSE_N8N_API_KEY` from `.env.local`. Use
      `--check` (read-only) and `--dry-run` first.
- [ ] Or do it by hand: import the workflow JSON — either
      `docs/n8n-weekly-pulse-mcp.json` (minimal) or `docs/mcp_workflow_v2.json`
      (your Milestone 5 flow with an MCP trigger added; keeps
      Webhook → Wait → Docs/Gmail and reuses your credentials).
- [ ] Set the trigger **Authentication = Bearer Auth**; create the **generic
      `Bearer Auth`** credential (a single "Bearer Token" field — *not* GitHub or
      any service OAuth) whose token equals `PULSE_MCP_TOKEN`.
- [ ] Attach Google credentials to `append_to_note` and Gmail credentials to
      `create_email_draft`; leave `REPLACE_WITH_YOUR_GOOGLE_DOC_ID` only in the
      minimal file — v2 already points at your doc.
- [ ] **Activate** the workflow; copy the **Production** URL.
- [ ] **Static check:** `python scripts/check_n8n_workflow.py docs/mcp_workflow_v2.json`
      (trigger, tool names, `ai_tool` wiring, `$fromAI` args, credentials).
- [ ] **Preflight:** `python scripts/check_n8n_endpoint.py --endpoint <url> --token <token>`
      (handshake + `tools/list`; publishes nothing).
- [ ] Run the pipeline against it (see below).
- [ ] Inspect `output/<run-id>/publish_log.json`: both outcomes should show a real
      destination and the Gmail draft should exist.

**Implementation notes / known caveats**

- **URL shape:** production `https://<host>/mcp/<path>` (path `weekly-pulse`); the
  `/mcp-test/<path>` URL only answers while you click *Listen for Test Event*.
- **Reverse proxies must not buffer** the MCP endpoint.
- **Doc URL destination.** Google Docs' `document:update` returns an empty
  `batchUpdate` body. The pipeline derives a canonical doc URL from
  `PULSE_MCP_NOTE_TARGET` when a note outcome returns no identifier; set it to
  your doc ID/URL (e.g. `PULSE_MCP_NOTE_TARGET=1AbCdEf...`) to log a real,
  clickable URL in `publish_log.json`. (Optional: for fully dynamic IDs, wrap
  the append in an n8n "Execute Workflow" / "Call n8n Workflow Tool" sub-workflow
  that returns `{ "url": … }`.)
- **n8n replies are JSON arrays.** n8n tool output arrives as a text block
  containing an array (`[{"documentId": …}]`), not a bare object. The pipeline
  flattens that shape, renders a returned `documentId` as a canonical Docs URL,
  and renders a draft's nested `message.id` as a `mail.google.com/…/#drafts/<id>`
  link — so both destinations are real links rather than fallbacks.
- Keep the tool node names exactly `append_to_note` and `create_email_draft`.

**Verify**

```powershell
$env:PULSE_MCP_TRANSPORT="http"
$env:PULSE_MCP_ENDPOINT="https://<your-n8n>/mcp/weekly-pulse"
$env:PULSE_MCP_TOKEN="<same bearer token>"
$env:PULSE_MCP_NOTE_TARGET="<google-doc-id-or-url>"  # optional but recommended

# Handshake-only first (recommended)
python scripts/check_n8n_endpoint.py

# Then publish
python run_pipeline.py --decision approve
```

**Done when.** `publish_log.json` shows a `create_email_draft` draft id and an
`append_note` destination, and the draft + appended section exist in Google.
**Status.** ✅ live-verified against `https://john008.app.n8n.cloud/mcp/weekly-pulse`
(both outcomes landed: doc `10wnp8Qe…` appended, Gmail draft `r1687169641787871004`
created with `labelIds: ["DRAFT"]`). The handshake + `tools/list` preflight pass;
destinations now resolve to real links from the n8n array payloads. Remember to
**Publish** the workflow after any edit — editing deactivates it and the
production URL then 404s.

---

## Appendix A — Critical decisions & traps

Distilled from building Phases 1–10. These are the things most likely to cost time:

1. **Themes are derived, never stored.** The CSV keeps the 9 Play Store columns only.
2. **Praise is background, not a theme** — otherwise the ≤5 cap breaks.
3. **Regex word boundaries are load-bearing** (`fee` ≠ `feedback`); include the
   plural `deductions`, and use the stem `\bwithdr\w*` for withdrawals.
4. **Dedupe must not touch `Review.text`** — quotes have to stay verbatim (AC3).
5. **Fee confusion must be data-derived**; absence is reported, not invented (AC4).
6. **Fixed counts are intentional:** 3 top themes, 3 quotes, 3 actions, 6 bullets,
   2 links. Don't make them dynamic.
7. **Gate order:** validation → approval → publish. Nothing bypasses it, including
   `--auto-approve` (which additionally needs `ALLOW_AUTO_APPROVE=1`).
8. **The email is a draft, never a send.**
9. **One MCP outcome failing doesn't block the other.**
10. **Windows/cp1252:** reconfigure stdout to UTF-8 so `₹`/`★` don't crash a run.
11. **Deterministic by default:** seed `20261016`, `offline` backend, `mock`
    transport — full run with no network or keys.

---

## Appendix B — Acceptance criteria → phase

| AC | Meaning | Proven in |
|---|---|---|
| AC1 | ≤5 themes | Phase 3 (+ Phase 6) |
| AC2 | top 3 ranked with counts/severity | Phase 4 (+ 6) |
| AC3 | 3 verbatim quotes with URLs | Phase 4 (+ 6) |
| AC4 | fee confusion from content, else "none detected" | Phase 4 (+ 6) |
| AC5 | pulse ≤250 words, 4 sections | Phase 5 (+ 6) |
| AC6 | explainer 6 bullets / 2 links / `Last checked` | Phase 5 (+ 6) |
| AC7 | no write without approval | Phase 7, 8 |
| AC8 | reproducible structure | Phase 9 (`run_meta.json`), Phase 10 |

---

## Appendix C — Command cheat-sheet

```powershell
# Full offline run (publishes via the mock MCP client)
python run_pipeline.py --offline --decision approve

# Draft only, nothing published
python run_pipeline.py --offline --decision pending

# Tests (offline, no keys)
python -m unittest discover -s tests

# Dataset maintenance
python scripts/generate_mock_reviews.py     # regenerate groww-reviews.csv + stats
python scripts/verify_dataset.py            # coherence + fee-signal checks
python scripts/diag_classify.py             # per-theme classification counts

# Local n8n stand-in: print exact tool-call payloads
python scripts/mock_mcp_server.py --once

# Live n8n publish
python scripts/check_n8n_workflow.py docs/mcp_workflow_v2.json  # static contract check
python scripts/check_n8n_endpoint.py                        # preflight (handshake + tools/list)
python run_pipeline.py --decision approve --mcp-transport http `
  --mcp-endpoint $env:PULSE_MCP_ENDPOINT --mcp-token $env:PULSE_MCP_TOKEN
```

Artifacts land in `output/<run-id>/` (see architecture §9).

---

## Appendix D — Optional backlog (not required by the milestone)

- Real LLM backends behind `llm.py` (`gateway` / `direct`); the deterministic
  fallback is the default and the offline path stays authoritative.
- Returning the real Google Doc URL from the note outcome (Call n8n Workflow Tool
  pattern, Phase 11 caveat) — the configured-target fallback already yields a
  canonical URL when `PULSE_MCP_NOTE_TARGET` is set; the sub-workflow pattern
  remains optional for dynamic doc IDs.
