#!/usr/bin/env bash
# Terraform Step 2: plan (saved to tfplan for a later apply).
#
# Git Bash port of the former step2_plan_apply_output.ps1.
#
# Usage:
#   tests/terraform/step2_plan_apply_output.sh [TERRAFORM_DIR] [VAR_FILE]
#
# TERRAFORM_DIR defaults to infra/envs/dev, VAR_FILE to terraform.tfvars.

set -uo pipefail

TERRAFORM_DIR="${1:-infra/envs/dev}"
VAR_FILE="${2:-terraform.tfvars}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOGS_DIR="$REPO_ROOT/logs"
mkdir -p "$LOGS_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOGS_DIR/tf_step2_plan_output_${TIMESTAMP}.log"

log() {
  echo "$1" | tee -a "$LOG_FILE"
}

log "Terraform Step 2: plan + output"
log "Terraform dir: $TERRAFORM_DIR"
log "Var file: $VAR_FILE"
log "Timestamp: $(date -Iseconds)"

log ""
log "=== terraform plan ==="
log "Command: terraform -chdir=$TERRAFORM_DIR plan -var-file=$VAR_FILE -out=tfplan"
plan_output="$(terraform "-chdir=$TERRAFORM_DIR" plan "-var-file=$VAR_FILE" -out=tfplan 2>&1)"
plan_exit_code=$?
echo "$plan_output" | tee -a "$LOG_FILE"
log "Exit code: $plan_exit_code"

if [[ $plan_exit_code -ne 0 ]]; then
  log ""
  log "=== Plan failed (exit $plan_exit_code). ==="
  log "=== Done. Full log written to: $LOG_FILE ==="
  exit $plan_exit_code
fi

log ""
log "=== Plan succeeded. Saved as tfplan in $TERRAFORM_DIR. Run 'terraform -chdir=$TERRAFORM_DIR apply tfplan' to apply it. ==="
log "=== Done. Full log written to: $LOG_FILE ==="
