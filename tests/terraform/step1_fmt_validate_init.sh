#!/usr/bin/env bash
# Terraform Step 1: fmt + validate + init.
#
# Git Bash port of the former step1_fmt_validate_init.ps1.
#
# Usage:
#   tests/terraform/step1_fmt_validate_init.sh [TERRAFORM_DIR]
#
# TERRAFORM_DIR defaults to infra/envs/dev.

set -uo pipefail

TERRAFORM_DIR="${1:-infra/envs/dev}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOGS_DIR="$REPO_ROOT/logs"
mkdir -p "$LOGS_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOGS_DIR/tf_step1_fmt_validate_init_${TIMESTAMP}.log"

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

log "Terraform Step 1: fmt + validate + init"
log "Terraform dir: $TERRAFORM_DIR"
log "Timestamp: $(date -Iseconds)"

run_step "terraform fmt -recursive" "-chdir=$TERRAFORM_DIR" fmt -recursive
run_step "terraform validate" "-chdir=$TERRAFORM_DIR" validate
run_step "terraform init" "-chdir=$TERRAFORM_DIR" init

log ""
log "=== Done. Full log written to: $LOG_FILE ==="
