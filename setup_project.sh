#!/usr/bin/env bash
# =============================================================================
# ECONITH :: Phase 0 :: Core skeleton generator
# -----------------------------------------------------------------------------
# Creates the AI-001 Core Engine directory tree. The Core Engine is an
# independent foundation, decoupled from the Trading (econith_quant) and the
# Simulator (ECONITH World) components.
#
# Safe to re-run: it only creates missing files/dirs (idempotent).
#
# Usage:
#     bash setup_project.sh
# =============================================================================
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

echo "==> Generating ECONITH core skeleton in: $ROOT_DIR"

# --- helper: create a python package dir with __init__.py ---------------------
pkg() {
  mkdir -p "$1"
  [[ -f "$1/__init__.py" ]] || : > "$1/__init__.py"
}
# --- helper: touch a file if absent ------------------------------------------
file() { [[ -f "$1" ]] || : > "$1"; }

# 1) config/ ------------------------------------------------------------------
pkg config
for f in settings database environment exchange logging; do file "config/${f}.py"; done

# 2) core/ --------------------------------------------------------------------
pkg core
for f in engine event_bus scheduler startup shutdown constants exceptions interfaces; do
  file "core/${f}.py"
done

# 3) infrastructure/ ----------------------------------------------------------
pkg infrastructure
pkg infrastructure/websocket
for f in streamer reconnect heartbeat orderbook trades ticker kline; do
  file "infrastructure/websocket/${f}.py"
done
pkg infrastructure/rest
for f in client historical exchange_info account candles; do
  file "infrastructure/rest/${f}.py"
done
pkg infrastructure/storage
for f in parquet sqlite postgres redis; do file "infrastructure/storage/${f}.py"; done

# 4) econith_quant/ (refactored trading engine) extension packages ------------
# The vendored engine lives in econith_quant/ (see scripts/01_rename...).
# Here we add the ECONITH-specific extension layers from the master plan.
if [[ -d econith_quant ]]; then
  pkg econith_quant/execution
  for f in executor twap vwap chaser maker smart_order; do
    file "econith_quant/execution/${f}.py"
  done
  pkg econith_quant/bridge
  for f in ai_bridge strategy_bridge exchange_bridge api_bridge; do
    file "econith_quant/bridge/${f}.py"
  done
  pkg econith_quant/recovery
  for f in state checkpoint recovery; do file "econith_quant/recovery/${f}.py"; done
else
  echo "    (!) econith_quant/ not found yet -- run scripts/01_rename_to_econith_quant.sh first"
fi

# 5) dashboard/ (frontend lives here; scaffolded separately) ------------------
mkdir -p dashboard

# 6) shared local-storage helpers --------------------------------------------
for d in logs models checkpoints datasets/raw datasets/processed datasets/parquet datasets/features; do
  mkdir -p "$d"
done

# 7) app entrypoint -----------------------------------------------------------
file main.py

echo "==> Skeleton ready. Tree (depth 2):"
command -v tree >/dev/null 2>&1 && tree -L 2 -d || find . -maxdepth 2 -type d -not -path '*/.*'
