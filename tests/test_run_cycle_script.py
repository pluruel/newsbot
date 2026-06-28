# tests/test_run_cycle_script.py
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from newsparser.store.sqlite import insert_article, get_unprocessed
from newsparser.ignore import IgnoreList
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


# The skill writes the full digest to the report file and prints the terse keyword
# summary to stdout (run_claude's return value), mirroring /weekly and /reflect.
TERSE_SUMMARY = """2026-05-08 12:00 KST

새 소식
• (0.8) OpenAI 신모델 발표"""


def _fake_run_claude_writes_report(prompt, **kw):
    """Simulates claude writing the full report file and printing the terse stdout summary."""
    import os as _os
    from pathlib import Path as _Path
    ws = _Path(_os.environ["WORKSPACE_DIR"])
    parts = prompt.strip().split()
    slot, category = parts[1], parts[2]
    report_dir = ws / "cycles" / category
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{slot}.md").write_text(SAMPLE_REPORT)
    return TERSE_SUMMARY


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
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message"):
        script.main("2026-05-08-12")

    assert guids_seen == [True], "guids file must exist when run_claude is called"


def test_run_cycle_sends_terse_importance_list(tmp_path):
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    sent: list[str] = []

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=_fake_run_claude_writes_report), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message", side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08-12")

    assert len(sent) == 1
    msg = sent[0]
    assert msg.startswith("[TECH]")
    assert "## Graph updates" not in msg
    assert "• 0.80 OpenAI 신모델 발표" in msg   # rendered from report file, score formatted 0.NN
    assert "(중요도" not in msg                   # verbose file markup is NOT sent


def test_run_cycle_renders_from_file_ignoring_stdout(tmp_path):
    """Telegram is rendered from the report file; LLM stdout is irrelevant."""
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    sent: list[str] = []

    def fake_garbage_stdout(prompt, **kw):
        import os as _os
        from pathlib import Path as _Path
        ws = _Path(_os.environ["WORKSPACE_DIR"])
        slot, category = prompt.strip().split()[1:3]
        report_dir = ws / "cycles" / category
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{slot}.md").write_text(SAMPLE_REPORT)
        return "ASDF 쓰레기 stdout 무시되어야 함"  # must be ignored

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=fake_garbage_stdout), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message", side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08-12")

    assert len(sent) == 1
    msg = sent[0]
    assert msg.startswith("[TECH]")
    assert "• 0.80 OpenAI 신모델 발표" in msg
    assert "쓰레기 stdout" not in msg


def test_run_cycle_drops_ignored_headline_from_telegram(tmp_path):
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    two_item_report = (
        "사이클 2026-05-08 12:00 KST\n\n"
        "새 소식\n"
        "• (중요도 0.9) Claude Opus 4.8 API 미등장 추측 재확산. 본문.\n"
        "• (중요도 0.7) 엔비디아 신규 GPU 공개. 본문.\n\n"
        "## Graph updates\n"
    )

    def fake_claude(prompt, **kw):
        import os as _os
        from pathlib import Path as _Path
        ws = _Path(_os.environ["WORKSPACE_DIR"])
        slot, category = prompt.strip().split()[1:3]
        (ws / "cycles" / category).mkdir(parents=True, exist_ok=True)
        (ws / "cycles" / category / f"{slot}.md").write_text(two_item_report)
        (ws / "me").mkdir(parents=True, exist_ok=True)
        (ws / "me" / "ignore.md").write_text(
            "| 종류 | 대상 | 추가일 | 메모 |\n"
            "|------|------|--------|------|\n"
            "| storyline | Opus 4.8 API 미등장 | 2026-06-28 |  |\n",
            encoding="utf-8",
        )
        return ""

    sent: list[str] = []
    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=fake_claude), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message", side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08-12")

    assert len(sent) == 1
    msg = sent[0]
    assert "미등장" not in msg                       # ignored storyline dropped
    assert "• 0.70 엔비디아 신규 GPU 공개" in msg     # other item kept


def test_render_telegram_keeps_highest_score_for_duplicate_headline():
    report = (
        "새 소식\n"
        "• (중요도 0.6) 환율 급등.\n"
        "이어지는 흐름\n"
        "• (중요도 0.8) 환율 급등. 추가 본문.\n"
        "## Graph updates\n"
    )
    # Dedup must keep the higher score, not the first-by-document-order one.
    assert script._render_telegram(report, IgnoreList([])) == ["• 0.80 환율 급등"]


