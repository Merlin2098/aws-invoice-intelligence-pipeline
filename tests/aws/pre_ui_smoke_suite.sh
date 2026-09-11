#!/usr/bin/env bash
# Pre-UI smoke suite: runs the direct AWS pipeline smoke, then the upload/status
# API smoke, then the chat API smoke, in that order.
#
# Git Bash port of the former tests/pre_ui_smoke_suite.ps1. Each step is now a
# Python script under tests/aws/ using boto3 for AWS calls and real HTTP for
# the web API endpoints.
#
# Usage:
#   tests/aws/pre_ui_smoke_suite.sh [--direct-count N] [--upload-count N]
#     [--upload-files "path1 path2 ..."] [--chat-questions "q1|q2|q3"]
#     [--timeout SECONDS] [--skip-upload-api] [--skip-chat-api]
#     [--allow-failed-upload-terminal-status]
#
# --chat-questions takes a single string with questions separated by "|"
# (so a question containing spaces doesn't need extra quoting gymnastics),
# e.g.: --chat-questions "How much did we spend?|Top 5 suppliers?"

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

DIRECT_COUNT=5
UPLOAD_COUNT=1
UPLOAD_FILES=()
CHAT_QUESTIONS="Cuantas facturas hay disponibles?|Cual es el monto total de facturas por proveedor?|Cuantas facturas hay por moneda?"
TIMEOUT_SECONDS=900
SKIP_UPLOAD_API=0
SKIP_CHAT_API=0
ALLOW_FAILED_UPLOAD_TERMINAL_STATUS=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --direct-count)
      DIRECT_COUNT="$2"; shift 2 ;;
    --upload-count)
      UPLOAD_COUNT="$2"; shift 2 ;;
    --upload-files)
      IFS=' ' read -r -a UPLOAD_FILES <<< "$2"; shift 2 ;;
    --chat-questions)
      CHAT_QUESTIONS="$2"; shift 2 ;;
    --timeout)
      TIMEOUT_SECONDS="$2"; shift 2 ;;
    --skip-upload-api)
      SKIP_UPLOAD_API=1; shift ;;
    --skip-chat-api)
      SKIP_CHAT_API=1; shift ;;
    --allow-failed-upload-terminal-status)
      ALLOW_FAILED_UPLOAD_TERMINAL_STATUS=1; shift ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 1 ;;
  esac
done

echo "Running pre-UI smoke suite..."
echo ""

echo "1/3 Direct AWS pipeline smoke"
python tests/aws/e2e_pipeline_smoke.py --count "$DIRECT_COUNT" --timeout "$TIMEOUT_SECONDS"
echo ""

if [[ "$SKIP_UPLOAD_API" -eq 0 ]]; then
  echo "2/3 Upload/status API smoke"
  upload_args=(--count "$UPLOAD_COUNT" --timeout "$TIMEOUT_SECONDS")
  if [[ ${#UPLOAD_FILES[@]} -gt 0 ]]; then
    upload_args+=(--files "${UPLOAD_FILES[@]}")
  fi
  if [[ "$ALLOW_FAILED_UPLOAD_TERMINAL_STATUS" -eq 1 ]]; then
    upload_args+=(--allow-failed-terminal-status)
  fi
  python tests/aws/upload_status_api_smoke.py "${upload_args[@]}"
  echo ""
else
  echo "2/3 Upload/status API smoke skipped."
  echo ""
fi

if [[ "$SKIP_CHAT_API" -eq 0 ]]; then
  echo "3/3 Chat API smoke"
  IFS='|' read -r -a questions_array <<< "$CHAT_QUESTIONS"
  python tests/aws/chat_api_smoke.py --questions "${questions_array[@]}"
  echo ""
else
  echo "3/3 Chat API smoke skipped."
  echo ""
fi

echo "Pre-UI smoke suite completed."
