# PRD: Weekly Product Pulse — AI Review-to-Insight Workflow

**Status:** Draft
**Date:** 2026-10-16
**Source problem statement:** `docs/problemstatement.txt`
**Source data:** `groww-reviews.csv` (Google Play public reviews, mock dataset — 700 rows)
**Data generator:** `scripts/generate_mock_reviews.py` (original file preserved at `docs/groww-reviews.original.csv`)

---

## 1. Problem Statement

Product and Support teams at Groww need a repeatable way to convert raw customer
reviews into decisions and communication. Today, review analysis is manual:
nobody has time to read hundreds of reviews each week, so recurring pain points —
especially confusion around fees and charges — surface late or not at all.

Support ends up answering the same misunderstood fee question ad hoc, while
Product lacks a structured weekly signal of what users are actually complaining
about.

**Goal:** an AI-powered workflow that transforms raw customer feedback into

1. a structured **internal weekly product pulse**, and
2. a **ready-to-use customer communication** (a fee explainer), with results
   logged/shared across internal tools via **MCP with approval gating**.

---

## 2. Users & Jobs-to-be-Done

| Persona | Job to be done |
|---|---|
| Product manager | "Give me a short weekly note on top themes, sentiment, and what's trending wrong." |
| Support lead | "Turn recurring confusion into a standardized, accurate explanation I can hand to agents." |
| Ops / tool owner | "Log and share these outputs into our internal tools, but only after a human approves." |

---

## 3. Scope — The Three Steps

### Step 1 — Review Intelligence Layer

**Input:** 1 public reviews CSV covering the last 8–12 weeks.

**Requirements:**

- R1. Ingest and parse the CSV (`Date`, `Rating`, `Title`, `Review`, `App version`, `Helpful votes`, `Developer reply`, `Reply date`, `Review URL`).
- R2. Cluster reviews into a **maximum of 5 themes** (LLM-based clustering; each review assigned to exactly one theme or "other"). Purely positive/praise reviews are tracked as background sentiment and excluded from the issue-theme count, so the issue themes stay at ≤ 5.
- R3. Rank themes and identify the **top 3 themes** (by volume + severity weighting from rating).
- R4. Extract **3 real user quotes** verbatim, each tied to a top theme and traceable to a `Review URL`.
- R5. Highlight **1 recurring confusion/pain point related to a fee/charge** — this must be derived from the reviews, not hardcoded. In this dataset it is the **"₹20 brokerage vs. total charges"** confusion (see fee signal below).

**Data profile (regenerated mock dataset, verified):** `groww-reviews.csv` holds
**700 reviews spanning 2026-07-24 → 2026-10-16 (~12 weeks)**, satisfying the
8–12 week coverage requirement. It is synthetic mock data, regenerated so it
cleanly supports Steps 1–3 (see `scripts/generate_mock_reviews.py`).

| Signal | Value |
|---|---|
| Rows | 700 |
| Date range | 2026-07-24 → 2026-10-16 (~12 weeks) |
| Rating mix | 5★ 53 · 4★ 139 · 3★ 301 · 2★ 143 · 1★ 64 (avg 2.96) |
| Low ratings (1–2★) | 207 (30%) |
| Distinct review texts | 697 of 700 |
| Rows with a developer reply | 501 of 700 |
| `Title` populated | 678 of 700 |
| **Fee/charge mentions** | **150** (rising over the period) |

Exact counts and averages are also emitted to `docs/dataset_stats.json` by the
generator.

The dataset is built around **five clean issue themes** so clustering yields
≤ 5 themes and a stable top 3:

| Theme | Volume | Avg rating |
|---|---|---|
| App performance & stability (lag, freezes, crashes, slow loads) | 160 | 2.62 |
| **Trading fees & charges (confusion)** | **150** | **2.73** |
| KYC / account activation delays | 120 | 2.56 |
| Withdrawals, payouts & settlement delays | 115 | 2.63 |
| UI/UX, navigation & feature requests | 115 | 3.89 |
| General praise (background, not a theme) | 40 | 4.67 |

**Fee signal (the crux of Step 3):** **150 reviews** mention fees/charges, making
it a recurring, *rising* complaint (W30 ≈5/week → W40 ≈19/week; see
`weekly_fee_mentions` in the stats file). The specific recurring confusion is:
**users are told brokerage is a flat ₹20 per order, but they are also deducted
statutory charges — stamp duty, exchange transaction charges, SEBI turnover fees,
GST, and DP/delivery charges — so their real cost per trade is higher than
expected and the final credited amount is a surprise.** Representative verbatim
quotes present in the CSV:

