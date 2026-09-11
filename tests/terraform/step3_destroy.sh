#!/usr/bin/env bash
# Terraform Step 3: destroy. Defaults to a dry run (plan -destroy); pass
# --confirm to actually run 'terraform destroy'.
#
# Git Bash port of the former step3_destroy.ps1.
#
# Usage:
#   tests/terraform/step3_destroy.sh [--confirm] [TERRAFORM_DIR] [VAR_FILE]
#
# TERRAFORM_DIR defaults to infra/envs/dev, VAR_FILE to terraform.tfvars.
# Requires the same explicit approval as terraform destroy itself, per
# AGENTS.md -- --confirm is not a substitute for that approval.

set -uo pipefail

CONFIRM=0
POSITIONAL=()
for arg in "$@"; do
  case "$arg" in
    --confirm)
      CONFIRM=1 ;;
    *)
      POSITIONAL+=("$arg") ;;
  esac
done

TERRAFORM_DIR="${POSITIONAL[0]:-infra/envs/dev}"
VAR_FILE="${POSITIONAL[1]:-terraform.tfvars}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOGS_DIR="$REPO_ROOT/logs"
mkdir -p "$LOGS_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOGS_DIR/tf_step3_destroy_${TIMESTAMP}.log"

log() {
  echo "$1" | tee -a "$LOG_FILE"
}

run_step() {
  local title="$1"
  shift
  log ""
  log "=== $title ==="
  log "Command: terraform $*"
  local output
  output="$(terraform "$@" 2>&1)"
  local exit_code=$?
  echo "$output" | tee -a "$LOG_FILE"
  log "Exit code: $exit_code"
  return $exit_code
}

log "Terraform Step 3: destroy"
log "Terraform dir: $TERRAFORM_DIR"
log "Var file: $VAR_FILE"
log "Confirm requested: $([[ $CONFIRM -eq 1 ]] && echo true || echo false)"
log "Timestamp: $(date -Iseconds)"

if [[ $CONFIRM -ne 1 ]]; then
  log ""
  log "=== Destroy not confirmed. Running plan -destroy (dry run) only. ==="
  log "=== Pass --confirm to run 'terraform destroy' for real. ==="

  run_step "terraform plan -destroy (dry run)" "-chdir=$TERRAFORM_DIR" plan -destroy "-var-file=$VAR_FILE"

  log ""
  log "=== Done. Full log written to: $LOG_FILE ==="
  exit 0
fi

run_step "terraform destroy" "-chdir=$TERRAFORM_DIR" destroy "-var-file=$VAR_FILE" -auto-approve

log ""
log "=== Done. Full log written to: $LOG_FILE ==="
