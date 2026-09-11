#!/usr/bin/env bash
# Terraform Step 2c: check state lock (reads the S3 native lock object, then
# re-runs 'terraform apply tfplan' to capture the full lock error if present).
#
# Git Bash port of the former step2c_check_lock.ps1.
#
# Usage:
#   tests/terraform/step2c_check_lock.sh [TERRAFORM_DIR] [BUCKET_NAME] [STATE_KEY] [REGION]
#
# Defaults match the invoice-pipeline-dev backend.

set -uo pipefail

TERRAFORM_DIR="${1:-infra/envs/dev}"
BUCKET_NAME="${2:-invoice-pipeline-dev-tfstate-184670914470}"
STATE_KEY="${3:-invoice-pipeline/dev/terraform.tfstate}"
REGION="${4:-us-east-1}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOGS_DIR="$REPO_ROOT/logs"
mkdir -p "$LOGS_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOGS_DIR/tf_step2c_check_lock_${TIMESTAMP}.log"

log() {
  echo "$1" | tee -a "$LOG_FILE"
}

log "Terraform Step 2c: check state lock"
log "Terraform dir: $TERRAFORM_DIR"
log "Bucket: $BUCKET_NAME"
log "State key: $STATE_KEY"
log "Timestamp: $(date -Iseconds)"

# 1) Read the S3 native lock object (use_lockfile = true -> "<state-key>.tflock")
LOCK_KEY="${STATE_KEY}.tflock"
TMP_LOCK_FILE="${TMPDIR:-/tmp}/tfstate_lock_${TIMESTAMP}.json"

log ""
log "=== Reading S3 lock object: s3://$BUCKET_NAME/$LOCK_KEY ==="
lock_output="$(aws s3api get-object --bucket "$BUCKET_NAME" --key "$LOCK_KEY" --region "$REGION" "$TMP_LOCK_FILE" 2>&1)"
lock_exit_code=$?
echo "$lock_output" | tee -a "$LOG_FILE"
log "Exit code: $lock_exit_code"

if [[ $lock_exit_code -eq 0 && -f "$TMP_LOCK_FILE" ]]; then
  log ""
  log "=== Lock file content ==="
  cat "$TMP_LOCK_FILE" | tee -a "$LOG_FILE"
  rm -f "$TMP_LOCK_FILE"
else
  log ""
  log "=== No lock object found (state is not locked, or different lock mechanism). ==="
fi

# 2) Re-run terraform apply to capture the full lock error message (including LockID)
log ""
log "=== terraform apply tfplan (capturing full output) ==="
log "Command: terraform -chdir=$TERRAFORM_DIR apply tfplan"
apply_output="$(terraform "-chdir=$TERRAFORM_DIR" apply tfplan 2>&1)"
apply_exit_code=$?

echo "$apply_output" | tee -a "$LOG_FILE"
log "Exit code: $apply_exit_code"

log ""
log "=== Done. Full log written to: $LOG_FILE ==="
