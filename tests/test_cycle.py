import os
from pathlib import Path
import pytest
from unittest.mock import patch, call

from newsparser.store.sqlite import init_db, insert_article, get_unprocessed
from newsparser.scheduler.cycle import run_cycle


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("NEO4J_PASSWORD", "testpass")
    init_db()


SAMPLE_TECH_DIGEST = """사이클 2026-05-07 12:00 KST [tech]

새 소식
• (중요도 0.8) OpenAI 신모델 발표.

오픈 스레드
• 없음"""

SAMPLE_TECH_REPORT = SAMPLE_TECH_DIGEST + """

## Graph updates
### Entities
- NEW | Company | OpenAI | aliases: []

### Relations
"""

SAMPLE_MARKETS_DIGEST = """사이클 2026-05-07 12:00 KST [markets]

새 소식
• (중요도 0.7) Fed 50bp 인하.

오픈 스레드
• 없음"""

SAMPLE_MARKETS_REPORT = SAMPLE_MARKETS_DIGEST + """

## Graph updates
### Entities
- NEW | Institution | Fed | aliases: [연준]

### Relations
"""


def test_run_cycle_classifies_null_articles_then_dispatches_per_category(tmp_path):
    insert_article("g1", "OpenAI Blog", "Model X", "https://x.com/1", None, "release", category="tech")
    insert_article("g2", "FT", "Fed cuts", "https://x.com/2", None, "rate cut", category="markets")
    insert_article("g3", "HN", "Mixed", "https://x.com/3", None, "ambiguous")  # NULL

    def fake_classify(title, body):
        return "tech" if "Mixed" in title else "markets"

    fake_run_claude_calls: list[str] = []

    def fake_run_claude(prompt, **kw):
        fake_run_claude_calls.append(prompt)
        if "[tech]" in prompt or "tech" in prompt[:200]:
            return SAMPLE_TECH_REPORT
        return SAMPLE_MARKETS_REPORT

    with patch("newsparser.scheduler.cycle.classify_article", side_effect=fake_classify), \
         patch("newsparser.scheduler.cycle.run_claude", side_effect=fake_run_claude), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message"):
        run_cycle("2026-05-07-12")

    # 2 cycles dispatched (tech + markets), each got their own claude call
    assert len(fake_run_claude_calls) == 2
    # All articles processed
    assert get_unprocessed() == []


def test_run_cycle_skips_empty_category(tmp_path):
    insert_article("g1", "OpenAI Blog", "Model X", "https://x.com/1", None, "release", category="tech")
    # No markets articles

    fake_run_claude_calls: list[str] = []

    def fake_run_claude(prompt, **kw):
        fake_run_claude_calls.append(prompt)
        return SAMPLE_TECH_REPORT

    with patch("newsparser.scheduler.cycle.classify_article"), \
         patch("newsparser.scheduler.cycle.run_claude", side_effect=fake_run_claude), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message"):
        run_cycle("2026-05-07-12")

    assert len(fake_run_claude_calls) == 1


def test_run_cycle_writes_per_category_report(tmp_path):
    insert_article("g1", "OpenAI Blog", "Model X", "https://x.com/1", None, "release", category="tech")
    workspace = Path(os.environ["WORKSPACE_DIR"])

    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_TECH_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message"):
        run_cycle("2026-05-07-12")

    report = workspace / "cycles" / "tech" / "2026-05-07-12.md"
    assert report.exists()
    assert "OpenAI" in report.read_text()


def test_run_cycle_telegram_prefix_marks_category(tmp_path):
    insert_article("g1", "OpenAI Blog", "Model X", "https://x.com/1", None, "release", category="tech")

    sent: list[str] = []

    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_TECH_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message",
               side_effect=lambda msg: sent.append(msg)):
        run_cycle("2026-05-07-12")

    assert len(sent) == 1
    assert sent[0].startswith("[TECH]")
    assert "## Graph updates" not in sent[0]


def test_run_cycle_passes_category_to_apply_graph_updates(tmp_path):
    insert_article("g1", "OpenAI Blog", "Model X", "https://x.com/1", None, "release", category="tech")

    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_TECH_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates") as mock_apply, \
         patch("newsparser.scheduler.cycle.send_long_message"):
        run_cycle("2026-05-07-12")

    mock_apply.assert_called_once()
    kwargs = mock_apply.call_args.kwargs
    assert kwargs["cycle_id"] == "tech-2026-05-07-12"
    assert kwargs["category"] == "tech"


def test_run_cycle_marks_processed_even_if_telegram_fails(tmp_path):
    insert_article("g1", "OpenAI Blog", "Model X", "https://x.com/1", None, "release", category="tech")

    with patch("newsparser.scheduler.cycle.run_claude", return_value=SAMPLE_TECH_REPORT), \
         patch("newsparser.scheduler.cycle.apply_graph_updates"), \
         patch("newsparser.scheduler.cycle.send_long_message", side_effect=RuntimeError("boom")):
        run_cycle("2026-05-07-12")

    assert get_unprocessed() == []
