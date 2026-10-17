"""StateRecorder must never silently write under a read-only host dataset bind."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from infrastructure.storage.recorder import StateRecorder
from infrastructure.storage.sqlite import SQLiteStore


def test_recorder_uses_explicit_durable_path(tmp_path, monkeypatch):
    from infrastructure.storage import recorder as recorder_module
    journal = tmp_path / "durable" / "state.sqlite"
    monkeypatch.setattr(
        recorder_module, "get_environment",
        lambda: SimpleNamespace(state_recorder_db_path=str(journal)),
    )
    recorder = StateRecorder(bus=object())
    try:
        assert journal.is_file()
        tables = set(row[0] for row in recorder._store.query(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ))
        assert {"market_ticks", "sentinel_state"} <= tables
    finally:
        recorder.close()
    with sqlite3.connect(journal) as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"


def test_explicit_store_does_not_read_global_environment(tmp_path, monkeypatch):
    from infrastructure.storage import recorder as recorder_module

    def forbidden():
        raise AssertionError("explicit injected store must not resolve environment")

    monkeypatch.setattr(recorder_module, "get_environment", forbidden)
    store = SQLiteStore(tmp_path / "injected.sqlite")
    recorder = StateRecorder(bus=object(), store=store)
    try:
        assert recorder._store is store
    finally:
        recorder.close()


def test_legacy_recorder_path_remains_backward_compatible():
    from config.environment import Environment
    from infrastructure.storage.sqlite import DEFAULT_PATH
    cfg = Environment(_env_file=None)
    assert cfg.state_recorder_db_path == str(DEFAULT_PATH)
