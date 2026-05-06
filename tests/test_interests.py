import json
import os
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from newsparser.scheduler.interests import interests_rollup


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    ws = tmp_path / "workspace" / "me"
    ws.mkdir(parents=True)
    (ws / "interests.md").write_text(
        "# Interests Profile\nLast updated: 2026-05-01\n\n## Themes\n\n## User overrides\n- 항상 포함: 반도체\n",
        encoding="utf-8",
    )
    (ws / "interest-events.jsonl").touch()


def _write_event(tmp_path, query: str, entities: list[str], days_ago: int = 1):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    event = {"ts": ts, "type": "query", "entities": entities, "themes": [query], "depth": "shallow"}
    ws = Path(os.environ["WORKSPACE_DIR"])
    with (ws / "me" / "interest-events.jsonl").open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def test_rollup_skips_when_no_events():
    with patch("newsparser.scheduler.interests.run_claude") as mock_claude:
        interests_rollup()
    mock_claude.assert_not_called()


def test_rollup_skips_events_older_than_14_days(tmp_path):
    _write_event(tmp_path, "테크기사", ["삼성전자"], days_ago=15)
    with patch("newsparser.scheduler.interests.run_claude") as mock_claude:
        interests_rollup()
    mock_claude.assert_not_called()


def test_rollup_calls_claude_with_event_data(tmp_path):
    _write_event(tmp_path, "반도체 업황", ["삼성전자", "TSMC"], days_ago=1)
    with patch("newsparser.scheduler.interests.run_claude", return_value="# Interests Profile\nLast updated: 2026-05-06\n\n## Themes\n- 반도체\n\n## User overrides\n- 항상 포함: 반도체\n") as mock_claude:
        interests_rollup()
    mock_claude.assert_called_once()
    prompt = mock_claude.call_args[0][0]
    assert "삼성전자" in prompt
    assert "TSMC" in prompt
    assert "반도체 업황" in prompt
    assert "User overrides" in prompt


def test_rollup_writes_claude_output_to_interests_md(tmp_path):
    _write_event(tmp_path, "AI 반도체", ["엔비디아"], days_ago=2)
    new_content = "# Interests Profile\nLast updated: 2026-05-06\n\n## Themes\n- AI 반도체\n\n## User overrides\n- 항상 포함: 반도체\n"
    with patch("newsparser.scheduler.interests.run_claude", return_value=new_content):
        interests_rollup()
    ws = Path(os.environ["WORKSPACE_DIR"])
    written = (ws / "me" / "interests.md").read_text(encoding="utf-8")
    assert written == new_content
