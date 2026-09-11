#!/usr/bin/env bash
# Terraform Step 4: get CloudFront portal URL and distribution ID.
#
# Git Bash port of the former step4_get_portal_url.ps1.
#
# Usage:
#   tests/terraform/step4_get_portal_url.sh [TERRAFORM_DIR]
#
# TERRAFORM_DIR defaults to infra/envs/dev.

set -uo pipefail

TERRAFORM_DIR="${1:-infra/envs/dev}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOGS_DIR="$REPO_ROOT/logs"
mkdir -p "$LOGS_DIR"

TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOGS_DIR/tf_step4_get_portal_url_${TIMESTAMP}.log"

log() {
  echo "$1" | tee -a "$LOG_FILE"
}

log "Terraform Step 4: get CloudFront portal URL"
log "Terraform dir: $TERRAFORM_DIR"
log "Timestamp: $(date -Iseconds)"

# 1) Portal URL (https://<cloudfront-domain>)
log ""
log "=== terraform output portal_url ==="
log "Command: terraform -chdir=$TERRAFORM_DIR output -raw portal_url"
portal_url="$(terraform "-chdir=$TERRAFORM_DIR" output -raw portal_url 2>&1)"
exit_code=$?
log "$portal_url"
log "Exit code: $exit_code"

# 2) CloudFront distribution ID (useful for cache invalidation)
log ""
log "=== terraform output cloudfront_distribution_id ==="
log "Command: terraform -chdir=$TERRAFORM_DIR output -raw cloudfront_distribution_id"
distribution_id="$(terraform "-chdir=$TERRAFORM_DIR" output -raw cloudfront_distribution_id 2>&1)"
dist_exit_code=$?
log "$distribution_id"
log "Exit code: $dist_exit_code"

if [[ $exit_code -eq 0 ]]; then
  log ""
  log "=== Portal URL ==="
  log "$portal_url"
fi

log ""
log "=== Done. Full log written to: $LOG_FILE ==="
