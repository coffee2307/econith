#!/usr/bin/env bash
#
# ECONITH :: scripts/setup_vendors.sh
#
# Lean vendor puller. For each vendor in vendors/manifest.json it performs a
# blob-less, sparse, pinned checkout of ONLY the core-logic subtrees (never the
# full repo). Upstream source lands in vendors/<name>/ and stays untracked.
#
#   bash scripts/setup_vendors.sh                # pull every P0 vendor
#   bash scripts/setup_vendors.sh openbb qlib    # pull a subset by name
#   STRICT=1 bash scripts/setup_vendors.sh       # fail if any commit is unpinned
#
# Requires: git >= 2.27 (sparse-checkout cone mode), python3 (manifest parsing).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENDORS="$ROOT/vendors"
MANIFEST="$VENDORS/manifest.json"
STRICT="${STRICT:-0}"

log()  { printf '\033[36m[vendors]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[vendors] WARN:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[vendors] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

command -v git >/dev/null 2>&1 || die "git is required"
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  die "python3 (or python) is required to parse the manifest"
fi
[ -f "$MANIFEST" ] || die "manifest not found: $MANIFEST"

# Emit one tab-separated record per vendor: name<TAB>repo<TAB>commit<TAB>paths(space-joined)
read_manifest() {
  "$PY" - "$MANIFEST" <<'PYEOF'
import json, sys
with open(sys.argv[1], "r", encoding="utf-8") as fh:
    data = json.load(fh)
for v in data.get("vendors", []):
    name = v["name"]
    repo = v["repo"]
    commit = v.get("commit", "")
    paths = " ".join(v.get("sparse_paths", []))
    print(f"{name}\t{repo}\t{commit}\t{paths}")
PYEOF
}

# Optional name filter from argv.
declare -A WANT=()
for a in "$@"; do WANT["$a"]=1; done
filter_on=$(( $# > 0 ? 1 : 0 ))

pull_vendor() {
  local name="$1" repo="$2" commit="$3" paths="$4"
  local dest="$VENDORS/$name"

  if [ -z "$commit" ] || [ "$commit" = "REPLACE_WITH_PINNED_SHA" ]; then
    if [ "$STRICT" = "1" ]; then
      die "$name: commit is not pinned (set a real SHA in manifest.json)"
    fi
    warn "$name: commit not pinned — skipping. Pin a SHA in manifest.json, then re-run."
    return 0
  fi

  log "== $name =="
  if [ -d "$dest/.git" ]; then
    log "$name: repo present, syncing to pinned commit"
  else
    rm -rf "$dest"
    mkdir -p "$dest"
    git -C "$dest" init -q
    git -C "$dest" remote add origin "$repo"
    # Cone-mode sparse checkout keeps the working tree to the listed subtrees only.
    git -C "$dest" config core.sparseCheckout true
    git -C "$dest" sparse-checkout init --cone
  fi

  # (Re)apply sparse paths on every run so manifest edits take effect.
  if [ -n "$paths" ]; then
    # shellcheck disable=SC2086
    git -C "$dest" sparse-checkout set $paths
  fi

  # Blob-less partial fetch of just the pinned commit → minimal bytes on the wire.
  git -C "$dest" fetch --depth 1 --filter=blob:none origin "$commit" \
    || die "$name: failed to fetch commit $commit from $repo"
  git -C "$dest" checkout -q FETCH_HEAD

  local got
  got="$(git -C "$dest" rev-parse HEAD)"
  printf '%s\n' "$got" > "$dest/VENDOR_SHA.txt"
  if [ "$got" != "$commit" ]; then
    warn "$name: checked-out SHA ($got) != manifest commit ($commit)"
  fi
  log "$name: ready @ $got"
  log "$name: sparse tree ->"
  git -C "$dest" sparse-checkout list | sed 's/^/           /'
}

log "root:     $ROOT"
log "manifest: $MANIFEST"
[ "$filter_on" = "1" ] && log "filter:   $*"

count=0
while IFS=$'\t' read -r name repo commit paths; do
  [ -z "${name:-}" ] && continue
  if [ "$filter_on" = "1" ] && [ -z "${WANT[$name]:-}" ]; then
    continue
  fi
  pull_vendor "$name" "$repo" "$commit" "$paths"
  count=$((count + 1))
done < <(read_manifest)

log "done. processed $count vendor(s)."
log "next: python scripts/verify_invariants.py"
