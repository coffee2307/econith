"""Pack a small RunPod training bundle (code + labeled parquets only).

Does NOT include the 48GB tick lake. Typical size ~150–250 MB.

    python -m training.pack_runpod_bundle
    # -> dist/econith_runpod_train_bundle.zip
"""
from __future__ import annotations

import argparse
import zipfile
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# Paths relative to repo root that the H200 pod needs.
_DATA_FILES = (
    "datasets/processed/quant_labeled.parquet",
    "datasets/processed/quant_holdout.parquet",
    "datasets/processed/quant_labeled_hf.parquet",
    "datasets/processed/quant_holdout_hf.parquet",
)

_CODE_GLOBS = (
    "ai/**/*.py",
    "training/**/*.py",
    "infrastructure/**/*.py",
    "core/**/*.py",
    "requirements.txt",
    "requirements-train.txt",
    "requirements-ml.txt",
    "Makefile",
    "docs/RUNPOD_H200_TRAINING.md",
)


def _iter_code_files() -> list[Path]:
    out: list[Path] = []
    for pattern in _CODE_GLOBS:
        if "*" in pattern:
            out.extend(p for p in _ROOT.glob(pattern) if p.is_file())
        else:
            p = _ROOT / pattern
            if p.exists():
                out.append(p)
    # de-dupe, skip __pycache__ / tests noise inside training if any
    uniq = []
    seen = set()
    for p in sorted(out):
        if "__pycache__" in p.parts or p.suffix == ".pyc":
            continue
        rel = p.relative_to(_ROOT).as_posix()
        if rel in seen:
            continue
        seen.add(rel)
        uniq.append(p)
    return uniq


def pack(out_zip: Path) -> dict:
    out_zip.parent.mkdir(parents=True, exist_ok=True)
    missing = [r for r in _DATA_FILES if not (_ROOT / r).exists()]
    if missing:
        raise SystemExit(f"missing train artifacts: {missing}\nRun audit / label first.")

    files = _iter_code_files() + [_ROOT / r for r in _DATA_FILES]
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in files:
            zf.write(path, path.relative_to(_ROOT).as_posix())
        meta = (
            f"econith runpod train bundle\n"
            f"created_utc={datetime.now(timezone.utc).isoformat()}\n"
            f"files={len(files)}\n"
        )
        zf.writestr("BUNDLE_INFO.txt", meta)

    size_mb = out_zip.stat().st_size / 1e6
    print(f"wrote {out_zip} ({size_mb:.1f} MB, {len(files)} files)")
    return {"path": str(out_zip), "size_mb": size_mb, "files": len(files)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Pack RunPod training zip")
    p.add_argument(
        "--out",
        default=str(_ROOT / "dist" / "econith_runpod_train_bundle.zip"),
    )
    args = p.parse_args(argv)
    pack(Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
