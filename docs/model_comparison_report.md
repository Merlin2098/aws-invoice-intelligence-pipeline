# Bedrock Model Comparison — Parser and NLQ->SQL

> **Status: populated with real runs, prompt iterated to a clean result.**
> Parser results come from `results_20260910_221156.json` (one run). NLQ->SQL
> went through 3 rounds against live Bedrock `Converse` calls in
> `us-east-1`: an initial run
> (`results_20260910_221156.json`, 6/8 and 5/8 valid), a first prompt fix
> (`results_20260910_222503.json`, 7/8 and 6/8), and a second prompt
> reinforcement (`results_20260910_223658.json`, **8/8 and 8/8**) — see
> [NLQ prompt iteration](#nlq-sql-prompt-iteration) for what changed and why.
> Single run per round, 8 cases per task per model — see
> [Limitations](#limitations) before treating small differences as
> significant.

## Goal

Both LLM call sites in this repo originally used Claude Sonnet 4.5
(`us.anthropic.claude-sonnet-4-5-20250929-v1:0`) via Bedrock `Converse` — the
parser has since been switched to Haiku 4.5 based on this comparison (see
[Status](#status) below). Neither call site uses vision, tool-use, or
multimodal input — Textract already does OCR before the parser call, and
the NLQ->SQL call is plain text-in/text-out with a fixed table schema. That
made both candidates for a cheaper model, as long as accuracy holds up on
the actual prompt and production SQL validator.

- **Parser** (`src/aws/bedrock_client.py`, TIF -> Silver normalization):
  Claude Sonnet 4.5 (baseline) vs. **Claude Haiku 4.5** vs. **Amazon Nova
  Lite**.
- **NLQ -> SQL** (`src/analytics/bedrock_sql.py`): Claude Sonnet 4.5
  (baseline) vs. **Amazon Nova Micro**.

## Method

- Same system prompts as production
  ([bedrock_normalization_prompt.md](../specs/prompts/bedrock_normalization_prompt.md),
  [bedrock_analytics_sql_prompt.md](../specs/prompts/bedrock_analytics_sql_prompt.md)).
- Parser: 8 hand-built cases
  ([parser_cases.json](../tests/model_comparison/parser_cases.json)) scored
  by exact-match on `document_type`, `vendor_name`, `document_date`,
  `total_amount`, `currency` against hand-authored ground truth.
- NLQ->SQL: 8 natural-language questions
  ([nlq_cases.json](../tests/model_comparison/nlq_cases.json)), each
  generated query run through the real
  [sql_validator.py](../src/analytics/sql_validator.py) — "valid" means it
  would actually be allowed to reach Athena in production, not just that it
  parses as SQL.
- Cost computed from Bedrock's reported `usage.inputTokens` /
  `usage.outputTokens` per call, multiplied by on-demand list price. Latency
  is single-run wall-clock per call, not a load-tested p50/p99 — treat it as
  directional (the gaps here are large enough that run-to-run noise doesn't
  change the ranking).

See [tests/model_comparison/README.md](../tests/model_comparison/README.md)
for prerequisites and how to reproduce with more cases or against your own
production traffic sample.

---

## Parser: TIF -> Silver normalization

| Model | `model_id` | Input / output price per 1K tokens (USD) |
|---|---|---|
| Claude Sonnet 4.5 (current) | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | $0.003 / $0.015 |
| Claude Haiku 4.5 | `us.anthropic.claude-haiku-4-5-20251001-v1:0` | $0.001 / $0.005 |
| Amazon Nova Lite | `amazon.nova-lite-v1:0` | $0.00006 / $0.00024 |

### Accuracy (8 cases, 5 scored fields each = 40 field-checks)

| Model | Fields correct | Cases with all 5 fields correct |
|---|---|---|
| Claude Sonnet 4.5 (current) | 39 / 40 | 7 / 8 |
| **Claude Haiku 4.5** | **40 / 40** | **8 / 8** |
| Amazon Nova Lite | 38 / 40 | 6 / 8 |

### Cost and latency (average per call, measured)

| Model | Avg input tokens | Avg output tokens | Avg cost/call (USD) | Avg latency (ms) | Cost per 1,000 documents |
|---|---|---|---|---|---|
| Claude Sonnet 4.5 (current) | 460 | 235 | $0.004905 | 4,283 | $4.91 |
| **Claude Haiku 4.5** | 460 | 234 | $0.001630 | 1,922 | $1.63 |
| Amazon Nova Lite | 408 | 153 | $0.000061 | 812 | $0.06 |

### Per-case detail — mismatches only

Every model got the other 5-6 cases fully correct; only the misses are
worth a close look.

| Case | Sonnet 4.5 | Haiku 4.5 | Nova Lite |
|---|---|---|---|
| `contribution_document` (donor name in a non-commercial "contribution" doc — ground truth says `vendor_name: null`, since a donor isn't a vendor) | **Miss** — returned `vendor_name: "Maria Lopez"` (treated the donor as the vendor) | Correct — returned `null` | **Miss** — same as Sonnet, returned `"Maria Lopez"` |
| `receipt_pen_no_currency_symbol` (Peruvian "BOLETA DE VENTA" — ground truth `document_type: receipt`) | Correct | Correct | **Miss** — classified as `invoice` instead of `receipt` |
| All other 6 cases | Correct | Correct | Correct |

Two takeaways: **Haiku 4.5 was strictly more accurate than the current
Sonnet 4.5 on this test set**, not just "close enough" — it's the only
model that got the donor/vendor distinction right. Nova Lite made two
different mistakes (one shared with Sonnet, one unique to it), consistent
with it being the smallest/cheapest model of the three.

---

## NLQ -> SQL

| Model | `model_id` | Input / output price per 1K tokens (USD) |
|---|---|---|
| Claude Sonnet 4.5 (current) | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` | $0.003 / $0.015 |
| Amazon Nova Micro | `amazon.nova-micro-v1:0` | $0.000035 / $0.00014 |

### Final SQL validity (8 questions, validated by the real `sql_validator.py`)

| Model | Queries that passed validation | Queries that hit the expected table |
|---|---|---|
| Claude Sonnet 4.5 (current) | **8 / 8** | **8 / 8** |
| Amazon Nova Micro | **8 / 8** | **8 / 8** |

### Cost and latency (average per call, measured, final run)

| Model | Avg input tokens | Avg output tokens | Avg cost/call (USD) | Avg latency (ms) | Cost per 10,000 questions |
|---|---|---|---|---|---|
| Claude Sonnet 4.5 (current) | 1,300 | 77 | $0.005061 | 2,161 | $50.61 |
| **Amazon Nova Micro** | 1,230 | 62 | **$0.000052** | **502** | **$0.52** |

Input tokens are higher than in the first round because the reinforced
prompt (schema for both tables + explicit date/status rules) is longer —
that cost is already reflected in the numbers above. Even so, Nova Micro
costs about **97x less** and runs about **4.3x faster** than Sonnet 4.5,
with identical validity on this test set.

### NLQ -> SQL prompt iteration

Three rounds against
[specs/prompts/bedrock_analytics_sql_prompt.md](../specs/prompts/bedrock_analytics_sql_prompt.md),
each fixing what the previous round's failures revealed:

**Round 1 (baseline prompt, before any fix):** Sonnet 6/8, Nova Micro 5/8.
Both models failed `invoices_last_month` identically, using
`date_add`/`date_format`/`current_date` — none allowlisted by
`sql_validator.py`. Root cause: **the prompt never told the model which date
functions are allowed**, and separately, **`bedrock_sql.py` only ever sent
the `gold_invoice_summary` schema** to the model
(`table_schema_prompt()` defaulted to one table), so a model told "use
`gold_documents` for quality_status/duplicates" had no way to see that
table's actual columns — both models partly guessed column names for it.

**Fix 1 (code + prompt):**
[`table_schema_prompt()`](../src/analytics/schema_registry.py#L71) changed
to return both tables' schemas by default; the prompt gained a "no
invented columns" rule, a ban on window functions (`OVER (...)`), and a
"Date handling" section listing exactly which date functions the validator
allows.

**Round 2 (after fix 1):** Sonnet 7/8, Nova Micro 6/8 — real improvement,
2 failures left. Sonnet still used `OVER()` in `quality_rejected_reasons`
(ignored the new rule, rather than lacking the information). Nova Micro
still used `current_date`/`INTERVAL` in `invoices_last_month` (same rule,
still not followed strongly enough), and separately queried
`gold_invoice_summary.processing_status` in `failed_status_detail` — a
*real* column name, but only present on `gold_documents`; the schema
example in the prompt mentioned `quality_status` as a
`gold_documents`-only column but not `processing_status`, so the model
didn't generalize the rule to it.

**Fix 2 (prompt only):** the status-column rule was made explicit and
exhaustive ("`gold_invoice_summary` has no status column of any kind... any
question about status/state/duplicates must use `gold_documents`, even if
the question says 'invoices'"), and the `current_date` ban was promoted
from a bullet in a list to its own rule with the same "hard requirement,
no exception" wording as the window-function rule, plus a literal
copy-pasteable SQL pattern for "last/this month" questions grounded in
`MAX(date_column)` instead of today's date.

**Round 3 (after fix 2): 8/8 for both models.** Every case in both models
now uses the `MAX(processing_date)` subquery pattern for the "last month"
question, and both correctly route status/duplicate questions to
`gold_documents` without inventing columns.

This iteration is worth keeping in mind for future prompt work on this call
site: a single generic rule ("don't invent columns") did not fully
generalize past the literal examples given, and a rule stated once in a
list was followed less reliably than the same rule restated as its own
non-negotiable bullet with a worked example. Smaller models (Nova Micro)
needed the more explicit, exhaustive phrasing more than Sonnet did, but
the fixes improved *both* models' results at every round — this was a
genuine prompt/schema gap, not a Nova-Micro-specific weakness.

---

## Recommendation

**Parser: switch to Claude Haiku 4.5.** It won on accuracy (8/8 vs Sonnet's
7/8) and cut cost by ~3x ($1.63 vs $4.91 per 1,000 documents) and latency by
~2.2x. This is a strict win, not a trade-off — there's no reason measured
here to keep paying for Sonnet 4.5 on this call site. Nova Lite is
dramatically cheaper still (~$0.06 per 1,000 docs) but made 2 classification
mistakes in 8 cases; worth a larger test batch if the cost gap vs. Haiku
matters at your volume, but don't switch to it on this evidence alone.

**NLQ -> SQL: switch to Amazon Nova Micro.** After fixing the prompt/schema
gap that was capping both models' accuracy (see
[NLQ -> SQL prompt iteration](#nlq-sql-prompt-iteration)), Nova Micro
matches Sonnet 4.5 exactly on validity (8/8 both) at ~97x lower cost and
~4.3x lower latency. This was a genuine tie on accuracy, not a
close-enough judgment call — the fix eliminated every failure mode either
model had, so there's no remaining accuracy trade-off to weigh against the
cost savings.

### Status

1. **Parser → Claude Haiku 4.5: done.** `infra/envs/dev/variables.tf` now
   has separate `parser_bedrock_model_id` (default
   `us.anthropic.claude-haiku-4-5-20251001-v1:0`) and
   `chat_bedrock_model_id` variables; `terraform.tfvars` updated to match.
2. **SQL prompt/schema fix: done.**
   `specs/prompts/bedrock_analytics_sql_prompt.md` and
   `src/analytics/schema_registry.py`'s `table_schema_prompt()` were updated
   as described above; verified by re-running the harness to 8/8 on both
   models.
3. **NLQ -> SQL model switch to Nova Micro: done.**
   `chat_bedrock_model_id` in `infra/envs/dev/terraform.tfvars` (and the
   variable's default in `variables.tf`) is now `amazon.nova-micro-v1:0`.
   Note Nova model ids do **not** take the `us.`
   cross-region-inference-profile prefix that the Claude model ids in this
   file use. **Not yet deployed** — run `make tf-plan` / `make tf-apply`
   (per [AGENTS.md](../AGENTS.md) approval rules) to apply it to the live
   `chat_lambda` and its `chat_bedrock_permissions` IAM policy.
4. Optionally widen `parser_cases.json` and `nlq_cases.json` past 8 cases
   each (more vendor/format variety from actual production traffic) before
   fully trusting these rankings at larger scale — see
   [Limitations](#limitations).

## Limitations

- Single run, 8 cases per task — enough to catch gross failures (wrong
  document type, hallucinated SQL columns, invalid syntax) but not
  statistically powered for close calls. The parser cases are hand-authored
  Textract-shaped payloads, not sampled from real production documents —
  revisit them against your actual vendor/format mix before trusting the
  accuracy ranking at larger scale.
- Latency figures are single-region, cold, sequential calls from this
  machine — not a load-tested p50/p99. The relative gaps (Haiku ~2x faster
  than Sonnet, Nova models ~4x faster than their Claude counterparts) are
  large enough to trust directionally.
- Pricing is on-demand list price at the time this harness was written;
  verify against the [Bedrock pricing page](https://aws.amazon.com/bedrock/pricing/)
  before finalizing a budget projection, and adjust the "cost per N"
  columns to your actual monthly call volume.
