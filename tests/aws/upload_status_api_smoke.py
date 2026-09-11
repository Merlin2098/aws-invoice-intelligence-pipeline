#!/usr/bin/env python3
"""Smoke test for the upload + status web API (POST /uploads, GET /invoices/{id}/status).

Mirrors the frontend's upload flow: requests presigned PUT URLs from the web
API, uploads document(s) via those presigned URLs, polls invoice status
until terminal, then checks the invoice history endpoint.

Git Bash / Python port of the former tests/api_upload_status_smoke.ps1.
The upload/status endpoints are HTTP-only (API Gateway -> Lambda), so they
are called with real HTTP requests; boto3 is not applicable here since
there is no direct AWS service call in this flow (presigned URLs are
plain HTTP PUTs, and the API URL is read from Terraform output like the
rest of this test suite).

Usage:
    python tests/aws/upload_status_api_smoke.py [--count N] [--files path1 path2 ...]
        [--data-dir DIR] [--timeout SECONDS] [--poll-interval SECONDS]
        [--allow-failed-terminal-status]
"""

from __future__ import annotations

import argparse
import datetime
import json
import mimetypes
import random
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

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
                raise FileNotFoundError(
                    f"Upload fixture not found: {p}. Pass a real PDF/TIF/TIFF path or omit --files "
                    f"to auto-select from {data_dir}."
                )
    else:
        candidates = [p for p in data_dir.iterdir() if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS]
        if not candidates:
            raise FileNotFoundError(f"No PDF/TIF/TIFF fixtures found in {data_dir}.")
        selected = random.sample(candidates, min(count, len(candidates)))

    if len(selected) < count:
        raise FileNotFoundError(
            f"Found {len(selected)} supported document(s); expected {count}. "
            f"Add PDF/TIF/TIFF files under {data_dir} or pass --files explicitly."
        )

    for p in selected:
        if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Upload API smoke only accepts PDF/TIF/TIFF. Unsupported file: {p}")
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
    raise TimeoutError(
        f"Timed out waiting for terminal status for invoice_id={invoice_id}. Last status: {json.dumps(last_status)}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1, help="Number of documents to auto-select from --data-dir")
    parser.add_argument("--files", nargs="*", default=[], help="Explicit file paths to upload (overrides --count)")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Directory to auto-select fixtures from")
    parser.add_argument("--timeout", type=int, default=900, help="Seconds to wait for terminal status per invoice")
    parser.add_argument("--poll-interval", type=int, default=15, help="Seconds between status polls")
    parser.add_argument(
        "--allow-failed-terminal-status",
        action="store_true",
        help="Do not treat a Failed terminal status as a smoke-test failure",
    )
    args = parser.parse_args()

    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"upload_status_api_smoke_{timestamp}.log"
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

    log(f"Web API: {base_url}")
    log("Selected upload documents:")
    for doc in documents:
        log(f" - {doc.name} ({doc.stat().st_size} bytes)")

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

    log(f"Upload run_id: {run_id}")

    for doc, upload in zip(documents, uploads):
        log(f"Uploading via presigned URL: {doc.name}")
        http_put_file(upload["upload_url"], doc, CONTENT_TYPES[doc.suffix.lower()])

    statuses = []
    try:
        for doc in documents:
            invoice_id = doc.stem
            status = wait_for_invoice_status(base_url, invoice_id, args.timeout, args.poll_interval, log)
            if status.get("status") == "Failed" and not args.allow_failed_terminal_status:
                log(
                    f"ERROR: Invoice {invoice_id} reached Failed status. Re-run with "
                    "--allow-failed-terminal-status if this is expected for the fixture."
                )
                flush()
                return 1
            statuses.append(status)
    except TimeoutError as exc:
        log(f"ERROR: {exc}")
        flush()
        return 1

    _, history = http_json("GET", f"{base_url}/invoices?limit=20")

    summary = {
        "base_url": base_url,
        "run_id": run_id,
        "uploaded_documents": [doc.name for doc in documents],
        "terminal_statuses": statuses,
        "history_count": len(history.get("invoices") or []),
    }

    log("")
    log("Upload/status API smoke summary:")
    log(json.dumps(summary, indent=2, ensure_ascii=False))

    log("")
    log(f"=== Done. Full log written to: {log_file} ===")
    flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
