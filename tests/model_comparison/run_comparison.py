#!/usr/bin/env python3
"""Bedrock model comparison harness.

Compares candidate Bedrock models against the two production LLM call
sites in this repo, using the *real* prompts and validation logic:

  - Parser (TIF -> Silver normalization): src/aws/bedrock_client.py's prompt
    (specs/prompts/bedrock_normalization_prompt.md), scored against hand-built
    ground truth in tests/model_comparison/parser_cases.json.

  - NLQ -> SQL: src/analytics/bedrock_sql.py's prompt
    (specs/prompts/bedrock_analytics_sql_prompt.md), scored by running the
    generated SQL through the actual src/analytics/sql_validator.py.

Requires valid AWS credentials with bedrock:InvokeModel /
bedrock:InvokeModelWithResponseStream (via Converse) for each model_id
tested, in a region where that model is enabled.

Usage:
    python tests/model_comparison/run_comparison.py --region us-east-1
    python tests/model_comparison/run_comparison.py --task parser
    python tests/model_comparison/run_comparison.py --task nlq

Output:
    Prints a summary table to stdout and writes
    tests/model_comparison/results_<timestamp>.json with raw per-case data,
    which docs/model_comparison_report.md is written from.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.analytics.schema_registry import table_schema_prompt  # noqa: E402
from src.analytics.sql_validator import SqlValidationError, validate_sql  # noqa: E402

HARNESS_DIR = Path(__file__).parent
PARSER_PROMPT_PATH = REPO_ROOT / "specs" / "prompts" / "bedrock_normalization_prompt.md"
SQL_PROMPT_PATH = REPO_ROOT / "specs" / "prompts" / "bedrock_analytics_sql_prompt.md"

# Candidate models per task, as requested:
#   - Parser: current (Claude Sonnet 4.5) vs Claude Haiku 4.5 vs Nova Lite
#   - NLQ->SQL: current (Claude Sonnet 4.5) vs Nova Micro
PARSER_MODELS = {
    "claude-sonnet-4.5 (current)": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "claude-haiku-4.5": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
    "nova-lite": "amazon.nova-lite-v1:0",
}

NLQ_MODELS = {
    "claude-sonnet-4.5 (current)": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "nova-micro": "amazon.nova-micro-v1:0",
}

# On-demand Bedrock list prices, USD per 1K tokens (us-east-1, as of this
# harness's writing, cross-checked across AWS docs + multiple third-party
# trackers -- there was a small discrepancy between sources for Haiku 4.5
# and Nova Micro, so double check https://aws.amazon.com/bedrock/pricing/
# before trusting these for a budget decision; prices also change over time).
# Note: Claude models require the "us." cross-region inference-profile
# prefix for on-demand invocation in this account; Nova models use the
# plain foundation-model id (no region prefix).
PRICING_PER_1K_TOKENS = {
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0": {"input": 0.003, "output": 0.015},
    "us.anthropic.claude-haiku-4-5-20251001-v1:0": {"input": 0.001, "output": 0.005},
    "amazon.nova-lite-v1:0": {"input": 0.00006, "output": 0.00024},
    "amazon.nova-micro-v1:0": {"input": 0.000035, "output": 0.00014},
}


def _bedrock_client(region: str):
    import boto3

    return boto3.client("bedrock-runtime", region_name=region)


def _estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    prices = PRICING_PER_1K_TOKENS.get(model_id)
    if prices is None:
        return None
    return (input_tokens / 1000) * prices["input"] + (output_tokens / 1000) * prices["output"]


def _extract_json(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        if len(parts) >= 2:
            cleaned = parts[1]
            if cleaned.lower().startswith("json"):
                cleaned = cleaned[4:]
    return json.loads(cleaned.strip())


def _extract_sql(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        if len(parts) >= 3:
            cleaned = parts[1].strip()
            if cleaned.lower().startswith("sql"):
                cleaned = cleaned[3:].strip()
    return cleaned.strip()


def _score_parser_result(predicted: dict[str, Any], ground_truth: dict[str, Any]) -> dict[str, Any]:
    fields = ["document_type", "vendor_name", "document_date", "total_amount", "currency"]
    matches = {}
    for field in fields:
        expected = ground_truth.get(field)
        actual = predicted.get(field)
        if isinstance(expected, float) and isinstance(actual, (int, float)):
            matches[field] = abs(expected - float(actual)) < 0.01
        elif isinstance(expected, str) and isinstance(actual, str):
            matches[field] = expected.strip().lower() == actual.strip().lower()
        else:
            matches[field] = expected == actual
    correct = sum(1 for v in matches.values() if v)
    return {"field_matches": matches, "fields_correct": correct, "fields_total": len(fields)}


def run_parser_case(client, model_id: str, system_prompt: str, case: dict[str, Any]) -> dict[str, Any]:
    user_message = json.dumps(case["payload"], ensure_ascii=False)
    start = time.perf_counter()
    try:
        response = client.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 1024},
        )
    except Exception as exc:  # noqa: BLE001 - surfaced in report, not swallowed
        return {"case_id": case["id"], "error": str(exc)}
    latency_ms = (time.perf_counter() - start) * 1000

    text = response["output"]["message"]["content"][0]["text"]
    usage = response.get("usage", {})
    input_tokens = usage.get("inputTokens", 0)
    output_tokens = usage.get("outputTokens", 0)

    try:
        parsed = _extract_json(text)
    except json.JSONDecodeError as exc:
        return {
            "case_id": case["id"],
            "error": f"JSON parse failure: {exc}",
            "raw_output": text,
            "latency_ms": latency_ms,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }

    score = _score_parser_result(parsed, case["ground_truth"])
    return {
        "case_id": case["id"],
        "description": case["description"],
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _estimate_cost_usd(model_id, input_tokens, output_tokens),
        "predicted": parsed,
        "ground_truth": case["ground_truth"],
        **score,
    }


def run_nlq_case(client, model_id: str, system_prompt: str, case: dict[str, Any]) -> dict[str, Any]:
    user_message = (
        f"{table_schema_prompt()}\n\n"
        f"User question:\n{case['question'].strip()}\n\n"
        "Return only Athena SQL."
    )
    start = time.perf_counter()
    try:
        response = client.converse(
            modelId=model_id,
            system=[{"text": system_prompt}],
            messages=[{"role": "user", "content": [{"text": user_message}]}],
            inferenceConfig={"maxTokens": 512, "temperature": 0},
        )
    except Exception as exc:  # noqa: BLE001
        return {"case_id": case["id"], "error": str(exc)}
    latency_ms = (time.perf_counter() - start) * 1000

    text = response["output"]["message"]["content"][0]["text"]
    usage = response.get("usage", {})
    input_tokens = usage.get("inputTokens", 0)
    output_tokens = usage.get("outputTokens", 0)

    sql = _extract_sql(text)
    validation_error = None
    validated_sql = None
    tables_used: tuple[str, ...] = ()
    try:
        result = validate_sql(sql)
        validated_sql = result.sql
        tables_used = result.tables
    except SqlValidationError as exc:
        validation_error = str(exc)

    table_matches_expectation = case["expects_table"] in tables_used

    return {
        "case_id": case["id"],
        "question": case["question"],
        "latency_ms": latency_ms,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cost_usd": _estimate_cost_usd(model_id, input_tokens, output_tokens),
        "generated_sql": sql,
        "validated_sql": validated_sql,
        "validation_passed": validation_error is None,
        "validation_error": validation_error,
        "tables_used": list(tables_used),
        "expected_table": case["expects_table"],
        "table_matches_expectation": table_matches_expectation,
    }


def run_task(task: str, region: str) -> dict[str, Any]:
    client = _bedrock_client(region)
    results: dict[str, Any] = {}

    if task == "parser":
        cases = json.loads((HARNESS_DIR / "parser_cases.json").read_text(encoding="utf-8"))
        system_prompt = PARSER_PROMPT_PATH.read_text(encoding="utf-8")
        for model_name, model_id in PARSER_MODELS.items():
            print(f"\n=== Parser | {model_name} ({model_id}) ===")
            case_results = []
            for case in cases:
                print(f"  case: {case['id']} ...", end=" ", flush=True)
                res = run_parser_case(client, model_id, system_prompt, case)
                case_results.append(res)
                if "error" in res:
                    print(f"ERROR: {res['error']}")
                else:
                    print(f"{res['fields_correct']}/{res['fields_total']} fields correct, {res['latency_ms']:.0f}ms")
            results[model_name] = {"model_id": model_id, "cases": case_results}

    elif task == "nlq":
        cases = json.loads((HARNESS_DIR / "nlq_cases.json").read_text(encoding="utf-8"))
        system_prompt = SQL_PROMPT_PATH.read_text(encoding="utf-8")
        for model_name, model_id in NLQ_MODELS.items():
            print(f"\n=== NLQ->SQL | {model_name} ({model_id}) ===")
            case_results = []
            for case in cases:
                print(f"  case: {case['id']} ...", end=" ", flush=True)
                res = run_nlq_case(client, model_id, system_prompt, case)
                case_results.append(res)
                if "error" in res:
                    print(f"ERROR: {res['error']}")
                else:
                    status = "valid" if res["validation_passed"] else f"INVALID ({res['validation_error']})"
                    print(f"{status}, {res['latency_ms']:.0f}ms")
            results[model_name] = {"model_id": model_id, "cases": case_results}

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1", help="AWS region with Bedrock model access enabled")
    parser.add_argument(
        "--task",
        choices=["parser", "nlq", "both"],
        default="both",
        help="Which comparison to run",
    )
    args = parser.parse_args()

    all_results: dict[str, Any] = {}
    tasks = ["parser", "nlq"] if args.task == "both" else [args.task]
    for task in tasks:
        all_results[task] = run_task(task, args.region)

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = HARNESS_DIR / f"results_{timestamp}.json"
    out_path.write_text(json.dumps(all_results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nRaw results written to: {out_path}")
    print("Next: feed this file to the report generator or paste its content back to build docs/model_comparison_report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
