import os
from pathlib import Path
import pytest

from newsparser.scheduler.workspace import ensure_workspace


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))


def test_ensure_workspace_creates_per_category_dirs():
    root = ensure_workspace()
    assert (root / "input" / "tech").is_dir()
    assert (root / "input" / "markets").is_dir()
    assert (root / "cycles" / "tech").is_dir()
    assert (root / "cycles" / "markets").is_dir()


def test_ensure_workspace_creates_interest_templates():
    root = ensure_workspace()
    assert (root / "me" / "interests_tech.md").exists()
    assert (root / "me" / "interests_markets.md").exists()
    assert (root / "me" / "manifesto.md").exists()


def test_ensure_workspace_does_not_overwrite_existing_interests(tmp_path):
    root = ensure_workspace()
    custom = "# Custom tech profile\n\n## Themes\n"
    (root / "me" / "interests_tech.md").write_text(custom)
    ensure_workspace()  # second call must not overwrite
    assert (root / "me" / "interests_tech.md").read_text() == custom


def test_ensure_workspace_idempotent_on_repeated_calls():
    ensure_workspace()
    ensure_workspace()  # must not raise


def test_ensure_workspace_seeds_ignore_file(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    from newsparser.scheduler.workspace import ensure_workspace
    from newsparser.ignore import load_ignore

    root = ensure_workspace()
    ignore_path = root / "me" / "ignore.md"
    assert ignore_path.exists()
    # Seeded file is an empty (header-only) table.
    assert load_ignore(root).entries == []
    # Header row present so users/bot know the columns.
    assert "| 종류 | 대상 | 추가일 | 메모 |" in ignore_path.read_text()


def test_ensure_workspace_does_not_overwrite_existing_ignore(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    from newsparser.scheduler.workspace import ensure_workspace

    ensure_workspace()
    ignore_path = tmp_path / "workspace" / "me" / "ignore.md"
    ignore_path.write_text("CUSTOM CONTENT", encoding="utf-8")

    ensure_workspace()  # second call must not clobber
    assert ignore_path.read_text() == "CUSTOM CONTENT"