def test_render_telegram_does_not_truncate_at_abbreviations():
    report = (
        "새 소식\n"
        "• (중요도 0.8) U.S. 반도체 수출 통제 강화. 본문.\n"
        "• (중요도 0.7) Apple Inc. 신제품 발표. 본문 문장.\n"
        "• (중요도 0.6) 2026. 6. 28. 美 연준 동결. 본문.\n"
        "## Graph updates\n"
    )
    lines = script._render_telegram(report, IgnoreList([]))
    assert "• 0.80 U.S. 반도체 수출 통제 강화" in lines
    assert "• 0.70 Apple Inc. 신제품 발표" in lines
    assert "• 0.60 2026. 6. 28. 美 연준 동결" in lines


def test_run_cycle_warns_when_report_has_items_but_render_empty(tmp_path, caplog):
    import logging
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    drift_report = (
        "새 소식\n"
        "• (중요도: 0.8) 콜론 형식이라 regex 불일치.\n"   # colon → _CYCLE_ITEM_RE won't match
        "## Graph updates\n"
    )

    def fake_claude(prompt, **kw):
        import os as _os
        from pathlib import Path as _Path
        ws = _Path(_os.environ["WORKSPACE_DIR"])
        slot, category = prompt.strip().split()[1:3]
        (ws / "cycles" / category).mkdir(parents=True, exist_ok=True)
        (ws / "cycles" / category / f"{slot}.md").write_text(drift_report)
        return ""

    sent: list[str] = []
    with caplog.at_level(logging.WARNING), \
         patch("newsparser.scripts.run_cycle.run_claude", side_effect=fake_claude), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message", side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08-12")

    assert sent == ["[TECH]\n새 소식 없음"]
    assert any("format drift" in r.getMessage() for r in caplog.records)


def test_run_cycle_skips_empty_category(tmp_path):
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    claude_calls: list[str] = []

    def fake_claude(prompt, **kw):
        claude_calls.append(prompt)
        _fake_run_claude_writes_report(prompt, **kw)
        return ""

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=fake_claude), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
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
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="markets"), \
         patch("newsparser.scripts.run_cycle.send_long_message", side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08-12")

    assert len(sent) == 1
    assert sent[0].startswith("[MARKETS]")


def test_snapshot_block_prepended_to_input(tmp_path, monkeypatch):
    """run_cycle should prepend a ## 시장 스냅샷 block above the article list."""
    from datetime import date
    from unittest.mock import patch

    monkeypatch.setenv("MARKET_DB_PATH", str(tmp_path / "market.db"))
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))

    from newsparser.store.sqlite import init_db, insert_article
    from newsparser.market import store as market_store
    init_db()
    market_store.init_market_db()
    market_store.upsert_daily([
        {"instrument": "SPX", "date": "2026-05-07",
         "open": 100, "high": 100, "low": 100, "close": 100, "volume": 1},
        {"instrument": "SPX", "date": "2026-05-08",
         "open": 100, "high": 100, "low": 100, "close": 102, "volume": 1},
    ])

    insert_article("g1", "Bloomberg", "T", "https://x.com/1", None, "body", category="markets")

    seen_input: list[str] = []

    def fake_run_claude(prompt, **kw):
        ws = tmp_path / "workspace"
        slot, cat = prompt.strip().split()[1:3]
        seen_input.append((ws / "input" / cat / f"{slot}-input.md").read_text())
        report_dir = ws / "cycles" / cat
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / f"{slot}.md").write_text("사이클 OK\n## Graph updates\n")
        return ""

    import newsparser.scripts.run_cycle as run_cycle
    with patch.object(run_cycle, "run_claude", side_effect=fake_run_claude), \
         patch.object(run_cycle, "send_long_message"):
        run_cycle.main("2026-05-09-12")

    assert any("## 시장 스냅샷" in t for t in seen_input)
    # Snapshot must precede article list
    text = next(t for t in seen_input if "## 시장 스냅샷" in t)
    snap_idx = text.find("## 시장 스냅샷")
    art_idx = text.find("Collected Articles")
    assert 0 <= snap_idx < art_idx
