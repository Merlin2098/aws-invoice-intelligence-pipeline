#!/usr/bin/env python3
"""End-to-end smoke test for the direct AWS pipeline (S3 upload -> Step Functions -> Gold).

Uploads a random sample of documents from data/raw straight to the data lake's
raw/ prefix (bypassing the web upload API), waits for the triggered Step
Functions executions to reach a terminal state, and checks for terminal S3
outputs (silver/valid, silver/rejected, errors/silver_failed).

Git Bash / Python port of the former tests/aws_e2e_smoke.ps1. All AWS calls
use boto3 (S3, Step Functions) instead of the aws CLI; Terraform outputs are
still read via the terraform binary (boto3 has no Terraform API).

Usage:
    python tests/aws/e2e_pipeline_smoke.py [--count N] [--run-id ID]
        [--timeout SECONDS] [--poll-interval SECONDS] [--skip-execution-wait]
"""

from __future__ import annotations

import argparse
import datetime
import json
import random
import subprocess
import sys
import time
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "logs"
RAW_DATA_DIR = REPO_ROOT / "data" / "raw"
TERRAFORM_DIR = "infra/envs/dev"

SUPPORTED_EXTENSIONS = {".pdf", ".tif", ".tiff", ".png", ".jpg", ".jpeg"}
TERMINAL_EXECUTION_STATUSES = {"SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"}


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


def list_s3_keys(s3_client, bucket: str, prefix: str) -> list[str]:
    keys: list[str] = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            keys.append(obj["Key"])
    return keys


def list_executions_for_run(sfn_client, state_machine_arn: str, run_id: str) -> list[dict]:
    executions: list[dict] = []
    paginator = sfn_client.get_paginator("list_executions")
    for page in paginator.paginate(stateMachineArn=state_machine_arn, maxResults=100):
        for execution in page["executions"]:
            if execution["name"].startswith(f"{run_id}-"):
                executions.append(execution)
    return executions


def wait_for_executions(
    sfn_client, state_machine_arn: str, run_id: str, expected_count: int, timeout: int, poll_interval: int, log
) -> list[dict]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        executions = list_executions_for_run(sfn_client, state_machine_arn, run_id)
        terminal = [e for e in executions if e["status"] in TERMINAL_EXECUTION_STATUSES]
        log(f"Executions: {len(executions)}/{expected_count} observed, {len(terminal)}/{expected_count} terminal")
        if len(executions) >= expected_count and len(terminal) >= expected_count:
            return executions
        time.sleep(poll_interval)
    raise TimeoutError(f"Timed out waiting for {expected_count} Step Functions executions for run_id={run_id}")


def wait_for_terminal_s3_outputs(
    s3_client, bucket: str, run_id: str, expected_count: int, timeout: int, poll_interval: int, log
) -> dict[str, list[str]]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        valid = list_s3_keys(s3_client, bucket, f"silver/valid/run_id={run_id}/")
        rejected = list_s3_keys(s3_client, bucket, f"silver/rejected/run_id={run_id}/")
        failed = list_s3_keys(s3_client, bucket, f"errors/silver_failed/run_id={run_id}/")
        terminal_count = len(valid) + len(rejected) + len(failed)
        log(
            f"Terminal S3 outputs: {terminal_count}/{expected_count} "
            f"(valid={len(valid)}, rejected={len(rejected)}, failed={len(failed)})"
        )
        if terminal_count >= expected_count:
            return {"valid": valid, "rejected": rejected, "failed": failed}
        time.sleep(poll_interval)
    raise TimeoutError(f"Timed out waiting for {expected_count} terminal S3 outputs for run_id={run_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=5, help="Number of documents to randomly select from data/raw")
    parser.add_argument("--run-id", default=None, help="Explicit run_id (default: generated timestamp-based id)")
    parser.add_argument("--raw-data-dir", default=str(RAW_DATA_DIR), help="Directory to select fixtures from")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds to wait for terminal state")
    parser.add_argument("--poll-interval", type=int, default=15, help="Seconds between polls")
    parser.add_argument(
        "--skip-execution-wait",
        action="store_true",
        help="Skip waiting on Step Functions executions and only wait for terminal S3 outputs",
    )
    args = parser.parse_args()

    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"e2e_pipeline_smoke_{timestamp}.log"
    lines: list[str] = []

    def log(msg: str = "") -> None:
        print(msg)
        lines.append(str(msg))

    def flush() -> None:
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    run_id = args.run_id or f"e2e-smoke-{datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}"

    log(f"Run ID: {run_id}")
    log("Reading Terraform outputs...")

    try:
        data_lake_bucket = terraform_output("data_lake_bucket_name")
        state_machine_arn = terraform_output("state_machine_arn")
    except RuntimeError as exc:
        log(f"ERROR: {exc}")
        flush()
        return 1

    log(f"Data lake bucket: {data_lake_bucket}")
    log(f"State machine: {state_machine_arn}")

    raw_data_dir = Path(args.raw_data_dir)
    candidates = [p for p in raw_data_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
    if len(candidates) < args.count:
        log(f"ERROR: Only found {len(candidates)} supported documents in {raw_data_dir}; expected {args.count}")
        flush()
        return 1
    documents = random.sample(candidates, args.count)

    log("Selected documents:")
    for doc in documents:
        log(f" - {doc.name}")

    s3_client = boto3.client("s3")
    sfn_client = boto3.client("stepfunctions")

    for doc in documents:
        key = f"raw/run_id={run_id}/{doc.name}"
        log(f"Uploading {doc.name} -> s3://{data_lake_bucket}/{key}")
        s3_client.upload_file(str(doc), data_lake_bucket, key)

    try:
        if not args.skip_execution_wait:
            executions = wait_for_executions(
                sfn_client, state_machine_arn, run_id, args.count, args.timeout, args.poll_interval, log
            )
        else:
            executions = []

        outputs = wait_for_terminal_s3_outputs(
            s3_client, data_lake_bucket, run_id, args.count, args.timeout, args.poll_interval, log
        )
    except TimeoutError as exc:
        log(f"ERROR: {exc}")
        flush()
        return 1

    bronze = list_s3_keys(s3_client, data_lake_bucket, f"bronze/textract-json/run_id={run_id}/")
    gold = list_s3_keys(s3_client, data_lake_bucket, "gold/documents/")
    manifests = list_s3_keys(s3_client, data_lake_bucket, "gold/manifests/")

    status_counts: dict[str, int] = {}
    for execution in executions:
        status_counts[execution["status"]] = status_counts.get(execution["status"], 0) + 1

    summary = {
        "run_id": run_id,
        "uploaded_documents": [d.name for d in documents],
        "data_lake_bucket": data_lake_bucket,
        "state_machine_arn": state_machine_arn,
        "execution_count": len(executions),
        "execution_statuses": [{"status": s, "count": c} for s, c in status_counts.items()],
        "bronze_count": len(bronze),
        "silver_valid_count": len(outputs["valid"]),
        "silver_rejected_count": len(outputs["rejected"]),
        "failed_count": len(outputs["failed"]),
        "recent_gold_object_count": len(gold),
        "recent_manifest_count": len(manifests),
    }

    log("")
    log("E2E smoke summary:")
    log(json.dumps(summary, indent=2))

    if outputs["failed"]:
        log("")
        log(f"WARNING: One or more documents produced failed outputs. Inspect errors/silver_failed/run_id={run_id}/.")

    log("")
    log(f"=== Done. Full log written to: {log_file} ===")
    flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
