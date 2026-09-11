#!/usr/bin/env bash
# Diagnose the terraform CLI environment: which binary resolves, any TF_*
# env vars, and a sanity-check version call against infra/envs/dev.
#
# Git Bash port of the former check_terraform_cli_env.ps1.
#
# Usage:
#   tests/check_terraform_cli_env.sh

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOGS_DIR="$REPO_ROOT/logs"
mkdir -p "$LOGS_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOGS_DIR/terraform_cli_env_check_${TIMESTAMP}.log"

log() {
  echo "$1" | tee -a "$LOG_FILE"
}

run_check() {
  local title="$1"
  shift
  log ""
  log "=== $title ==="
  local output
  output="$("$@" 2>&1)"
  if [[ -z "$output" ]]; then
    log "(no output)"
  else
    echo "$output" | tee -a "$LOG_FILE"
  fi
}

log "Terraform CLI environment check"
log "Timestamp: $(date -Iseconds)"

log ""
log "=== which -a terraform ==="
which -a terraform 2>&1 | tee -a "$LOG_FILE" || log "(terraform not found on PATH)"

log ""
log "=== type terraform ==="
type -a terraform 2>&1 | tee -a "$LOG_FILE" || log "(no shell function/alias for terraform)"

log ""
log "=== TF_* environment variables ==="
env | grep '^TF_' | tee -a "$LOG_FILE" || log "(none set)"

log ""
log "=== Shell rc files referencing terraform ==="
for rc in "$HOME/.bashrc" "$HOME/.bash_profile" "$HOME/.profile"; do
  if [[ -f "$rc" ]]; then
    log "-- $rc --"
    grep -n "terraform" "$rc" 2>&1 | tee -a "$LOG_FILE" || log "(no match)"
  fi
done

log ""
log "=== terraform -chdir version (sanity check) ==="
version_output="$(terraform -chdir=infra/envs/dev version 2>&1)"
version_exit_code=$?
echo "$version_output" | tee -a "$LOG_FILE"
log "Exit code: $version_exit_code"

log ""
log "=== Done. Full log written to: $LOG_FILE ==="
