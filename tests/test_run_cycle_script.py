# tests/test_run_cycle_script.py
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from newsparser.store.sqlite import insert_article, get_unprocessed
import newsparser.scripts.run_cycle as script


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("NEO4J_PASSWORD", "testpass")


SAMPLE_REPORT = """\
사이클 2026-05-08 12:00 KST

새 소식
• (중요도 0.8) OpenAI 신모델 발표.

오픈 스레드
• 없음

## Graph updates
### Entities
- NEW | Company | OpenAI | aliases: []

### Relations
"""


def _fake_run_claude_writes_report(prompt, **kw):
    """Simulates claude writing the report file, then returning."""
    import os as _os
    from pathlib import Path as _Path
    ws = _Path(_os.environ["WORKSPACE_DIR"])
    parts = prompt.strip().split()
    slot, category = parts[1], parts[2]
    report_dir = ws / "cycles" / category
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{slot}.md").write_text(SAMPLE_REPORT)
    return ""


def test_run_cycle_writes_guids_file_before_calling_run_claude(tmp_path):
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    guids_seen: list[bool] = []

    def fake_claude(prompt, **kw):
        import os as _os
        from pathlib import Path as _Path
        ws = _Path(_os.environ["WORKSPACE_DIR"])
        parts = prompt.strip().split()
        slot, category = parts[1], parts[2]
        guids_path = ws / "input" / category / f"{slot}-guids.txt"
        guids_seen.append(guids_path.exists())
        _fake_run_claude_writes_report(prompt, **kw)
        return ""

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=fake_claude), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message"):
        script.main("2026-05-08-12")

    assert guids_seen == [True], "guids file must exist when run_claude is called"


def test_run_cycle_sends_digest_to_telegram(tmp_path):
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    sent: list[str] = []

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=_fake_run_claude_writes_report), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message", side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08-12")

    assert len(sent) == 1
    assert sent[0].startswith("[TECH]")
    assert "## Graph updates" not in sent[0]


def test_run_cycle_skips_empty_category(tmp_path):
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    claude_calls: list[str] = []

    def fake_claude(prompt, **kw):
        claude_calls.append(prompt)
        _fake_run_claude_writes_report(prompt, **kw)
        return ""

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=fake_claude), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message"):
        script.main("2026-05-08-12")

    assert len(claude_calls) == 1
    assert "tech" in claude_calls[0]


def test_run_cycle_category_error_doesnt_stop_other(tmp_path):
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")
    insert_article("g2", "src", "t2", "u2", None, "body", category="markets")

    sent: list[str] = []

    def fake_claude(prompt, **kw):
        parts = prompt.strip().split()
        if parts[2] == "tech":
            raise RuntimeError("tech failed")
        _fake_run_claude_writes_report(prompt, **kw)
        return ""

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=fake_claude), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="markets"), \
         patch("newsparser.scripts.run_cycle.send_long_message", side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08-12")

    assert len(sent) == 1
    assert sent[0].startswith("[MARKETS]")
