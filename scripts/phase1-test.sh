#!/usr/bin/env bash
# ECONITH Phase 1 — user-controlled Linux/cloud CPU-only operator entrypoint.
set -euo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
MODE="${1:-doctor}"
if [[ $# -gt 0 ]]; then shift; fi
PYTHON="$REPO/.venv/bin/python"
if [[ ! -x "$PYTHON" ]]; then PYTHON=python3; fi

case "$MODE" in
  setup)
    python3 -m venv .venv
    "$REPO/.venv/bin/python" -m pip install -r requirements.txt
    ;;
  doctor)
    "$PYTHON" -m ci.phase1_operator_lab doctor "$@"
    ;;
  quick|diagnostic|hour|day)
    "$PYTHON" -m ci.phase1_operator_lab smoke \
      --allow-public-market-data --profile "$MODE" "$@"
    ;;
  report)
    "$PYTHON" -m ci.phase1_operator_lab report "$@"
    ;;
  *)
    echo "Usage: bash scripts/phase1-test.sh setup|doctor|quick|diagnostic|hour|day|report [args]" >&2
    exit 2
    ;;
esac
