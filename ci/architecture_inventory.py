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


def module_name_from_path(path: str) -> str:
    """Infer module name from an exact first-party Python source path."""
    if not path.endswith(".py"):
        raise ValueError(f"not Python source: {path}")
    name = path[:-3].replace("/", ".")
    if name.endswith(".__init__"):
        return name[: -len(".__init__")]
    return name


def resolve_import(declared: str, importer_path: str) -> str:
    """Resolve dot-relative AST imports without importing application code."""
    if not declared.startswith("."):
        return declared
    depth = len(declared) - len(declared.lstrip("."))
    package = module_name_from_path(importer_path)
    if not importer_path.endswith("/__init__.py"):
        package = package.rpartition(".")[0]
    levels = package.split(".") if package else []
    # A single leading dot refers to the current package.
    if depth > len(levels):
        return declared  # invalid/unresolvable candidate, for human review
    head = levels[: len(levels) - depth + 1]
    tail = declared[depth:]
    return ".".join(head + ([tail] if tail else []))


def logical_domain(path: str) -> str:
    """Classify physical owners without relocating or rewriting imports."""
    if path.startswith(("econith/world/", "ai/simulator_engine/", "ai/world_agents/")):
        return "world"
    if path.startswith(("econith/quant/", "quant/", "sentinel/", "training/quant/")):
        return "quant"
    if path.startswith(("econith/data/", "collectors/", "core/ingestion/",
                        "infrastructure/websocket/", "infrastructure/feature_store/")):
        return "data"
    if path.startswith(("ci/", "tests/")):
        return "ci"
    if path.startswith(("training/",)):
        return "training"
    if path.startswith(("api/", "bridges/", "core/", "config/")):
        return "core"
    if path.startswith(("ai/",)):
        return "ai"
    if path.startswith(("infrastructure/",)):
        return "infrastructure"
    return "other"


def dependency_map(records: list[dict]) -> dict:
    """Best-effort direct edges only; never claim complete runtime reachability."""
    modules = {module_name_from_path(r["path"]): r for r in records}
    edges: list[dict] = []
    excluded: list[dict] = []
    inbound: dict[str, list[str]] = defaultdict(list)
    for record in records:
        origin = record["path"]
        for declaration in record["imports"]:
            target = resolve_import(declaration, origin)
            if target == "econith_quant" or target.startswith("econith_quant."):
                excluded.append({"source": origin, "import": target,
                                 "reason": "isolated-third-party-or-legacy-subtree"})
            # Exact module edges only; 'from pkg import Foo' does not imply
            # that Foo is a submodule. Dynamic imports remain unknown.
            destination = modules.get(target)
            if destination and destination["path"] != origin:
                dest_path = destination["path"]
                edge = {
                    "source": origin, "destination": dest_path,
                    "source_domain": logical_domain(origin),
                    "destination_domain": logical_domain(dest_path),
                }
                edges.append(edge)
                inbound[dest_path].append(origin)
    edges.sort(key=lambda e: (e["source"], e["destination"]))
    boundary_edges = [e for e in edges
                      if e["source_domain"] != e["destination_domain"]]
    return {
        "direct_import_edges": edges,
        "cross_domain_edges": boundary_edges,
        "isolated_subtree_imports": sorted(
            excluded, key=lambda e: (e["source"], e["import"])
        ),
        "inbound_direct_imports": {
            k: sorted(v) for k, v in sorted(inbound.items())
        },
        "coverage_caveat": (
            "Only exact AST import names matched; imported symbols, "
            "dynamic imports, Docker, CLI and file/data references not resolved. "
            "Zero incoming edges DOES NOT certify a file is unused."
        ),
    }


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
    # This graph is advisory only. Never automatically delete an alleged
    # unused module: imported symbols and runtime entrypoints are incomplete.
    dependencies = dependency_map(records)
    domains: dict[str, list[str]] = defaultdict(list)
    for record in records:
        domains[logical_domain(record["path"])].append(record["path"])
    return {
        "kind": "STATIC_REVIEW_ONLY",
        "logical_domain_paths": {
            name: sorted(paths) for name, paths in sorted(domains.items())
        },
        "dependencies": dependencies,
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
        "direct_import_edges": len(inventory["dependencies"]["direct_import_edges"]),
        "cross_domain_edges": len(inventory["dependencies"]["cross_domain_edges"]),
        "isolated_subtree_imports": len(inventory["dependencies"]["isolated_subtree_imports"]),
        "no_files_modified": True,
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(json.dumps(overview, indent=2))
    return 1 if inventory["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
