import os
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from newsparser.store.sqlite import init_db, insert_article, get_unprocessed
from newsparser.scheduler.cycle import run_cycle

@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("NEO4J_PASSWORD", "testpass")
    init_db()

SAMPLE_REPORT = """# Cycle 2026-05-05 00:00 KST

## New developments
- [importance: 0.80] **Test story.**

## Graph updates
### Entities
- NEW | Institution | Fed | aliases: [연준]

### Relations
- NEW | Fed --INFLUENCES[conf:0.80, impact:0.70]--> KOSPI | test

## Open threads
- None
"""

def test_run_cycle_builds_input_and_calls_claude(tmp_path):
    insert_article("g1", "Reuters", "Title", "https://x.com", "2026-05-05T00:00:00", "body")
    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_REPORT) as mock_claude, \
         patch("newsparser.scheduler.cycle.apply_graph_updates") as mock_graph:
        run_cycle("2026-05-05-00")
    mock_claude.assert_called_once()
    mock_graph.assert_called_once()

def test_run_cycle_writes_report_file(tmp_path):
    insert_article("g1", "Reuters", "Title", "https://x.com", "2026-05-05T00:00:00", "body")
    workspace = Path(os.environ["WORKSPACE_DIR"])
    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"):
        run_cycle("2026-05-05-00")
    report = workspace / "cycles" / "2026-05-05-00.md"
    assert report.exists()
    assert "Test story" in report.read_text()

def test_run_cycle_marks_articles_processed():
    insert_article("g1", "Reuters", "Title", "https://x.com", "2026-05-05T00:00:00", "body")
    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"):
        run_cycle("2026-05-05-00")
    assert get_unprocessed() == []
