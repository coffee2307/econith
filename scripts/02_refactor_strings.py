#!/usr/bin/env python3
# =============================================================================
# ECONITH :: Phase 0 :: Step 1b -- Source-level Find & Replace ("freqtrade" -> ECONITH)
# -----------------------------------------------------------------------------
# Cross-platform (Windows / Linux / macOS) refactor utility.
#
# It walks the vendored engine directory (default: ./econith_quant) and rewrites
# occurrences of "freqtrade" according to a chosen mode:
#
#   --mode branding   (RECOMMENDED, SAFE)
#       Replaces ONLY user-facing brand strings:
#           "Freqtrade" / "FreqTrade" / "FREQTRADE"  -> "ECONITH Quant"
#       Leaves the python import path / package identifier `freqtrade` UNTOUCHED,
#       so `pip install -e .` and `from freqtrade... import ...` keep working and
#       you can still pull upstream security patches.
#
#   --mode full       (AGGRESSIVE, BREAKS UPSTREAM COMPATIBILITY)
#       Replaces every lowercase `freqtrade` token -> `econith_quant` AND the
#       brand strings above. This rewrites import paths and (optionally) renames
#       the inner python package directory. After this you can no longer cleanly
#       merge upstream freqtrade updates. Use only if you intend to hard-fork.
#
# Safety features:
#   * Dry-run by default. Nothing is written unless you pass --apply.
#   * A timestamped backup manifest is printed; use --backup to copy files first.
#   * Binary files, .git/, virtualenvs, caches and lockfiles are skipped.
#   * Idempotent: re-running on already-migrated source is a no-op.
#
# Usage:
#   python scripts/02_refactor_strings.py --mode branding            # preview
#   python scripts/02_refactor_strings.py --mode branding --apply    # write
#   python scripts/02_refactor_strings.py --mode full --apply --rename-package
# =============================================================================
from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Directories we never descend into.
SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "node_modules", ".venv", "venv", "env", ".idea", ".vscode",
    "build", "dist", ".eggs",
}

# Only rewrite recognised text/source files.
TEXT_SUFFIXES = {
    ".py", ".pyi", ".txt", ".md", ".rst", ".cfg", ".ini", ".toml", ".yml",
    ".yaml", ".json", ".sh", ".ps1", ".env", ".example", ".service", ".in",
    ".dockerfile", ".html", ".js", ".ts", ".css", ".sql",
}

# Never rewrite these specific filenames.
SKIP_FILES = {"poetry.lock", "package-lock.json", "yarn.lock"}

# Brand-string replacements applied in BOTH modes (order matters).
BRAND_RULES = [
    (re.compile(r"FREQTRADE"), "ECONITH_QUANT"),
    (re.compile(r"FreqTrade"), "ECONITH Quant"),
    (re.compile(r"Freqtrade"), "ECONITH Quant"),
]

# Lowercase identifier replacement, applied only in FULL mode.
# \b word boundaries keep us from touching things like "myfreqtraderc".
IDENT_RULE = (re.compile(r"\bfreqtrade\b"), "econith_quant")


def iter_text_files(root: Path):
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {
            "Dockerfile", ".gitignore", ".dockerignore",
        }:
            continue
        yield path


def transform(text: str, mode: str) -> str:
    out = text
    if mode == "full":
        out = IDENT_RULE[0].sub(IDENT_RULE[1], out)
    for pattern, repl in BRAND_RULES:
        out = pattern.sub(repl, out)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="ECONITH freqtrade refactor tool")
    parser.add_argument("--target", default="econith_quant",
                        help="Directory to refactor (default: econith_quant)")
    parser.add_argument("--mode", choices=["branding", "full"], default="branding")
    parser.add_argument("--apply", action="store_true",
                        help="Actually write changes (default is dry-run preview)")
    parser.add_argument("--backup", action="store_true",
                        help="Copy each modified file to <file>.bak before writing")
    parser.add_argument("--rename-package", action="store_true",
                        help="[full only] rename inner freqtrade/ package dir -> econith_quant/")
    args = parser.parse_args()

    target = Path(args.target).resolve()
    if not target.exists():
        print(f"ERROR: target '{target}' does not exist.", file=sys.stderr)
        return 1

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"==> ECONITH refactor  [{stamp}]")
    print(f"    target : {target}")
    print(f"    mode   : {args.mode}")
    print(f"    apply  : {args.apply}")
    print("-" * 70)

    changed_files = 0
    changed_hits = 0

    for path in iter_text_files(target):
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError):
            continue  # binary or locked -> skip

        updated = transform(original, args.mode)
        if updated == original:
            continue

        hits = sum(1 for a, b in zip(original.splitlines(), updated.splitlines()) if a != b)
        changed_files += 1
        changed_hits += hits
        rel = path.relative_to(target)
        print(f"  ~ {rel}  ({hits} line(s))")

        if args.apply:
            if args.backup:
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
            path.write_text(updated, encoding="utf-8")

    # Optional inner-package directory rename (FULL mode only).
    if args.mode == "full" and args.rename_package:
        inner = target / "freqtrade"
        if inner.is_dir():
            dst = target / "econith_quant_pkg"  # avoids clash with the outer folder name
            print("-" * 70)
            print(f"  [package] {inner.name}/ -> {dst.name}/")
            if args.apply:
                inner.rename(dst)

    print("-" * 70)
    print(f"==> files touched: {changed_files}   line-changes: {changed_hits}")
    if not args.apply:
        print("==> DRY RUN. Re-run with --apply to write changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
