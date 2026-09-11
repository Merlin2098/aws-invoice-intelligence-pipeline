#!/usr/bin/env bash
# Inspect the Terraform state bucket directly: existence, current object
# listing, and object versions (useful for leftover-delete-marker debugging
# on a versioned bucket).
#
# Git Bash port of the former check_tfstate_bucket.ps1.
#
# Usage:
#   tests/check_tfstate_bucket.sh [BUCKET_NAME] [REGION]
#
# Defaults match the invoice-pipeline-dev state bucket.

set -uo pipefail

BUCKET_NAME="${1:-invoice-pipeline-dev-tfstate-184670914470}"
REGION="${2:-us-east-1}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS_DIR="$REPO_ROOT/logs"
mkdir -p "$LOGS_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOGS_DIR/tfstate_bucket_check_${TIMESTAMP}.log"

log() {
  echo "$1" | tee -a "$LOG_FILE"
}

run_check() {
  local title="$1"
  shift
  log ""
  log "=== $title ==="
  log "Command: aws $*"
  local output
  output="$(aws "$@" 2>&1)"
  local exit_code=$?
  echo "$output" | tee -a "$LOG_FILE"
  log "Exit code: $exit_code"
}

log "Terraform state bucket check"
log "Bucket: $BUCKET_NAME"
log "Region: $REGION"
log "Timestamp: $(date -Iseconds)"

run_check "head-bucket (existence check)" \
  s3api head-bucket --bucket "$BUCKET_NAME" --region "$REGION"

run_check "list objects (current state)" \
  s3 ls "s3://$BUCKET_NAME/" --recursive

run_check "list object versions (versioned state, leftover deletes)" \
  s3api list-object-versions --bucket "$BUCKET_NAME" --region "$REGION"

log ""
log "=== Done. Full log written to: $LOG_FILE ==="
