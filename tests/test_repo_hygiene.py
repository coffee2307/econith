"""Repository hygiene invariants: explicitly preserve intentional placeholders."""
from __future__ import annotations

from pathlib import Path

from ci.repo_hygiene_check import (
    classify_empty_file, forbidden_tracked_path, summarize,
)


def test_forbidden_generated_and_local_credential_paths() -> None:
    assert forbidden_tracked_path("node_modules/rxjs/index.js")
    assert forbidden_tracked_path("dashboard/node_modules/next/package.json")
    assert forbidden_tracked_path("dashboard/.next/server/app.js")
    assert forbidden_tracked_path("core/__pycache__/engine.cpython-312.pyc")
    assert forbidden_tracked_path(".env.production")
    assert forbidden_tracked_path("secret/.env.local")
    assert forbidden_tracked_path("ai/cache.pt") is None
    assert forbidden_tracked_path("econith_quant/LICENSE") is None
    assert forbidden_tracked_path("KHKT_Evaluation/final_report/figures.png") is None


def test_namespace_and_existing_evidence_must_not_auto_delete() -> None:
    assert classify_empty_file("module/__init__.py") == "intentional-namespace-or-placeholder"
    assert classify_empty_file("data/.gitkeep") == "intentional-namespace-or-placeholder"
    assert classify_empty_file("execution/executor.py") == "manual-review-required"


def test_report_is_read_only_and_finds_identical_content(tmp_path: Path) -> None:
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "one.txt").write_text("x" * 70, encoding="utf-8")
    (tmp_path / "b" / "two.txt").write_text("x" * 70, encoding="utf-8")
    (tmp_path / "b" / "__init__.py").touch()
    result = summarize(tmp_path, ["a/one.txt", "b/two.txt", "b/__init__.py"])
    assert result["tracked_files"] == 3
    assert result["deletions_performed"] == 0
    assert len(result["exact_duplicate_candidates"]) == 1
    assert result["empty_files_review"]["b/__init__.py"] == "intentional-namespace-or-placeholder"
    assert (tmp_path / "a" / "one.txt").read_text() == "x" * 70


def test_extra_missing_file_does_not_disappear_silently(tmp_path: Path) -> None:
    report = summarize(tmp_path, ["deleted/file.py"])
    assert report["missing_tracked_files_in_checkout"] == ["deleted/file.py"]
