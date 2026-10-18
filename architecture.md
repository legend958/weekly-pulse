# Architecture: Weekly Product Pulse — AI Review-to-Insight Workflow

**Status:** Draft
**Date:** 2026-10-16
**Companion doc:** `prd.md` (requirements R1–R17, acceptance criteria AC1–AC8)
**Source data:** `groww-reviews.csv` (700-row mock dataset, 12 weeks)
**Data generator:** `scripts/generate_mock_reviews.py` → `docs/dataset_stats.json`

---

## 1. Purpose & Scope

This document describes the technical architecture for the workflow defined in
`prd.md`: turn raw customer reviews into (1) a ≤250-word internal **Weekly Product
Pulse**, and (2) a **Fee Explainer** customer communication, then distribute both
through **MCP tools behind a mandatory human approval gate**.

In scope: the offline weekly batch pipeline, its data contracts, validation
guardrails, the approval-gated publish step, and how the system is run/tested.

Out of scope (per PRD §6): real-time scraping, sentiment dashboards, historical
trend storage, auto-publishing, multi-product support.

---

## 2. Architecture at a Glance

```
                         ┌─────────────────────────────────────────────┐
                         │              ORCHESTRATOR (CLI)             │
                         │   run_pipeline.py  (weekly batch entrypoint)│
                         └───────────────┬─────────────────────────────┘
                                         │
   groww-reviews.csv                     ▼
        │                    ┌────────────────────────┐
        └───────────────────►│ 1. INGEST & CLEAN      │  Stage A
                             │    ingest.py           │
                             └───────────┬────────────┘
                                         │ list[Review] (+ quality report)
                                         ▼
                             ┌────────────────────────┐
                             │ 2. THEME CLUSTERING    │  Stage B
                             │    cluster.py (LLM)    │  ≤ 5 themes
                             └───────────┬────────────┘
                                         │ ThemeSet (+ counts, avg rating)
                                         ▼
                             ┌────────────────────────┐
                             │ 3. INSIGHT EXTRACTION  │  Stage C
                             │    insights.py         │  top-3 + quotes + fee signal
                             └───────────┬────────────┘
                                         │ Insights (Topics, Quotes, FeeConfusion)
                     ┌───────────────────┴───────────────────┐
                     ▼                                       ▼
        ┌────────────────────────┐            ┌────────────────────────────┐
        │ 4a. PULSE GENERATOR    │            │ 4b. FEE EXPLAINER GENERATOR│  Stage D
        │   pulse.py (LLM)       │            │   fee_explainer.py (LLM)   │
        └───────────┬────────────┘            └──────────────┬─────────────┘
                    │  ProductPulse (≤250w)                  │ FeeExplainer (6 bullets, 2 links)
                    └───────────────────┬───────────────────┘
                                        ▼
                         ┌───────────────────────────────┐
                         │ 5. VALIDATION GATE            │  Stage E
                         │    validators.py (deterministic)
                         └───────────────┬───────────────┘
                                         ▼
                         ┌───────────────────────────────┐
                         │ 6. HUMAN APPROVAL GATE        │  Stage F
                         │    approval.py (+ artifacts)  │
                         └───────────────┬───────────────┘
                              approved?    │
                              ┌────────────┴────────────┐
                              │ yes                     │ no (reject/edit)
                              ▼                         ▼
                 ┌───────────────────────────┐   ┌──────────────────┐
                 │ 7. MCP PUBLISH            │   │ stop / regenerate │
                 │    publish_mcp.py         │   └──────────────────┘
                 └───────────────┬───────────┘
                                 ▼
                   internal tools (tracker / docs / channel)
                                 │
                                 ▼ PublishRecord (tool + link/id)
```

Design principles:

- **Deterministic vs. generative split.** All LLM output passes a
  deterministic validator (Stage E) before a human ever sees it.
- **Single source of truth for evidence.** Every generated claim is traceable to
  a `Review.url`; the fee explainer must cite the fee-confusion review IDs.
- **Write isolation.** External writes happen *only* in Stage G, *only* after the
  Stage F gate returns `approved`.
- **Fail closed.** If validation fails or approval is missing, nothing is published.

---

## 3. Component Breakdown

