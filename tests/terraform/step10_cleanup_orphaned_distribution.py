#!/usr/bin/env python3
"""Recover from a `terraform destroy` that fails on CloudFront OAC / WAF
Web ACL with "still in use" errors (see docs/destruction_guide.md Section 6).

This happens when aws_cloudfront_distribution.site is removed from Terraform
state but the distribution is still live in AWS -- CloudFront requires a
distribution to be disabled and fully propagated (Status: Deployed, Enabled:
false) before it can be deleted, and that propagation can outlast a single
`terraform destroy` call. The orphaned distribution keeps referencing the
OAC and WAF Web ACL, so AWS refuses to delete either.

Usage:
  tests/terraform/step10_cleanup_orphaned_distribution.py --comment <comment>
  tests/terraform/step10_cleanup_orphaned_distribution.py --id <distribution_id>

Without --delete, this finds the distribution, disables it if still enabled,
and polls until propagation finishes (Status: Deployed, Enabled: false) --
no delete-distribution call is made. Re-run with --delete once propagation
is done to actually delete it. Splitting these avoids leaving a half-deleted
distribution if the script is interrupted during the slow propagation wait.

After a successful delete, re-run `terraform destroy` -- the OAC and WAF Web
ACL should now delete cleanly since nothing references them.

Logs combined output to logs/tf_step10_cleanup_orphaned_distribution_<timestamp>.log.
"""

import argparse
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGS_DIR = REPO_ROOT / "logs"

POLL_INTERVAL_SECONDS = 30
POLL_TIMEOUT_SECONDS = 20 * 60


def run(args):
    result = subprocess.run(args, cwd=REPO_ROOT, capture_output=True, text=True)
    return result.stdout, result.stderr, result.returncode


def run_json(args, log):
    stdout, stderr, code = run(args)
    if code != 0:
        log(f"Command failed: {' '.join(args)}")
        log(stderr.rstrip())
        raise SystemExit(code)
    return json.loads(stdout)


def find_distribution(comment, log):
    log(f"=== Searching distributions for Comment == {comment!r} ===")
    items = run_json(
        [
            "aws", "cloudfront", "list-distributions",
            "--query", "DistributionList.Items[]",
        ],
        log,
    ) or []
    matches = [i for i in items if i.get("Comment") == comment]
    if not matches:
        log("No distribution found with that Comment. All distributions:")
        for i in items:
            log(f"  {i.get('Id')} Comment={i.get('Comment')!r} Enabled={i.get('Enabled')} Status={i.get('Status')}")
        raise SystemExit(1)
    if len(matches) > 1:
        log(f"Multiple distributions matched, refusing to guess: {[m['Id'] for m in matches]}")
        raise SystemExit(1)
    dist_id = matches[0]["Id"]
    log(f"Found distribution: {dist_id}")
    return dist_id


def get_config(dist_id, log):
    stdout, stderr, code = run(
        ["aws", "cloudfront", "get-distribution-config", "--id", dist_id, "--output", "json"]
    )
    if code != 0:
        log(f"Failed to get distribution config: {stderr.rstrip()}")
        raise SystemExit(code)
    return json.loads(stdout)


def disable_distribution(dist_id, config, log):
    etag = config["ETag"]
    dist_config = config["DistributionConfig"]
    dist_config["Enabled"] = False

    tmp_path = REPO_ROOT / f"dist_config_disabled_{dist_id}.json"
    tmp_path.write_text(json.dumps(dist_config), encoding="utf-8")
    log(f"Wrote disabled config to {tmp_path} (UTF-8, no BOM)")

    log(f"=== Disabling distribution {dist_id} (ETag {etag}) ===")
    stdout, stderr, code = run(
        [
            "aws", "cloudfront", "update-distribution",
            "--id", dist_id,
            "--distribution-config", f"file://{tmp_path}",
            "--if-match", etag,
        ]
    )
    tmp_path.unlink(missing_ok=True)
    if code != 0:
        log(f"Failed to disable distribution: {stderr.rstrip()}")
        raise SystemExit(code)
    log("Disable request accepted, waiting for propagation.")


def wait_for_disabled(dist_id, log):
    log(f"=== Waiting for {dist_id} to reach Status=Deployed, Enabled=false ===")
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    while True:
        info = run_json(
            [
                "aws", "cloudfront", "get-distribution",
                "--id", dist_id,
                "--query", "Distribution.{Status:Status,Enabled:DistributionConfig.Enabled}",
            ],
            log,
        )
        log(f"  Status={info.get('Status')} Enabled={info.get('Enabled')}")
        if info.get("Status") == "Deployed" and info.get("Enabled") is False:
            log("Propagation complete.")
            return
        if time.monotonic() > deadline:
            log(f"Timed out after {POLL_TIMEOUT_SECONDS}s waiting for propagation. Re-run this script later.")
            raise SystemExit(1)
        time.sleep(POLL_INTERVAL_SECONDS)


def delete_distribution(dist_id, log):
    config = get_config(dist_id, log)
    etag = config["ETag"]
    log(f"=== Deleting distribution {dist_id} (fresh ETag {etag}) ===")
    stdout, stderr, code = run(
        ["aws", "cloudfront", "delete-distribution", "--id", dist_id, "--if-match", etag]
    )
    if code != 0:
        log(f"Failed to delete distribution: {stderr.rstrip()}")
        raise SystemExit(code)
    log("Distribution deleted. Re-run `terraform destroy` to clean up the OAC and WAF Web ACL.")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--comment", default="invoice-pipeline-dev", help="Distribution Comment to match (default: invoice-pipeline-dev)")
    parser.add_argument("--id", default=None, help="Distribution ID (skips the list-distributions lookup)")
    parser.add_argument("--delete", action="store_true", help="Actually delete the distribution (default: find/disable/wait only)")
    args = parser.parse_args()

    LOGS_DIR.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"tf_step10_cleanup_orphaned_distribution_{timestamp}.log"
    lines = []

    def log(msg=""):
        print(msg)
        lines.append(msg)

    try:
        log("Terraform Step 10: clean up orphaned CloudFront distribution")
        log(f"Delete requested: {args.delete}")
        log(f"Timestamp: {datetime.datetime.now().isoformat()}")

        dist_id = args.id or find_distribution(args.comment, log)

        if args.delete:
            wait_for_disabled(dist_id, log)
            delete_distribution(dist_id, log)
        else:
            config = get_config(dist_id, log)
            if config["DistributionConfig"].get("Enabled"):
                disable_distribution(dist_id, config, log)
            else:
                log(f"{dist_id} is already disabled.")
            wait_for_disabled(dist_id, log)
            log("")
            log(f"Propagation done. Re-run with --id {dist_id} --delete to delete it.")
    finally:
        log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nFull log written to: {log_file}")


if __name__ == "__main__":
    sys.exit(main())
