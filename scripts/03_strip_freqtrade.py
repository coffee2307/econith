#!/usr/bin/env python3
"""Strip all freqtrade branding; rename package paths to econith."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {
    ".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", "node_modules", ".venv", "venv", "env", ".idea", ".vscode",
    "build", "dist", ".eggs", "datasets", "models",
}

SKIP_FILES = {"poetry.lock", "package-lock.json", "yarn.lock", "03_strip_freqtrade.py"}

TEXT_SUFFIXES = {
    ".py", ".pyi", ".txt", ".md", ".rst", ".cfg", ".ini", ".toml", ".yml",
    ".yaml", ".json", ".sh", ".ps1", ".env", ".example", ".service", ".in",
    ".dockerfile", ".html", ".js", ".ts", ".css", ".sql", ".svg", ".j2",
    ".ipynb", ".watchdog", ".coveragerc",
}

# Longest / most specific replacements first.
REPLACEMENTS: list[tuple[str, str]] = [
    ("freqtrade@protonmail.com", "coffee2307.pham@gmail.com"),
    ("154552126+freqtrade-bot@users.noreply.github.com", "coffee2307.pham@gmail.com"),
    ("freqtrade-bot@users.noreply.github.com", "coffee2307.pham@gmail.com"),
    ("ECONITH QuantBot", "EconithBot"),
    ("freqtrade-bot", "econith-bot"),
    ("freqtradeorg/freqtrade", "econith/econith-quant"),
    ("github.com/freqtrade/freqtrade", "github.com/econith/econith"),
    ("github.com/freqtrade", "github.com/econith"),
    ("www.freqtrade.io", "econith"),
    ("freqtrade.io", "econith"),
    ("frequi.freqtrade.io", "econith"),
    ("hub.docker.com/r/freqtradeorg/freqtrade", "econith"),
    ("freqtrade_poweredby.svg", "econith_poweredby.svg"),
    ("freqtrade-client.md", "econith-client.md"),
    ("freqtrade_client_version_align.py", "econith_client_version_align.py"),
    ("test_freqtradebot.py", "test_econithbot.py"),
    ("freqtrade.service.watchdog", "econith.service.watchdog"),
    ("freqtrade.service", "econith.service"),
    ("freqtrade_client", "econith_client"),
    ("freqtrade-client", "econith-client"),
    ("FreqtradeBot", "EconithBot"),
    ("freqtradebot", "econithbot"),
    ("FREQTRADE", "ECONITH"),
    ("FreqTrade", "Econith"),
    ("Freqtrade", "Econith"),
    (".freqtrade", ".econith"),
    ("freqtrade", "econith"),
]

# Path renames: (old relative to ROOT, new relative to ROOT) — deepest paths first.
PATH_RENAMES: list[tuple[str, str]] = [
    ("econith_quant/ft_client/freqtrade_client", "econith_quant/ft_client/econith_client"),
    ("econith_quant/tests/freqtradebot/test_freqtradebot.py",
     "econith_quant/tests/freqtradebot/test_econithbot.py"),
    ("econith_quant/tests/freqtradebot", "econith_quant/tests/econithbot"),
    ("econith_quant/freqtrade/freqtradebot.py", "econith_quant/freqtrade/econithbot.py"),
    ("econith_quant/build_helpers/freqtrade_client_version_align.py",
     "econith_quant/build_helpers/econith_client_version_align.py"),
    ("econith_quant/docs/commands/freqtrade-client.md", "econith_quant/docs/commands/econith-client.md"),
    ("econith_quant/docs/assets/freqtrade_poweredby.svg", "econith_quant/docs/assets/econith_poweredby.svg"),
    ("econith_quant/freqtrade.service.watchdog", "econith_quant/econith.service.watchdog"),
    ("econith_quant/freqtrade.service", "econith_quant/econith.service"),
    ("econith_quant/freqtrade", "econith_quant/econith"),
    ("econith_quant/ft_client", "econith_quant/econith_client"),
]


def iter_text_files(root: Path):
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.name in SKIP_FILES:
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {
            "Dockerfile", ".gitignore", ".dockerignore", "Makefile",
        }:
            continue
        yield path


def transform(text: str) -> str:
    out = text
    for old, new in REPLACEMENTS:
        out = out.replace(old, new)
    return out


def apply_path_renames(root: Path, dry_run: bool) -> None:
    for old_rel, new_rel in PATH_RENAMES:
        old = root / old_rel
        new = root / new_rel
        if not old.exists():
            continue
        print(f"  rename: {old_rel} -> {new_rel}")
        if not dry_run:
            new.parent.mkdir(parents=True, exist_ok=True)
            old.rename(new)


def apply_text_rewrites(root: Path, dry_run: bool) -> tuple[int, int]:
    files = 0
    lines = 0
    for path in iter_text_files(root):
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, PermissionError, OSError):
            continue
        updated = transform(original)
        if updated == original:
            continue
        hits = sum(1 for a, b in zip(original.splitlines(), updated.splitlines()) if a != b)
        files += 1
        lines += hits
        rel = path.relative_to(root)
        print(f"  ~ {rel} ({hits} line(s))")
        if not dry_run:
            path.write_text(updated, encoding="utf-8")
    return files, lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--root", default=str(ROOT))
    args = parser.parse_args()
    root = Path(args.root).resolve()
    dry = not args.apply

    print(f"==> strip freqtrade  root={root}  apply={args.apply}")
    print("-- path renames --")
    apply_path_renames(root, dry)
    print("-- text rewrites --")
    files, lines = apply_text_rewrites(root, dry)
    print(f"==> done: {files} files, {lines} line changes")
    if dry:
        print("DRY RUN — re-run with --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