| # | Component | Module (target) | Responsibility | PRD refs |
|---|---|---|---|---|
| A | Ingest & Clean | `src/pulse/ingest.py` | Parse CSV, normalize, dedupe, quality report | R1 |
| B | Theme Clustering | `src/pulse/cluster.py` | Cluster reviews into ≤5 themes | R2, R3 |
| C | Insight Extraction | `src/pulse/insights.py` | Rank top-3, pick 3 quotes, detect fee confusion | R3, R4, R5 |
| D1 | Pulse Generator | `src/pulse/pulse.py` | Draft the ≤250-word internal note | R6–R9 |
| D2 | Fee Explainer Generator | `src/pulse/fee_explainer.py` | Draft the 6-bullet fee explanation | R10–R14 |
| E | Validation Gate | `src/pulse/validators.py` | Enforce AC1–AC6 deterministically | AC1–AC6 |
| F | Approval Gate | `src/pulse/approval.py` | Human review state machine + artifacts | R16 |
| G | MCP Publish | `src/pulse/publish_mcp.py` | Log/share via MCP, record destination | R15, R17 |
| — | Orchestrator | `src/pulse/run_pipeline.py` | Wire stages, config, logging | all |
| — | Contracts | `src/pulse/models.py` | Dataclasses / pydantic models | all |
| — | LLM client | `src/pulse/llm.py` | Provider or AI-gateway wrapper | R2,D |
| — | Config | `src/pulse/config.py` | Paths, model, allowlists, thresholds | — |

**Target repository layout**

```
Weekly Product Pulse/
├─ prd.md
├─ architecture.md
├─ implementation.md                 # phase-wise build guide
├─ groww-reviews.csv                 # pipeline input
├─ docs/
│  ├─ problemstatement.txt
│  ├─ mcp-n8n.md                     # Stage G contract + n8n build steps
│  ├─ n8n-weekly-pulse-mcp.json      # importable n8n workflow (minimal)
│  ├─ mcp_workflow_v2.json           # client workflow + MCP trigger added
│  ├─ dataset_stats.json             # ground-truth stats (generator output)
│  └─ groww-reviews.original.csv     # pre-regeneration backup
├─ scripts/
│  ├─ generate_mock_reviews.py       # dataset generator
│  ├─ verify_dataset.py
│  ├─ diag_classify.py
│  ├─ verify_themes.py
│  ├─ check_n8n_workflow.py          # static check of an n8n workflow JSON
│  ├─ setup_n8n_mcp.py               # provision credential + workflow + publish via API
│  ├─ check_n8n_endpoint.py          # Stage G preflight (handshake + tools/list)
│  └─ mock_mcp_server.py             # local MCP stand-in (dev aid)
├─ src/pulse/                        # pipeline (to be implemented)
│  ├─ models.py  config.py  llm.py
│  ├─ ingest.py  cluster.py  insights.py
│  ├─ pulse.py   fee_explainer.py
│  ├─ validators.py  approval.py  publish_mcp.py
│  └─ run_pipeline.py
├─ output/                           # run artifacts (gitignored)
└─ tests/
```

---

## 4. Data Contracts

Plain dataclasses (or pydantic models) in `models.py`. The CSV schema is fixed to
the Play Store export and must not be extended in the source file.

```python
# Stage A input row (matches the CSV header exactly)
ReviewRow = {
    "Date": str, "Rating": int, "Title": str, "Review": str,
    "App version": str, "Helpful votes": int,
    "Developer reply": str, "Reply date": str, "Review URL": str,
}

@dataclass
class Review:            # normalized
    id: str              # derived from Review URL (stable hash)
    date: date
    rating: int
    title: str
    text: str
    url: str
    app_version: str
    helpful_votes: int

@dataclass
class Theme:
    name: str            # short label
    summary: str
    review_ids: list[str]
    count: int
    avg_rating: float

@dataclass
class ThemeSet:          # AC1
    themes: list[Theme]  # len <= 5
    background_praise: int  # non-issue count (excluded from themes)

@dataclass
class Quote:             # AC3
    text: str            # verbatim
    review_id: str
    url: str
    rating: int
    date: date
    theme: str

@dataclass
class FeeConfusion:      # R5
    detected: bool
    label: str           # e.g. "₹20 brokerage vs total charges"
    evidence_ids: list[str]
    mention_count: int

@dataclass
class Insights:
    top_themes: list[Theme]      # exactly 3
    quotes: list[Quote]          # exactly 3
    fee_confusion: FeeConfusion
    weekly_trend: dict[str, int] # theme/week counts for "key observation"

@dataclass
class ProductPulse:      # AC5
    text: str            # <= 250 words
    sections: dict       # themes, quotes, observation, actions(3)

@dataclass
class FeeExplainer:      # AC6
    bullets: list[str]   # exactly 6
    links: list[str]     # exactly 2, official domain
    last_checked: date
    source_review_ids: list[str]

@dataclass
class ValidationReport:  # AC1-AC6
    ok: bool
    failures: list[str]

# Approval + publish
ApprovalState = Literal["draft", "pending_approval", "approved", "rejected", "published"]

@dataclass
class PublishRecord:     # R17
    tool: str
    artifact: Literal["pulse", "fee_explainer"]
    destination: str     # link or ID
    published_at: datetime
    outcome: str = ""    # "append_note" | "create_email_draft" | "error"
    detail: str = ""
```

