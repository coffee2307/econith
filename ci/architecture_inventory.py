"""Static reverse-import and event-authority inventory, never imports runtime code.

Supports Phase 0 extraction decisions: prints which modules depend on which
owner and where order intents/fills appear as string literals. This is
**not** a security proof; dynamic imports and constructed topics may be missed.
"""
from __future__ import annotations

import argparse
import ast
import json
from collections import defaultdict
from pathlib import Path

ROOTS = (
    "ai", "api", "bridges", "ci", "collectors", "config", "core", "econith",
    "infrastructure", "quant", "scripts", "sentinel", "training",
)
EVENT_LITERALS = (
    "order.intent", "quant.fill", "order.rejected", "order.update",
    "sentinel.status", "execution.status", "world.", "core.macro.context",
)


def scan_python_source(source: str, *, path: str) -> dict:
    """AST-only; class/import/call info, without executing untrusted modules."""
    tree = ast.parse(source, filename=path)
    imports: set[str] = set()
    classes: list[str] = []
    event_hits: list[dict] = []
    order_api_calls: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            # Relative imports resolve against the package in later analysis.
            imports.add("." * node.level + node.module)
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if any(value == e or (e.endswith(".") and value.startswith(e))
                   for e in EVENT_LITERALS):
                event_hits.append({"literal": value, "line": node.lineno})
        elif isinstance(node, ast.Call):
            target = node.func
            name = target.attr if isinstance(target, ast.Attribute) else (
                target.id if isinstance(target, ast.Name) else "")
            if name in ("create_order", "create_market_order",
                        "create_limit_order", "submit_order"):
                order_api_calls.append({"name": name, "line": node.lineno})
    return {
        "path": path,
        "imports": sorted(imports),
        "classes": sorted(classes),
        "event_literals": sorted(event_hits, key=lambda v: v["line"]),
        "potential_order_api_calls": sorted(order_api_calls, key=lambda v: v["line"]),
    }


def find_source_files(root: Path) -> list[Path]:
    files = []
    if (root / "main.py").is_file():
        files.append(root / "main.py")
    for folder in ROOTS:
        location = root / folder
        if location.exists():
            files += [
                p for p in location.rglob("*.py")
                if not any(part in ("node_modules", "__pycache__", ".venv")
                           for part in p.relative_to(root).parts)
            ]
    # Deliberately exclude independent GPLv3 subtree and historical
    # KHKT experiment artifacts; scan those separately after license review.
    return sorted(set(files))


def build_inventory(root: Path) -> dict:
    records = []
    failures = []
    for file in find_source_files(root):
        path = file.relative_to(root).as_posix()
        try:
            records.append(scan_python_source(file.read_text(encoding="utf-8-sig"),
                                              path=path))
        except (UnicodeError, SyntaxError, OSError) as exc:
            failures.append({"path": path, "error": type(exc).__name__,
                             "detail": str(exc)})
    providers = defaultdict(list)
    for record in records:
        path = record["path"]
        for event in record["event_literals"]:
            providers[event["literal"]].append({"path": path, "line": event["line"]})
    # Import declarations are only signals for planning, NOT an exact runtime
    # dependency graph: importlib and module monkeypatching are dynamic.
    return {
        "kind": "STATIC_REVIEW_ONLY",
        "first_party_files_scanned": len(records),
        "errors": failures,
        "modules": records,
        "event_literal_occurrences": dict(sorted(providers.items())),
        "files_with_order_api_calls": [
            {"path": x["path"], "calls": x["potential_order_api_calls"]}
            for x in records if x["potential_order_api_calls"]
        ],
        "excluded_roots": ["econith_quant", "KHKT_Evaluation",
                           "dashboard", "archive"],
        "no_files_modified": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    inventory = build_inventory(args.root.resolve())
    overview = {
        "scanned": inventory["first_party_files_scanned"],
        "errors": len(inventory["errors"]),
        "literal_order_intent_sites": len(
            inventory["event_literal_occurrences"].get("order.intent", [])
        ),
        "potential_order_api_paths": len(inventory["files_with_order_api_calls"]),
        "no_files_modified": True,
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(json.dumps(overview, indent=2))
    return 1 if inventory["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
