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

SAMPLE_DIGEST = """사이클 2026-05-05 00:00 KST

새 소식
• (중요도 0.80) Fed가 50bp 깜짝 인하. KOSPI 영향 주목.

오픈 스레드
• 없음"""

SAMPLE_REPORT = SAMPLE_DIGEST + """

## Graph updates
### Entities
- NEW | Institution | Fed | aliases: [연준]

### Relations
- NEW | Fed --INFLUENCES[conf:0.80, impact:0.70]--> KOSPI | test
"""

def test_run_cycle_builds_input_and_calls_claude(tmp_path):
    insert_article("g1", "Reuters", "Title", "https://x.com", "2026-05-05T00:00:00", "body")
    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_REPORT) as mock_claude, \
         patch("newsparser.scheduler.cycle.apply_graph_updates") as mock_graph, \
         patch("newsparser.scheduler.cycle.send_long_message") as mock_send:
        run_cycle("2026-05-05-00")
    mock_claude.assert_called_once()
    mock_graph.assert_called_once()
    mock_send.assert_called_once_with(SAMPLE_DIGEST)

def test_run_cycle_writes_report_file(tmp_path):
    insert_article("g1", "Reuters", "Title", "https://x.com", "2026-05-05T00:00:00", "body")
    workspace = Path(os.environ["WORKSPACE_DIR"])
    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message"):
        run_cycle("2026-05-05-00")
    report = workspace / "cycles" / "2026-05-05-00.md"
    assert report.exists()
    text = report.read_text()
    assert "Fed가 50bp" in text
    assert "## Graph updates" in text  # full report saved, not just digest

def test_run_cycle_marks_articles_processed():
    insert_article("g1", "Reuters", "Title", "https://x.com", "2026-05-05T00:00:00", "body")
    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message"):
        run_cycle("2026-05-05-00")
    assert get_unprocessed() == []

def test_run_cycle_marks_processed_even_if_telegram_fails():
    insert_article("g1", "Reuters", "Title", "https://x.com", "2026-05-05T00:00:00", "body")
    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message", side_effect=RuntimeError("boom")):
        run_cycle("2026-05-05-00")
    assert get_unprocessed() == []
