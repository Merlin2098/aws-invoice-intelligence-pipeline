# Bedrock Model Comparison Harness

Compares candidate Amazon Bedrock models against the two production LLM call
sites in this repo, using the real prompts and real validation logic (not a
synthetic benchmark):

- **Parser** (TIF -> Silver normalization): drives
  [src/aws/bedrock_client.py](../../src/aws/bedrock_client.py)'s exact system
  prompt against 8 hand-built cases in
  [parser_cases.json](parser_cases.json) (clean invoice, ambiguous
  multi-vendor, illegible amount, wrong-guess traps, date/currency format
  edge cases), each with hand-authored ground truth. Scored by exact-match
  on the 5 normalized fields (`document_type`, `vendor_name`,
  `document_date`, `total_amount`, `currency`).

- **NLQ -> SQL**: drives
  [src/analytics/bedrock_sql.py](../../src/analytics/bedrock_sql.py)'s exact
  system prompt against 8 natural-language questions in
  [nlq_cases.json](nlq_cases.json), covering both Gold tables. Every
  generated query is run through the actual
  [src/analytics/sql_validator.py](../../src/analytics/sql_validator.py) used
  in production — so "valid" here means "would actually be allowed to reach
  Athena," not just "looks like SQL."

Models compared (per current request):

| Task | Baseline (current) | Candidates |
|---|---|---|
| Parser | Claude Sonnet 4.5 | Claude Haiku 4.5, Amazon Nova Lite |
| NLQ -> SQL | Claude Sonnet 4.5 | Amazon Nova Micro |

## Prerequisites

- Valid AWS credentials with `bedrock:InvokeModel` (via `Converse`) for each
  model_id under test, in the target region.
- Model access enabled for Anthropic and Amazon models in the Bedrock
  console for that account/region (Bedrock requires explicit per-model
  access grants before first use).
- `boto3` (already a project dependency).

The credentials currently in `infra/env/.env.credentials` did not
authenticate against AWS STS as of 2026-09-10 — fix that first
(`aws sts get-caller-identity` should succeed) or export working credentials
in your shell before running this.

## Running it

```bash
# both tasks, default region us-east-1
python tests/model_comparison/run_comparison.py

# just one task
python tests/model_comparison/run_comparison.py --task parser
python tests/model_comparison/run_comparison.py --task nlq

# different region (only if your Bedrock model access is granted elsewhere)
python tests/model_comparison/run_comparison.py --region us-east-1
```

Each run prints a live progress line per case/model and writes a full
results file to `tests/model_comparison/results_<timestamp>.json` containing
every case's raw prediction/SQL, field-level match booleans, validation
errors, token counts, per-call cost estimate, and latency.

## Turning results into the report

[docs/model_comparison_report.md](../../docs/model_comparison_report.md) is
the write-up. After running the harness, either:

- paste the contents of the generated `results_<timestamp>.json` back into
  this conversation and ask for the report to be regenerated with real
  numbers, or
- fill in the tables in `docs/model_comparison_report.md` by hand from the
  JSON — the structure is already there, only the numbers are placeholders.

## Model IDs

Claude models require the `us.` cross-region inference-profile prefix for
on-demand invocation (same pattern as this repo's existing
`bedrock_model_id` default for Sonnet 4.5); Nova models use the plain
foundation-model id, no region prefix:

| Model | `model_id` |
|---|---|
| Claude Sonnet 4.5 | `us.anthropic.claude-sonnet-4-5-20250929-v1:0` |
| Claude Haiku 4.5 | `us.anthropic.claude-haiku-4-5-20251001-v1:0` |
| Amazon Nova Lite | `amazon.nova-lite-v1:0` |
| Amazon Nova Micro | `amazon.nova-micro-v1:0` |

## Notes / limitations

- Token counts and latency come straight from the Bedrock `Converse`
  response (`usage.inputTokens`/`usage.outputTokens`) and wall-clock time in
  this script — they reflect a cold, sequential, single-region run, not
  production p50/p99 under load. Run it a few times if latency variance
  matters for your decision.
- Cost estimates use a pricing table hardcoded at the top of
  `run_comparison.py` (`PRICING_PER_1K_TOKENS`). Bedrock pricing changes
  over time and varies by region — verify against the [Bedrock pricing
  page](https://aws.amazon.com/bedrock/pricing/) before using these numbers
  for a budget decision.
- 8 cases per task is enough to catch gross failures (wrong document type,
  invalid SQL, hallucinated columns) but is not statistically powered to
  detect small accuracy differences. Treat a close result as "roughly tied,
  decide on cost/latency" rather than a confident ranking.
- The parser cases are hand-authored Textract-shaped payloads (this repo has
  no bank of real, varied invoice fixtures beyond trivial unit-test
  samples like `data/test_tmp/**/sample.json`) — review them against your
  actual document mix and add cases that reflect vendors/formats you
  actually see in production before trusting the accuracy numbers.