---

## 5. Stage Design

### Stage A — Ingest & Clean (`ingest.py`, R1)

- Read `groww-reviews.csv` with `csv.DictReader` (UTF-8).
- Validate header equals the expected 9 columns; fail loudly on schema drift.
- Normalize: parse `Date` → `date`; coerce `Rating` to int 1–5; strip whitespace;
  drop rows with empty `Review`.
- Derive stable `id` = short hash of `Review URL` (GUID) — used for traceability.
- **Dedupe near-identical texts** (normalized whitespace + lowercase hash) to
  avoid inflating theme counts; record duplicates removed.
- Emit a **quality report**: row count, date range, rating mix, duplicates,
  empty fields. Cross-check against `docs/dataset_stats.json` when present.
- Output: `list[Review]` + `QualityReport`.

Failure mode: empty/too-small dataset (< 30 rows) → abort with a clear error
(insufficient input), per PRD's "don't fabricate" stance.

### Stage B — Theme Clustering (`cluster.py`, R2, R3)

Approach: **LLM-assisted clustering with a deterministic fallback.**

1. **Bucket** reviews by star band first (negative 1–2★ vs neutral 3★ vs positive
   4–5★) to keep positive praise from dominating issue themes.
2. **LLM clustering** into **≤ 5 issue themes**: send a sampled, deduplicated set
   of negative/neutral review texts (batched) and ask for theme labels +
   assignments; ensemble/settle labels across batches.
3. **Assign** every review to the nearest theme (or `other`).
4. **Background praise**: 4–5★ reviews with no issue content are counted
   separately (`ThemeSet.background_praise`) and do **not** become a theme, so the
   issue-theme cap stays at 5 (AC1, PRD R2).
5. **Deterministic fallback** (if the LLM is unavailable or `--offline`): a
   keyword/taxonomy classifier covering the five known issue families
   (performance, fees/charges, KYC, withdrawals, UI/UX). This guarantees a
   reproducible run and is the mode used by tests.

Output: `ThemeSet` with per-theme counts and avg rating, sorted by
`volume × severity(rating)` (R3).

### Stage C — Insight Extraction (`insights.py`, R3, R4, R5)

- **Top 3 themes** = top 3 of `ThemeSet` by combined volume + severity (AC2).
- **3 quotes** (R4/AC3): pick the most representative *verbatim* review text per
  top theme (prefer high `Helpful votes`, mid-length, no PII); store the exact
  string + `review_id` + `url`. Quotes are copied, never paraphrased.
