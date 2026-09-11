#!/usr/bin/env python3
"""Smoke test for the natural-language-to-SQL chat endpoint (POST /chat).

Sends a small set of natural-language questions to the deployed chat API
and reports the generated SQL, answer, row count, and latency for each.

Git Bash / Python port of the former tests/chat_api_smoke.ps1. The chat
endpoint is only reachable over HTTP (API Gateway -> Lambda), so it is
called with a real HTTP request rather than boto3 -- boto3 has no API for
invoking a REST endpoint directly; it is used here only where it actually
applies (none of this script's calls are boto3-shaped, since the only AWS
lookup needed is the Terraform-managed API URL, read via the terraform
binary same as the rest of this test suite).

Usage:
    python tests/aws/chat_api_smoke.py [--questions "..." "..."]
        [--timeout SECONDS] [--max-average-latency-seconds N]
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "logs"
TERRAFORM_DIR = "infra/envs/dev"

DEFAULT_QUESTIONS = [
    "Cuantas facturas hay disponibles?",
    "Cual es el monto total de facturas por proveedor?",
    "Cuantas facturas hay por moneda?",
]


def terraform_output(name: str) -> str:
    result = subprocess.run(
        ["terraform", f"-chdir={TERRAFORM_DIR}", "output", "-raw", name],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"terraform output {name} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def post_chat_question(base_url: str, question: str, timeout: int) -> dict:
    payload = {"question": question}
    req = urllib.request.Request(
        f"{base_url}/chat",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    wall_clock_ms = int((time.perf_counter() - start) * 1000)

    for required_field in ("answer", "generated_sql", "query_id"):
        if not body.get(required_field):
            raise ValueError(f"Chat response is missing {required_field}.")

    return {
        "question": question,
        "answer": body["answer"],
        "generated_sql": body["generated_sql"],
        "query_id": body["query_id"],
        "execution_time_ms": body.get("execution_time_ms"),
        "athena_scan_mb": body.get("athena_scan_mb"),
        "row_count": len(body.get("rows") or []),
        "wall_clock_ms": wall_clock_ms,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", nargs="*", default=None, help="Natural-language questions to send")
    parser.add_argument("--timeout", type=int, default=90, help="HTTP timeout per request in seconds")
    parser.add_argument(
        "--max-average-latency-seconds",
        type=int,
        default=10,
        help="Warn (not fail) if average wall-clock latency exceeds this",
    )
    args = parser.parse_args()

    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"chat_api_smoke_{timestamp}.log"
    lines: list[str] = []

    def log(msg: str = "") -> None:
        print(msg)
        lines.append(str(msg))

    def flush() -> None:
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    questions = args.questions if args.questions else DEFAULT_QUESTIONS

    try:
        base_url = terraform_output("web_api_base_url").rstrip("/")
    except RuntimeError as exc:
        log(f"ERROR: {exc}")
        flush()
        return 1

    log(f"Web API: {base_url}")

    results = []
    for question in questions:
        log(f"Asking: {question}")
        try:
            results.append(post_chat_question(base_url, question, args.timeout))
        except urllib.error.HTTPError as exc:
            log(f"HTTPError: {exc.code} {exc.reason}")
            log(exc.read().decode(errors="replace"))
            flush()
            return 1
        except ValueError as exc:
            log(f"ERROR: {exc}")
            flush()
            return 1

    average_latency_ms = round(sum(r["wall_clock_ms"] for r in results) / len(results))
    max_average_latency_ms = args.max_average_latency_seconds * 1000

    summary = {
        "base_url": base_url,
        "question_count": len(results),
        "average_wall_clock_ms": average_latency_ms,
        "max_average_latency_seconds": args.max_average_latency_seconds,
        "results": results,
    }

    log("")
    log("Chat API smoke summary:")
    log(json.dumps(summary, indent=2, ensure_ascii=False))

    if average_latency_ms > max_average_latency_ms:
        log("")
        log(
            f"WARNING: Average chat latency exceeded {args.max_average_latency_seconds} seconds. "
            "This is a readiness risk, not necessarily a functional failure."
        )

    log("")
    log(f"=== Done. Full log written to: {log_file} ===")
    flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
