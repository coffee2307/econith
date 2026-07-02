"""ECONITH :: training.deploy  (PHASE E -- The Customs Gate)

Only let verified, untampered models onto the production trading floor.

Economic analogy
----------------
Freshly trained models arrive from the H200 factory like shipping containers.
Before any of them touch real money, they pass through customs. The inspector
checks each container's **security seal** (a SHA256 checksum recorded in the
manifest at build time) against the container in hand. If a single byte changed
in transit -- corruption, a half-finished upload, tampering -- the seal won't
match and the shipment is rejected. Nothing unverified reaches the floor.

Once everything clears, the gate updates the **"now serving" board**
(``active.yaml``) that production reads to know which models are live, and files
the previous board in the archive (``registry/history/``) so you can roll back to
yesterday's line-up in one command if the new models misbehave.

This is the safety valve that lets you experiment fearlessly: promotion is atomic
and reversible.

Run it:
    python training/deploy.py --registry ./models/registry/manifest.yaml \
        --target ./models --activate
    python training/deploy.py --registry ./models/registry/manifest.yaml --verify-only
    python training/deploy.py --target ./models --rollback
"""
from __future__ import annotations

import argparse
import hashlib
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)
logger = logging.getLogger("econith.training.deploy")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_yaml(path: Path) -> dict:
    import yaml

    if not path.exists():
        raise SystemExit(f"file not found: {path}")
    return yaml.safe_load(path.read_text()) or {}


def verify_registry(manifest_path: str, target: str = "./models") -> dict:
    """Check every model's security seal (SHA256) against the manifest.

    Returns a report mapping model name -> True/False. Raises if the manifest is
    structurally broken. A model is 'sealed' only when the file exists AND its
    fingerprint matches exactly what was recorded when it was trained.
    """
    manifest = _load_yaml(Path(manifest_path))
    models = manifest.get("models", {})
    if not models:
        raise SystemExit(f"manifest has no models: {manifest_path}")

    root = Path(target)
    report: dict[str, bool] = {}
    for name, entry in models.items():
        rel = entry.get("path")
        expected = entry.get("sha256")
        if not rel or not expected:
            logger.error("[%s] manifest entry missing path/sha256", name)
            report[name] = False
            continue
        fpath = root / rel
        if not fpath.exists():
            logger.error("[%s] missing file: %s", name, fpath)
            report[name] = False
            continue
        actual = _sha256(fpath)
        ok = actual == expected
        report[name] = ok
        logger.info("[%s] %s  (%s)", name, "VERIFIED" if ok else "SEAL MISMATCH", rel)
        if not ok:
            logger.error("    expected %s", expected)
            logger.error("    actual   %s", actual)
    return report


def activate(manifest_path: str, target: str) -> Path:
    """Promote verified models to live and archive the outgoing line-up.

    Steps (all-or-nothing):
      1. Verify every seal -- refuse to promote if ANY model fails.
      2. Archive the current active.yaml into registry/history/<timestamp>/.
      3. Write the new active.yaml the production backend reads on startup.
    """
    report = verify_registry(manifest_path, target)
    if not all(report.values()):
        failed = [n for n, ok in report.items() if not ok]
        raise SystemExit(f"deployment refused -- unverified models: {', '.join(failed)}")

    import yaml

    root = Path(target)
    registry = root / "registry"
    history = registry / "history"
    history.mkdir(parents=True, exist_ok=True)

    manifest = _load_yaml(Path(manifest_path))
    active_path = registry / "active.yaml"

    # 2) Archive the outgoing board so we can roll back to it later.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if active_path.exists():
        snap_dir = history / stamp
        snap_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(active_path, snap_dir / "active.yaml")
        logger.info("archived previous active.yaml -> %s", snap_dir / "active.yaml")

    # 3) Write the new "now serving" board.
    active = {
        "version": manifest.get("version", stamp),
        "activated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "manifest": str(Path(manifest_path).as_posix()),
        "models": {
            name: {"path": entry.get("path"), "sha256": entry.get("sha256")}
            for name, entry in manifest.get("models", {}).items()
        },
    }
    active_path.write_text(yaml.safe_dump(active, sort_keys=False))
    logger.info("ACTIVATED %d model(s) -> %s (version %s)",
                len(active["models"]), active_path, active["version"])
    return active_path


def rollback(target: str) -> Path:
    """Restore the most recently archived active.yaml (undo the last deploy)."""
    import yaml  # noqa: F401  (kept for symmetry / future use)

    registry = Path(target) / "registry"
    history = registry / "history"
    snaps = sorted([d for d in history.glob("*") if (d / "active.yaml").exists()])
    if not snaps:
        raise SystemExit("no history snapshots to roll back to")
    latest = snaps[-1]
    dst = registry / "active.yaml"
    shutil.copy2(latest / "active.yaml", dst)
    logger.info("ROLLED BACK to %s -> %s", latest.name, dst)
    return dst


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="deploy.py", description="ECONITH model customs gate")
    p.add_argument("--registry", default="./models/registry/manifest.yaml",
                   help="manifest to verify / activate")
    p.add_argument("--target", default="./models", help="model root directory")
    p.add_argument("--activate", action="store_true", help="verify then promote to live")
    p.add_argument("--verify-only", action="store_true", help="only check checksums")
    p.add_argument("--rollback", action="store_true", help="restore the previous active.yaml")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.rollback:
        rollback(args.target)
        return 0

    if args.verify_only:
        report = verify_registry(args.registry, args.target)
        ok = all(report.values())
        logger.info("verification %s (%d/%d sealed)",
                    "PASSED" if ok else "FAILED",
                    sum(report.values()), len(report))
        return 0 if ok else 1

    if args.activate:
        activate(args.registry, args.target)
        return 0

    # Default action: verify (safe, read-only) and tell the user how to promote.
    report = verify_registry(args.registry, args.target)
    if all(report.values()):
        logger.info("all models verified -- re-run with --activate to go live")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
