#!/usr/bin/env bash
# Terraform Step 2d: lookup remaining resource IDs for imports.tf.
#
# Git Bash port of the former step2d_lookup_remaining_ids.ps1.
#
# Usage:
#   tests/terraform/step2d_lookup_remaining_ids.sh [REGION] [GLUE_DATABASE] [ATHENA_WORKGROUP] [TEXTRACT_POLICY_NAME]
#
# Defaults match the invoice-pipeline-dev resources.

set -uo pipefail

REGION="${1:-us-east-1}"
GLUE_DATABASE="${2:-invoice_pipeline_gold}"
ATHENA_WORKGROUP="${3:-invoice-pipeline-dev}"
TEXTRACT_POLICY_NAME="${4:-invoice-pipeline-dev-textract}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOGS_DIR="$REPO_ROOT/logs"
mkdir -p "$LOGS_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOGS_DIR/tf_step2d_lookup_remaining_ids_${TIMESTAMP}.log"

log() {
  echo "$1" | tee -a "$LOG_FILE"
}

lookup() {
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

log "Terraform Step 2d: lookup remaining resource IDs for imports.tf"
log "Region: $REGION"
log "Timestamp: $(date -Iseconds)"

# 1) Glue Catalog Tables
lookup "Glue table: gold_documents" \
  glue get-table --database-name "$GLUE_DATABASE" --name gold_documents --region "$REGION"

lookup "Glue table: gold_invoice_summary" \
  glue get-table --database-name "$GLUE_DATABASE" --name gold_invoice_summary --region "$REGION"

# 2) Athena WorkGroup
lookup "Athena workgroup: $ATHENA_WORKGROUP" \
  athena get-work-group --work-group "$ATHENA_WORKGROUP" --region "$REGION"

# 3) IAM Policy (Textract)
lookup "IAM policy: $TEXTRACT_POLICY_NAME" \
  iam list-policies --scope Local --query "Policies[?PolicyName=='$TEXTRACT_POLICY_NAME']" --region "$REGION"

log ""
log "=== Done. Full log written to: $LOG_FILE ==="
