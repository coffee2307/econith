"""Regression tests for safe, WAL-aware SQLite snapshotting."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ci.sqlite_consistent_backup import backup_sqlite, main


def test_backup_contains_uncheckpointed_committed_wal_rows(tmp_path):
    source = tmp_path / "live.sqlite"
    destination = tmp_path / "immutable-backup.sqlite"
    writer = sqlite3.connect(source)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("CREATE TABLE ledger (id INTEGER PRIMARY KEY, marker TEXT)")
        writer.execute("INSERT INTO ledger VALUES (1, 'before-wal')")
        writer.commit()
        writer.execute("INSERT INTO ledger VALUES (2, 'committed-in-wal')")
        writer.commit()
        # The live writer connection stays open, with WAL sidecar present.
        assert (tmp_path / "live.sqlite-wal").exists()
        result = backup_sqlite(source, destination)
        assert result == destination and result.is_file()
        with sqlite3.connect(destination) as restored:
            assert restored.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            assert restored.execute("SELECT id, marker FROM ledger ORDER BY id").fetchall() == [
                (1, "before-wal"), (2, "committed-in-wal")
            ]
        # Never modify the source or its open writer connection.
        assert writer.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 2
    finally:
        writer.close()


def test_existing_backup_cannot_be_overwritten(tmp_path):
    original = tmp_path / "original.sqlite"
    backup = tmp_path / "previous.sqlite"
    with sqlite3.connect(original) as db:
        db.execute("CREATE TABLE test (id INT)")
        db.execute("INSERT INTO test VALUES (7)")
    backup.write_bytes(b"historic-backup-keep-me")
    with pytest.raises(FileExistsError):
        backup_sqlite(original, backup)
    assert backup.read_bytes() == b"historic-backup-keep-me"
    assert not list(tmp_path.glob(".econith-backup-*"))


def test_missing_source_and_nonexistent_destination_parent_refused(tmp_path):
    with pytest.raises(FileNotFoundError):
        backup_sqlite(tmp_path / "missing.sqlite", tmp_path / "backup.sqlite")
    source = tmp_path / "source.sqlite"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE test (id INT)")
    with pytest.raises(FileNotFoundError):
        backup_sqlite(source, tmp_path / "uncreated" / "backup.sqlite")


def test_invalid_input_never_publishes_backup(tmp_path):
    source = tmp_path / "not-a-db.sqlite"
    target = tmp_path / "backup.sqlite"
    source.write_text("this is not SQLite", encoding="utf-8")
    with pytest.raises(sqlite3.DatabaseError):
        backup_sqlite(source, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".econith-backup-*"))


def test_manual_cli_creates_backup_only_when_explicitly_invoked(tmp_path):
    source = tmp_path / "source.sqlite"
    dest = tmp_path / "backup.sqlite"
    with sqlite3.connect(source) as db:
        db.execute("CREATE TABLE test (id INT)")
        db.execute("INSERT INTO test VALUES (1)")
    assert main(["--source", str(source), "--destination", str(dest)]) == 0
    assert dest.exists()
    with pytest.raises(FileExistsError):
        main(["--source", str(source), "--destination", str(dest)])
