"""Read-only inventory and guard against committing machine-generated artifacts.

Usage:
  python -m ci.repo_hygiene_check --root . --enforce
  python -m ci.repo_hygiene_check --root . --json-out /tmp/repo_audit.json

This script never deletes, edits, moves or untracks any file. Duplicates and
empty modules are REVIEW CANDIDATES, never deletion instructions.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Iterable


_GENERATED_PARTS = frozenset({
    "node_modules", "__pycache__", ".next", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".turbo", ".tox", "htmlcov"
})
_TRACKED_SECRET_BASENAMES = frozenset({
    ".env", ".env.production", ".env.local", ".env.staging",
    "id_rsa", "id_ed25519",
})


def forbidden_tracked_path(path: str) -> str | None:
    """Explain only definite generated output or credential file violations."""
    normalized = path.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if any(p in _GENERATED_PARTS for p in parts):
        return "tracked-generated-directory"
    filename = parts[-1]
    if filename in _TRACKED_SECRET_BASENAMES or (
        filename.startswith(".env.")
        and not filename.endswith((".example", ".sample", ".template", ".dist"))
    ):
        return "tracked-sensitive-local-file"
    if parts[-1].endswith((".pyc", ".pyo", ".tsbuildinfo")):
        return "tracked-compiled-artifact"
    return None


def classify_empty_file(path: str) -> str:
    """Only a review hint; Python namespace files can be empty by design."""
    name = path.replace("\\", "/").split("/")[-1]
    if name in ("__init__.py", ".gitkeep"):
        return "intentional-namespace-or-placeholder"
    return "manual-review-required"


def tracked_paths(root: Path) -> list[str]:
    outcome = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--cached"],
        check=True, capture_output=True
    )
    return sorted(p.decode("utf-8", "surrogateescape")
                  for p in outcome.stdout.split(b"\0") if p)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def summarize(root: Path, paths: Iterable[str]) -> dict:
    paths = sorted(set(paths))
    invalid: dict[str, str] = {}
    missing = []
    empty: dict[str, str] = {}
    buckets: dict[str, dict[str, int]] = {}
    hash_candidates: dict[int, list[Path]] = defaultdict(list)
    total_bytes = 0
    for relative in paths:
        reason = forbidden_tracked_path(relative)
        if reason:
            invalid[relative] = reason
        file = root / relative
        if not file.is_file():
            missing.append(relative)
            continue
        size = file.stat().st_size
        total_bytes += size
        domain = relative.split("/")[0]
        bucket = buckets.setdefault(domain, {"tracked_files": 0, "bytes": 0})
        bucket["tracked_files"] += 1
        bucket["bytes"] += size
        if size == 0:
            empty[relative] = classify_empty_file(relative)
        elif size >= 64:
            hash_candidates[size].append(file)
    duplicates = []
    # Match sizes first to avoid hashing every file unnecessarily.
    for size, candidates in hash_candidates.items():
        if len(candidates) <= 1:
            continue
        by_digest: dict[str, list[str]] = defaultdict(list)
        for path in candidates:
            by_digest[sha256_file(path)].append(
                path.relative_to(root).as_posix()
            )
        for digest, matches in by_digest.items():
            if len(matches) > 1:
                duplicates.append({
                    "sha256": digest, "bytes_each": size, "paths": sorted(matches),
                    "action": "REVIEW_ONLY_NO_AUTO_DELETION"
                })
    return {
        "tracked_files": len(paths),
        "total_tracked_bytes": total_bytes,
        "forbidden_tracked_paths": invalid,
        "missing_tracked_files_in_checkout": missing,
        "empty_files_review": empty,
        "exact_duplicate_candidates": sorted(duplicates, key=lambda v: (-v["bytes_each"], v["paths"][0])),
        "root_domains": dict(sorted(buckets.items())),
        "deletions_performed": 0,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--enforce", action="store_true",
                        help="exit 1 if definitely forbidden artifacts are tracked")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    result = summarize(root, tracked_paths(root))
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({
        "tracked_files": result["tracked_files"],
        "tracked_bytes": result["total_tracked_bytes"],
        "forbidden": len(result["forbidden_tracked_paths"]),
        "exact_duplicate_candidate_groups": len(result["exact_duplicate_candidates"]),
        "empty_review_candidates": len(result["empty_files_review"]),
        "deletions_performed": 0,
    }, indent=2))
    return int(bool(args.enforce and result["forbidden_tracked_paths"]))


if __name__ == "__main__":
    raise SystemExit(main())