- **Fee confusion** (R5): search fee/charge vocabulary across reviews
  (`brokerage`, `charges`, `GST`, `stamp duty`, `deduction(s)`, `DP charges`).
  - Cluster the matching reviews by *what is confused* (e.g. "₹20 brokerage vs
    other charges", "hidden deductions", "AMC/DP charges").
  - Select the **largest recurring** cluster → `FeeConfusion(label, evidence_ids,
    mention_count)`.
  - Compute `weekly_trend` (theme counts per week) to support the pulse's "key
    observation" (R8).
  - If no fee/charge cluster exists → `detected=False` (AC4: report "none
    detected" instead of fabricating).
- In this dataset this yields the **"₹20 brokerage vs. total charges"** confusion
  with ~150 supporting reviews, rising W30→W40.

### Stage D1 — Product Pulse (`pulse.py`, R6–R9)

- LLM prompt is given **only** `Insights` (top themes, the 3 quotes, fee signal,
  weekly trend) — no raw corpus — to reduce hallucination surface.
- Produces exactly four sections: **themes**, **quotes**, **key observation**,
  **3 action ideas** (R9 requires exactly three).
- Constrained to ≤ 250 words (enforced again in Stage E).
- Tone: internal, factual, no marketing language.
- Output: `ProductPulse`.

### Stage D2 — Fee Explainer (`fee_explainer.py`, R10–R14)

- LLM prompt is given the `FeeConfusion` label + a curated set of fee-review
  quotes + an **approved facts sheet** (brokerage ₹20/order; stamp duty; exchange
  transaction charges; SEBI turnover fees; GST; DP/delivery charges).
- Emits **exactly 6 bullets**, neutral/facts-only, **exactly 2 official links**
  from an allowlist, and `Last checked: <date>`.
- Must include `source_review_ids` linking back to the reviews that raised the
  confusion (R14 traceability).
- Output: `FeeExplainer`.

### Stage E — Validation Gate (`validators.py`, AC1–AC6)

Deterministic checks run after D1/D2 and before the human gate. Any hard failure
blocks the run.

| Check | Rule | AC |
|---|---|---|
| Theme cap | `len(themes) <= 5` | AC1 |
| Top-3 ranked | exactly 3, ordered, with counts + severity | AC2 |
| Quotes verbatim | each quote string found in a source review; URL non-empty | AC3 |
| Fee grounded | `fee_confusion` derived from reviews; if absent, explainer not generated | AC4 |
| Pulse size | total words ≤ 250; 4 sections present; actions == 3 | AC5 |
| Explainer shape | 6 bullets; 2 links; `Last checked` present | AC6 |
| Link allowlist | each link host ∈ {`groww.in`} | R12 / risk |
| Word-boundary fee terms | fee terms matched with `\b` (avoid "fee" in "feedback") | — |

Output: `ValidationReport(ok, failures)`. On `ok=False`, the run stops and writes
the draft + failures for review (no publish).

### Stage F — Human Approval Gate (`approval.py`, R16)

State machine (persisted to `output/<run-id>/approval.json`):

```
draft ──submit──► pending_approval ──approve──► approved ──publish──► published
                          └──────────reject────► rejected ──edit──► draft
```

- The gate renders both drafts (Pulse + Fee Explainer) plus the evidence
  (quotes, fee evidence IDs, validation report) for a human reviewer.
- Only `approved` unlocks Stage G. `rejected` returns to editing; the pipeline
  never auto-publishes.
- CLI: `--auto-approve` is **explicitly opt-in and non-default** and is refused
  unless an env flag `ALLOW_AUTO_APPROVE=1` is also set (safe demo escape hatch).

### Stage G — MCP Publish (`publish_mcp.py`, R15, R17)

Publishing targets a live **n8n MCP Server Trigger** over streamable HTTP
(JSON-RPC 2.0) with **two outcomes** (contract: `docs/mcp-n8n.md`):

| Outcome | Tool (default) | Payload | Artifact |
|---|---|---|---|
| 1. Append to Notes/Doc | `append_to_note` | title + Markdown packet (pulse ⧺ fee explainer) + `run_id` (+ optional `target`) | pulse |
| 2. Create Email Draft | `create_email_draft` | subject + same packet + `run_id` (+ optional `to`) | fee_explainer |

- Both run **only** on `approved`; each yields a
  `PublishRecord(tool, artifact, outcome, destination, detail, published_at)`,
  appended to `output/<run-id>/publish_log.json`.
- The email outcome creates a **draft only** — never sends — so a human stays in
  the loop after approval.
- Transport is selectable: `mock` (offline/tests) or `http` (n8n). A failed tool
  call is recorded as `outcome="error"` with the message; the other outcome still
  runs. Writes never bypass the Stage F gate.
- Client handshake: `initialize` → `notifications/initialized` → `tools/call`,
  echoing `Mcp-Session-Id`; answers may be JSON or SSE.
- **Preflight:** `MCPClient.list_tools()` runs the handshake + `tools/list`;
  `scripts/check_n8n_endpoint.py` uses it to verify reachability, auth and the
  required tool names *before* any publish (no approval needed).
- **Destination resolution:** use the tool's own identifier (`url`/`draftId`/…);
  if none is returned, fall back to the canonical Google Doc URL derived from
  `MCP_NOTE_TARGET`, else `<tool>://<run_id>`. Every call carries `run_id` so the
  n8n side can dedupe retries.

---

## 6. LLM Integration

- Single abstraction `llm.py` exposing `complete(prompt, schema) -> dict`.
- Two backends: **direct provider API** or an **AI gateway** (one credential,
  model routing) — selectable by config; the pipeline is agnostic.
- **Structured output**: prompts request JSON conforming to the Stage's model;
  responses are parsed and schema-validated before use.
- **Determinism**: run seed, temperature, and model ID are recorded in
  `output/<run-id>/run_meta.json` for reproducibility (AC8). The `--offline`
  fallback needs no LLM at all.
- **Evidence-only prompting**: D1/D2 receive only `Insights`/facts, not raw
  reviews, so quotes must originate from Stage C (verbatim) rather than the LLM.

---

## 7. Configuration (`config.py`)

| Key | Default | Notes |
|---|---|---|
| `INPUT_CSV` | `groww-reviews.csv` | pipeline input |
| `OUTPUT_DIR` | `output/` | per-run artifacts |
| `LLM_BACKEND` | `gateway` | `gateway` \| `direct` \| `offline` |
| `MODEL_ID` | configured | recorded in run meta |
| `MAX_THEMES` | `5` | AC1 |
| `PULSE_MAX_WORDS` | `250` | AC5 |
| `FEE_LINK_ALLOWLIST` | `["groww.in"]` | R12 |
| `MCP_TRANSPORT` | `mock` | `mock` \| `http` (live n8n) |
| `MCP_ENDPOINT` | — | n8n MCP Server Trigger URL |
| `MCP_TOKEN` | — | Bearer token (env only) |
| `MCP_MAX_RETRIES` | `2` | retries after the first attempt (bounded) |
| `MCP_BACKOFF_SECONDS` | `0.5` | exponential backoff base (`base * 2**attempt`) |
| `MCP_TOOL_APPEND_NOTE` | `append_to_note` | outcome 1 |
| `MCP_TOOL_EMAIL_DRAFT` | `create_email_draft` | outcome 2 |
| `MCP_NOTE_TARGET` | — | notes doc id/URL; used as the canonical note destination when the tool returns no id |
| `MCP_EMAIL_TO` | — | draft `To:` (blank = leave to the reviewer) |
| `ALLOW_AUTO_APPROVE` | `0` | must be `1` to enable non-interactive approve |

Secrets (LLM key, MCP credentials) come from env vars, never committed.

---

## 8. Error Handling & Degradation

| Condition | Behavior |
|---|---|
| Missing/short CSV, bad header | Abort with explicit error; no output |
| LLM unavailable | Fall back to deterministic clustering/generation (`offline`) |
| LLM returns invalid JSON/schema | Retry (bounded); then fail the stage |
| Validation failure | Stop before approval; write draft + `failures` for review |
| No fee confusion detected | Skip Fee Explainer; pulse notes "none detected" (AC4) |
| MCP publish error | Retry with backoff; surface error; never bypass approval |
| Approval rejected | Return to `draft`; nothing published |

---

## 9. Observability & Artifacts

Each run writes to `output/<run-id>/` (run id = UTC timestamp + short hash):

- `reviews.normalized.jsonl` — Stage A output + quality report
- `themes.json` — `ThemeSet`
- `insights.json` — `Insights` (top themes, quotes, fee signal, weekly trend)
- `pulse.md`, `fee_explainer.md` — human-readable drafts
- `validation.json` — `ValidationReport`
- `approval.json` — approval state + reviewer + timestamps
- `publish_log.json` — `PublishRecord[]` (R17)
- `run_meta.json` — seed, model, backend, config, durations

These artifacts make every published message reproducible and auditable.

---

## 10. Testing Strategy

- **Unit**: `ingest` (schema/dedupe/quality), `validators` (each AC rule, incl.
  word-boundary fee matching), `insights` (fee detection on fixture data).
- **Golden/fixture**: run the full pipeline in `--offline` mode against
  `groww-reviews.csv` and assert against `docs/dataset_stats.json`
  (150 fee rows; 5 issue themes; top-3 stable). This is the reproducibility test
  for AC8 and needs no network.
- **Contract**: `tests/` asserts `len(themes) <= 5`, 3 verbatim quotes found in
  source, 6 bullets / 2 links / `Last checked`, pulse ≤ 250 words.
- **Approval**: assert publish is impossible unless state == `approved`;
  `rejected` produces zero `PublishRecord`s.
- Existing `scripts/verify_dataset.py` / `verify_themes.py` remain as data-level
  checks that fixtures stay consistent.

---

## 11. Requirement → Component Traceability

| Req | Component / Stage |
|---|---|
| R1 parse CSV | A `ingest.py` |
| R2 ≤5 themes | B `cluster.py` + E |
| R3 top-3 ranking | B/C `cluster.py`, `insights.py` |
| R4 3 verbatim quotes | C `insights.py` + E (verbatim check) |
| R5 recurring fee confusion | C `insights.py` |
| R6–R9 pulse sections | D1 `pulse.py` + E |
| R10–R14 explainer (6 bullets/2 links/last checked/derived) | D2 `fee_explainer.py` + E |
| R15 MCP share | G `publish_mcp.py` |
| R16 approval gating | F `approval.py` |
| R17 publish record | G `publish_log.json` |
| AC1–AC8 | E `validators.py` (+ §10 tests) |

---

## 12. Non-Functional Notes

- **Reproducibility**: seeded data generator; run_meta records model/seed/config;
  `--offline` mode is fully deterministic.
- **Auditability**: every claim traces to a `Review URL`; artifacts retained per run.
- **Safety**: write isolation (Stage G only), hard approval gate, link allowlist,
  no auto-approve by default.
- **Portability**: CSV in/out, env-var config, provider-agnostic LLM layer —
  runs locally or in CI without MCP endpoints (publish mocked in tests).

---

## 13. Running the Scaffold

The pipeline is implemented under `src/pulse/` (see §3). It runs fully offline
with no keys by default; `MockMCPClient` stands in for real MCP servers.

```bash
# from the repo root
python run_pipeline.py --offline --decision approve     # full run, publishes
python run_pipeline.py --offline --decision pending     # draft only, no publish
python -m unittest discover -s tests                    # 27 tests, offline

# dataset maintenance
python scripts/generate_mock_reviews.py                 # regenerate groww-reviews.csv
python scripts/verify_dataset.py                        # coherence + fee-signal checks
python scripts/diag_classify.py                         # per-theme classification counts

# live n8n (preflight first)
python scripts/check_n8n_endpoint.py                     # handshake + tools/list
python run_pipeline.py --decision approve --mcp-transport http \
  --mcp-endpoint $PULSE_MCP_ENDPOINT --mcp-token $PULSE_MCP_TOKEN
```

| Flag | Meaning |
|---|---|
| `--offline` | force deterministic mode (no LLM) |
| `--decision approve\|reject\|pending` | drive the approval gate non-interactively |
| `--auto-approve` | approve automatically (also needs `ALLOW_AUTO_APPROVE=1`) |
| `--input` / `--output-dir` | override paths |
| `--mcp-transport mock\|http` | mock (default) or live n8n MCP server |
| `--mcp-endpoint` / `--mcp-token` | n8n MCP URL + Bearer token |

Artifacts land in `output/<run-id>/` (§9). Current status:

| Area | Status |
|---|---|
| Stages A–E (ingest → validation) | implemented, tested |
| Stage F (approval gate) | implemented (interactive / flag / auto) |
| Stage G (MCP publish) | implemented: two-outcome n8n client + mock, bounded retry/backoff; contract in `docs/mcp-n8n.md` |
| Stage G preflight | implemented: `tools/list` + `scripts/check_n8n_endpoint.py` (handshake-only, no publish) |
| LLM backends (`gateway`/`direct`) | interface stubbed in `llm.py`; deterministic fallback is the default |

Live publish example:

```bash
python scripts/check_n8n_endpoint.py         # preflight: handshake + tools/list

python run_pipeline.py --decision approve \
  --mcp-transport http --mcp-endpoint "$PULSE_MCP_ENDPOINT" --mcp-token "$PULSE_MCP_TOKEN"
```

