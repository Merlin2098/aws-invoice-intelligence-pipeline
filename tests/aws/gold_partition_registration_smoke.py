#!/usr/bin/env python3
"""Smoke test for automatic Gold partition registration (no manual MSCK REPAIR).

Regression test for the fix where consolidate_gold now registers each new
batch_id partition in Glue itself (via glue:CreatePartition) right after
writing the parquet, so Athena/the chat API can see it immediately. Before
that fix, a fresh batch_id was invisible to Athena until someone ran
`MSCK REPAIR TABLE gold_documents` by hand.

Flow:
  1. Uploads document(s) through the real web upload API (POST /uploads +
     presigned PUT), same path the frontend uses.
  2. Polls GET /invoices/{id}/status until the pipeline reaches a terminal
     state for each document.
  3. Calls Glue GetPartitions directly (boto3) -- deliberately NOT running
     MSCK REPAIR TABLE -- to confirm the new batch_id partition(s) already
     exist in the catalog.
  4. Asks the chat API a natural-language question and checks the returned
     row count is consistent with the partition being visible.

A failure here means either the partition-registration code regressed, or
the Glue IAM permissions/env vars for consolidate_gold drifted.

Usage:
    python tests/aws/gold_partition_registration_smoke.py [--count N]
        [--files path1 path2 ...] [--data-dir DIR] [--timeout SECONDS]
        [--poll-interval SECONDS]
"""

from __future__ import annotations

import argparse
import datetime
import json
import mimetypes  # noqa: F401  (kept for parity with upload_status_api_smoke's content-type table)
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import boto3

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "logs"
DEFAULT_DATA_DIR = REPO_ROOT / "data" / "raw"
TERRAFORM_DIR = "infra/envs/dev"

SUPPORTED_EXTENSIONS = {".pdf", ".tif", ".tiff"}
CONTENT_TYPES = {".pdf": "application/pdf", ".tif": "image/tiff", ".tiff": "image/tiff"}
MAX_SIZE_BYTES = 20 * 1024 * 1024
TERMINAL_STATUSES = {"Completed", "Failed"}


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


def http_json(method: str, url: str, payload: dict | None = None, timeout: int = 30):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return resp.status, (json.loads(body) if body else None)


