"""Create a verified, no-overwrite SQLite backup from an existing journal.

Usage (manual, OFFLINE/authorized only):
    python -m ci.sqlite_consistent_backup --source old.sqlite --destination backups/old.sqlite

Use SQLite's online Backup API rather than cp/copy2: in WAL mode the latest
committed transactions may still live in -wal, and raw copying only the main
.sqlite file can produce an inconsistent or outdated snapshot.

This copies ONE journal at a time; it does not provide cross-database atomicity,
back up PostgreSQL, freeze in-flight orders, or migrate production state.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import tempfile
from pathlib import Path


def backup_sqlite(source: Path | str, destination: Path | str) -> Path:
    """Copy committed SQLite state and publish only an integrity-checked backup.

    Source is strictly read-only. Destination is never overwritten, including
    if another process creates it during backup. A temporary file in the
    destination directory keeps the publish operation on one filesystem.
    """
    src = Path(source).expanduser().resolve(strict=True)
    dest = Path(destination).expanduser().absolute()
    if not src.is_file():
        raise ValueError("source must be a regular SQLite database file")
    if dest.exists() or dest.is_symlink():
        raise FileExistsError(f"backup destination already exists: {dest}")
    if not dest.parent.is_dir():
        raise FileNotFoundError(f"backup directory does not exist: {dest.parent}")
    if src == dest:
        raise ValueError("source and destination must differ")

    tmp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".econith-backup-", suffix=".sqlite", dir=dest.parent,
            delete=False,
        ) as temporary:
            tmp_name = temporary.name

        # pathlib.as_uri encodes spaces/unicode safely; mode=ro prevents writes
        # to a live source even if it has WAL state waiting to be checkpointed.
        with sqlite3.connect(src.as_uri() + "?mode=ro", uri=True, timeout=15) as original:
            with sqlite3.connect(tmp_name, timeout=15) as backup:
                original.backup(backup, pages=64, sleep=0.05)
                status = backup.execute("PRAGMA quick_check").fetchone()
                if not status or status[0] != "ok":
                    raise sqlite3.DatabaseError(f"backup quick_check failed: {status}")

        with open(tmp_name, "rb") as created:
            os.fsync(created.fileno())
        # An atomic same-directory hardlink refuses to replace an existing
        # backup, even under concurrent invocations. Clean the temporary.
        os.link(tmp_name, dest)
        return dest
    finally:
        if tmp_name is not None:
            try:
                Path(tmp_name).unlink(missing_ok=True)
            except OSError:
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    output = backup_sqlite(args.source, args.destination)
    print(f"SQLite snapshot verified: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
