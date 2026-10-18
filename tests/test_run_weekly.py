# tests/test_run_weekly.py
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from newsparser.claude.runner import ClaudeError
import newsparser.scripts.run_weekly as script


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("NEO4J_PASSWORD", "testpass")


def _write_report(category: str, stem: str, text: str) -> None:
    import os
    path = Path(os.environ["WORKSPACE_DIR"]) / "cycles" / category / f"{stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _weekly_path(date: str) -> Path:
    import os
    return Path(os.environ["WORKSPACE_DIR"]) / "cycles" / "weekly" / f"{date}.md"


def test_run_weekly_writes_briefing_and_sends_headlines(tmp_path):
    reply = json.dumps({"briefing": "주간 브리핑 본문", "headlines": ["• 첫째", "둘째"]},
                       ensure_ascii=False)
    sent: list[str] = []

    with patch("newsparser.scripts.run_weekly.ask_claude", return_value=reply), \
         patch("newsparser.scripts.run_weekly.send_long_message",
               side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08")

    assert _weekly_path("2026-05-08").read_text(encoding="utf-8") == "주간 브리핑 본문"
    assert sent == ["[WEEKLY] • 첫째\n• 둘째"]


def test_run_weekly_prompt_has_last_7_days_without_graph_block(tmp_path):
    graph = "\n## Graph updates\n### Entities\n- NEW | Company | GRAPH-ONLY | aliases: []\n"
    _write_report("tech", "2026-05-01-12", "OUT-OF-RANGE" + graph)
    _write_report("tech", "2026-05-02-12", "TECH-IN-RANGE" + graph)
    _write_report("markets", "2026-05-08-12", "MARKETS-IN-RANGE" + graph)

    calls: list[tuple[str, str, dict]] = []

    def fake_ask(prompt, system, **kw):
        calls.append((prompt, system, kw))
        return '{"briefing":"b","headlines":[]}'

    with patch("newsparser.scripts.run_weekly.ask_claude", side_effect=fake_ask), \
         patch("newsparser.scripts.run_weekly.send_long_message"):
        script.main("2026-05-08")

    assert len(calls) == 1
    prompt, system, kw = calls[0]
    assert "기준일 2026-05-08" in prompt
    assert '<report name="tech/2026-05-02-12">' in prompt
    assert '<report name="markets/2026-05-08-12">' in prompt
    assert "TECH-IN-RANGE" in prompt and "MARKETS-IN-RANGE" in prompt
    assert "OUT-OF-RANGE" not in prompt
    assert "## Graph updates" not in prompt and "GRAPH-ONLY" not in prompt
    assert "<demand>" in prompt and "사용자 관심 수요" in prompt
    assert "weekly briefing" in system
    assert kw["timeout"] == 1500
    assert kw["output_schema"] is script.WEEKLY_SCHEMA


def test_run_weekly_skips_telegram_when_no_headlines(tmp_path):
    sent: list[str] = []

    with patch("newsparser.scripts.run_weekly.ask_claude",
               return_value='{"briefing":"본문","headlines":[]}'), \
         patch("newsparser.scripts.run_weekly.send_long_message",
               side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08")

    assert _weekly_path("2026-05-08").read_text(encoding="utf-8") == "본문"
    assert sent == []


def test_run_weekly_invalid_json_raises_and_writes_nothing(tmp_path):
    sent: list[str] = []

    with patch("newsparser.scripts.run_weekly.ask_claude", return_value="not json"), \
         patch("newsparser.scripts.run_weekly.send_long_message",
               side_effect=lambda m: sent.append(m)), \
         pytest.raises(ClaudeError, match="weekly: invalid JSON"):
        script.main("2026-05-08")

    assert not _weekly_path("2026-05-08").exists()
    assert sent == []