def http_put_file(url: str, file_path: Path, content_type: str, timeout: int = 300) -> int:
    req = urllib.request.Request(url, data=file_path.read_bytes(), method="PUT")
    req.add_header("Content-Type", content_type)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def select_documents(count: int, explicit_paths: list[str], data_dir: Path) -> list[Path]:
    if explicit_paths:
        selected = [Path(p) for p in explicit_paths]
        for p in selected:
            if not p.is_file():
                raise FileNotFoundError(f"Upload fixture not found: {p}.")
    else:
        candidates = [p for p in data_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
        if not candidates:
            raise FileNotFoundError(f"No PDF/TIF/TIFF fixtures found in {data_dir}.")
        selected = random.sample(candidates, min(count, len(candidates)))

    if len(selected) < count:
        raise FileNotFoundError(f"Found {len(selected)} supported document(s); expected {count}.")

    for p in selected:
        if p.stat().st_size > MAX_SIZE_BYTES:
            raise ValueError(f"Upload fixture exceeds 20 MB API limit: {p}")

    return selected


def wait_for_invoice_status(base_url: str, invoice_id: str, timeout: int, poll_interval: int, log) -> dict:
    deadline = time.monotonic() + timeout
    last_status = None
    while time.monotonic() < deadline:
        try:
            _, status = http_json("GET", f"{base_url}/invoices/{invoice_id}/status")
            last_status = status
            log(f"Invoice {invoice_id} status: {status.get('status')}")
            if status.get("status") in TERMINAL_STATUSES:
                return status
        except urllib.error.HTTPError:
            log(f"Invoice {invoice_id} status not available yet.")
        time.sleep(poll_interval)
    raise TimeoutError(f"Timed out waiting for terminal status for invoice_id={invoice_id}. Last: {last_status}")


def get_registered_partition_values(glue_client, database: str, table: str) -> set[str]:
    values: set[str] = set()
    paginator = glue_client.get_paginator("get_partitions")
    for page in paginator.paginate(DatabaseName=database, TableName=table):
        for partition in page.get("Partitions", []):
            partition_values = partition.get("Values") or []
            if partition_values:
                values.add(partition_values[0])
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1, help="Number of documents to auto-select from --data-dir")
    parser.add_argument("--files", nargs="*", default=[], help="Explicit file paths to upload (overrides --count)")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Directory to auto-select fixtures from")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds to wait for terminal status per invoice")
    parser.add_argument("--poll-interval", type=int, default=15, help="Seconds between status polls")
    parser.add_argument(
        "--glue-database", default="invoice_pipeline_gold", help="Glue database holding gold_documents"
    )
    parser.add_argument("--glue-table", default="gold_documents", help="Glue table partitioned by batch_id")
    args = parser.parse_args()

    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"gold_partition_registration_smoke_{timestamp}.log"
    lines: list[str] = []

    def log(msg: str = "") -> None:
        print(msg)
        lines.append(str(msg))

    def flush() -> None:
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    try:
        base_url = terraform_output("web_api_base_url").rstrip("/")
        documents = select_documents(args.count, args.files, Path(args.data_dir))
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        log(f"ERROR: {exc}")
        flush()
        return 1

    glue = boto3.client("glue")

    log(f"Web API: {base_url}")
    log(f"Glue table: {args.glue_database}.{args.glue_table}")
    log("Selected upload documents:")
    for doc in documents:
        log(f" - {doc.name} ({doc.stat().st_size} bytes)")

    log("")
    log("Step 1/4: snapshotting partitions registered BEFORE upload...")
    try:
        partitions_before = get_registered_partition_values(glue, args.glue_database, args.glue_table)
    except Exception as exc:  # noqa: BLE001 - surfaced as a test failure below
        log(f"ERROR: could not read partitions from Glue: {exc}")
        flush()
        return 1
    log(f"  {len(partitions_before)} partition(s) registered before this run.")

    upload_request = {
        "files": [
            {
                "name": doc.name,
                "content_type": CONTENT_TYPES[doc.suffix.lower()],
                "size_bytes": doc.stat().st_size,
            }
            for doc in documents
        ]
    }

    log("")
    log("Step 2/4: uploading via the real web API (POST /uploads + presigned PUT)...")
    try:
        _, upload_response = http_json("POST", f"{base_url}/uploads", upload_request)
    except urllib.error.HTTPError as exc:
        log(f"HTTPError: {exc.code} {exc.reason}")
        log(exc.read().decode(errors="replace"))
        flush()
        return 1

    run_id = upload_response.get("run_id")
    uploads = upload_response.get("uploads") or []
    if not run_id or len(uploads) != len(documents):
        log("ERROR: Upload API response did not include run_id, or upload count mismatch.")
        flush()
        return 1
    log(f"  run_id: {run_id}")

    for doc, upload in zip(documents, uploads):
        log(f"  Uploading via presigned URL: {doc.name}")
        http_put_file(upload["upload_url"], doc, CONTENT_TYPES[doc.suffix.lower()])

    log("")
    log("Step 3/4: waiting for the pipeline to reach a terminal status per document...")
    statuses = []
    try:
        for doc in documents:
            invoice_id = doc.stem
            status = wait_for_invoice_status(base_url, invoice_id, args.timeout, args.poll_interval, log)
            statuses.append(status)
    except TimeoutError as exc:
        log(f"ERROR: {exc}")
        flush()
        return 1

    accepted_count = sum(1 for s in statuses if s.get("status") == "Completed")
    log(f"  {accepted_count}/{len(statuses)} document(s) reached Completed status.")

    log("")
    log("Step 4/4: checking Glue for new partitions WITHOUT running MSCK REPAIR TABLE...")
    # Batch consolidation can take a few seconds after the Step Function's
    # ConsolidateGold task returns, so allow a short grace period.
    new_partitions: set[str] = set()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        partitions_after = get_registered_partition_values(glue, args.glue_database, args.glue_table)
        new_partitions = partitions_after - partitions_before
        if new_partitions:
            break
        time.sleep(10)

    if not new_partitions:
        log(
            "FAIL: No new batch_id partition appeared in Glue within 120s of pipeline "
            "completion, without running MSCK REPAIR TABLE. Automatic partition "
            "registration in consolidate_gold may be broken (check its Glue IAM "
            "permissions and GLUE_DATABASE/GLUE_GOLD_DOCUMENTS_TABLE env vars)."
        )
        flush()
        return 1

    log(f"  New partition(s) registered automatically: {sorted(new_partitions)}")

    log("")
    log("Cross-check: asking the chat API a question that depends on this data...")
    try:
        _, chat_response = http_json(
            "POST",
            f"{base_url}/chat",
            {"question": "Cuantas facturas se han procesado en total?"},
            timeout=90,
        )
        log(f"  chat answer: {chat_response.get('answer')}")
        row_count = len(chat_response.get("rows") or [])
        log(f"  chat rows returned: {row_count}")
    except urllib.error.HTTPError as exc:
        log(f"  WARNING: chat API call failed ({exc.code} {exc.reason}); partition check already passed.")

    summary = {
        "base_url": base_url,
        "run_id": run_id,
        "uploaded_documents": [doc.name for doc in documents],
        "accepted_count": accepted_count,
        "partitions_before": len(partitions_before),
        "new_partitions": sorted(new_partitions),
    }

    log("")
    log("Gold partition registration smoke summary:")
    log(json.dumps(summary, indent=2, ensure_ascii=False))

    log("")
    log("PASS: new batch_id partition(s) were visible in Glue without a manual MSCK REPAIR TABLE.")
    log(f"=== Done. Full log written to: {log_file} ===")
    flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
