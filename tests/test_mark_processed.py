# tests/test_mark_processed.py
import os
import pytest
from pathlib import Path

from newsparser.store.sqlite import insert_article, get_unprocessed
import newsparser.scripts.mark_processed as script


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    (tmp_path / "workspace" / "input" / "tech").mkdir(parents=True)


def _guids_path() -> Path:
    ws = Path(os.environ["WORKSPACE_DIR"])
    return ws / "input" / "tech" / "2026-05-08-12-guids.txt"


def test_mark_processed_marks_db_rows():
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")
    insert_article("g2", "src", "t2", "u2", None, "body", category="tech")

    _guids_path().write_text("g1\ng2\n")

    script.main(["mark_processed.py", "tech", "2026-05-08-12"])

    assert get_unprocessed(category="tech") == []


def test_mark_processed_deletes_guids_file():
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")
    _guids_path().write_text("g1\n")

    script.main(["mark_processed.py", "tech", "2026-05-08-12"])

    assert not _guids_path().exists()


def test_mark_processed_exits_1_if_guids_missing():
    with pytest.raises(SystemExit) as exc:
        script.main(["mark_processed.py", "tech", "9999-99-99-99"])
    assert exc.value.code == 1


def test_mark_processed_exits_1_on_wrong_args():
    with pytest.raises(SystemExit) as exc:
        script.main(["mark_processed.py", "tech"])
    assert exc.value.code == 1
