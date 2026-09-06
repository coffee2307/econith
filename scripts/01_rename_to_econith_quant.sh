#!/usr/bin/env bash
# =============================================================================
# ECONITH :: Phase 0 :: Step 1a -- Structural rename of the vendored repo folder
# -----------------------------------------------------------------------------
# Renames the cloned upstream trading engine directory:
#     econith/econith/  ->  econith/econith_quant/
#
# This ONLY renames the top-level folder. It does NOT touch source code yet.
# Run 02_refactor_strings.py afterwards for the code-level rebrand.
#
# Usage:
#     bash scripts/01_rename_to_econith_quant.sh
# =============================================================================
set -euo pipefail

# Resolve the repository root (parent of this scripts/ folder)
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

SRC="econith"
DST="econith_quant"

echo "==> ECONITH structural rename"
echo "    root : $ROOT_DIR"
echo "    from : $SRC/"
echo "    to   : $DST/"

if [[ ! -d "$SRC" ]]; then
  if [[ -d "$DST" ]]; then
    echo "==> '$DST/' already exists and '$SRC/' is gone. Nothing to do (idempotent)."
    exit 0
  fi
  echo "ERROR: source directory '$SRC/' not found." >&2
  exit 1
fi

if [[ -d "$DST" ]]; then
  echo "ERROR: destination '$DST/' already exists. Refusing to overwrite." >&2
  exit 1
fi

# Prefer a git-aware move so history is preserved when the folder is a repo.
if git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "==> '$SRC' is a git repo; renaming the working directory in place."
  mv "$SRC" "$DST"
else
  mv "$SRC" "$DST"
fi

echo "==> Done. Vendored engine is now at: $DST/"
echo "    Next: python scripts/02_refactor_strings.py --mode branding --apply"