- `2026-10-02 | 3★ | Brokerage is only Rs 20, but the other charges quietly take away a big part of my profit, and nothing in the app explains how much I will actually pay.`
- `2026-08-31 | 3★ | I was shocked to see how much was deducted from my small trade after all the extra charges were added on top of the brokerage.`
- `2026-10-06 | 3★ | Please show me a clear breakup of every charge before I place an order, because right now the final cost is always a surprise.`

This is the fee the explainer must address: the **"₹20 brokerage vs. total
charges"** confusion.

**Data-quality invariants** (by construction; the pipeline should still validate):
(1) review text and star rating are coherent (no positive text on 1★ rows);
(2) review texts are ~unique (no templated duplicates to inflate theme counts);
(3) issue themes dominate volume, so top-3 ranking is stable.

### Step 2 — Weekly Product Pulse (Internal Output)

**Output:** a structured internal note, **≤ 250 words**, containing:

- R6. Summary of the top themes.
- R7. Supporting user quotes (the 3 from Step 1).
- R8. Key observation — what's going wrong / what's trending.
- R9. **3 action ideas** for the product team.

**Tone:** internal, concise, factual, no marketing language.

### Step 3 — Fee Explainer (Derived from Insights)

**Output:** a standardized explanation of the specific fee identified in Step 1:

- R10. **6 bullet points** explaining the fee clearly — i.e. what brokerage is (₹20/order) and the extra statutory charges (stamp duty, exchange transaction charges, SEBI turnover fees, GST, DP/delivery charges).
- R11. **Neutral, facts-only tone** (no apologies, no upselling).
- R12. **2 official source links** — from the official Groww domain, e.g. the Groww pricing/charges page (`https://groww.in/pricing`) and the Groww help centre (`https://groww.in/help`).
- R13. Footer line: `Last checked: <date>`.
- R14. Must **directly connect to the confusion found in Step 1** — the "₹20 brokerage vs. total charges" confusion — not a generic or random fee scenario. The pulse and explainer must be traceable to the same underlying reviews.

### Step 4 — MCP Distribution with Approval Gating

- R15. Use **MCP** to log and share the Step 2 + Step 3 outputs across internal tools (e.g., task tracker, docs, or team channel).
- R16. **Approval gating is mandatory:** no MCP write happens without explicit
  human approval of the generated content. Draft → review → approved → publish.
- R17. On approval, record where the artifact was published (tool + link/ID).

---

## 4. Pipeline Architecture (Suggested)

```
groww-reviews.csv
      │
      ▼
[1. Ingest & clean] ── dedupe, parse dates/ratings, drop empty reviews
      │
      ▼
[2. Theme clustering] ── LLM clustering, cap = 5 themes
      │
      ▼
[3. Insights] ── top 3 themes + 3 quotes + fee-confusion signal
      │
      ├──► [4a. Product Pulse generator] → ≤250-word note
      ├──► [4b. Fee Explainer generator] → 6 bullets + 2 links + last-checked
      │
      ▼
[5. Human review / approval gate]
      │ (approve | reject + edit)
      ▼
[6. MCP tools] → log + share to internal tools
```

LLM calls may be made directly (provider API) or through an **AI gateway**;
external writes must go through MCP tools only, behind the approval gate.

---

## 5. Acceptance Criteria

1. Running the pipeline on `groww-reviews.csv` produces exactly 5 or fewer themes.
2. Top 3 themes are ranked with supporting counts and severity.
3. All 3 quotes appear verbatim in the source CSV with working `Review URL`s.
4. A fee/charge confusion is identified **from review content**; if none exists in
   the data, the pipeline reports "no fee confusion detected" rather than fabricating one.
5. Product Pulse is ≤ 250 words and contains all four required sections (themes,
   quotes, key observation, 3 action ideas).
6. Fee Explainer has exactly 6 bullets, 2 official links, and `Last checked: <date>`.
7. No MCP write occurs without an explicit approval step; rejection blocks publishing.
8. Outputs are reproducible: re-running on the same CSV yields the same structure.

---

## 6. Out of Scope

- Real-time review ingestion / scheduled scraping of the Play Store.
- Sentiment dashboards or historical trend storage.
- Auto-publishing without human approval.
- Multi-product support (this milestone is Groww only).

---

## 7. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Mock data may not reflect real-world prevalence | Dataset is synthetic by design; documented and generated by `scripts/generate_mock_reviews.py` with a fixed seed for reproducibility |
| Rating/text drift reintroduced if data is edited by hand | Regenerate via the script, or run the coherence check (no positive text on 1–2★ rows) |
| LLM hallucinated quotes or fees | Verbatim quote validation against CSV; fee must cite source review(s) |
| Fabricated official links | Link-allowlist (official Groww domain) validated before output |
| Unintended publishes | Hard approval gate in front of every MCP write tool |
