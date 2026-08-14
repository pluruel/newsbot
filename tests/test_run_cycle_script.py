# tests/test_run_cycle_script.py
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from newsparser.store.sqlite import insert_article, get_unprocessed
from newsparser.ignore import IgnoreList, IgnoreEntry
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


def test_run_cycle_retries_until_report_appears(tmp_path):
    """A clean exit without a report file is a failed attempt, not success."""
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    calls: list[str] = []

    def flaky_claude(prompt, **kw):
        calls.append(prompt)
        if len(calls) < 3:
            return ""              # exit 0, no report — the silent-early-exit case
        return _fake_run_claude_writes_report(prompt, **kw)

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=flaky_claude), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message"):
        script.main("2026-05-08-12")

    assert len(calls) == 3
    assert get_unprocessed(category="tech") == []   # 3rd attempt succeeded → processed


def test_run_cycle_keeps_articles_pending_when_no_report_after_all_attempts(tmp_path):
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    calls: list[str] = []
    sent: list[str] = []

    def silent_claude(prompt, **kw):
        calls.append(prompt)
        return ""                  # never writes a report

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=silent_claude), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message", side_effect=lambda m: sent.append(m)):
        script.main("2026-05-08-12")

    assert len(calls) == script.CYCLE_CLAUDE_ATTEMPTS
    assert sent == []
    # The whole point: articles must stay pending for the next slot, not be
    # swallowed by the mark_processed safety net.
    assert [a["guid"] for a in get_unprocessed(category="tech")] == ["g1"]
    log = (Path(os.environ["WORKSPACE_DIR"]) / "logs" / "2026-05-08.log").read_text()
    assert "FAIL" in log and "OK" not in log


def test_run_cycle_retries_on_claude_error(tmp_path):
    from newsparser.claude.runner import ClaudeError

    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    calls: list[str] = []

    def crashy_claude(prompt, **kw):
        calls.append(prompt)
        if len(calls) < 2:
            raise ClaudeError("boom")
        return _fake_run_claude_writes_report(prompt, **kw)

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=crashy_claude), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message"):
        script.main("2026-05-08-12")

    assert len(calls) == 2
    assert get_unprocessed(category="tech") == []


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
    # Dedup must keep the higher score AND its section (이어지는 흐름, not 새 소식).
    assert script._render_telegram(report, IgnoreList([])) == ["이어지는 흐름", "• 0.80 환율 급등"]


def test_render_telegram_keeps_section_headers_and_groups_items():
    report = (
        "사이클 2026-05-08 12:00 KST\n\n"
        "새 소식\n"
        "• (중요도 0.8) A 발표.\n"
        "이어지는 흐름\n"
        "• (중요도 0.5) B 후속.\n"
        "## Graph updates\n"
    )
    lines = script._render_telegram(report, IgnoreList([]))
    assert lines[0] == "사이클 2026-05-08 12:00 KST"        # timestamp header (E)
    assert "새 소식" in lines and "이어지는 흐름" in lines    # section grouping (D)
    # 새 소식 group precedes 이어지는 흐름, each holding its own item
    assert lines.index("• 0.80 A 발표") > lines.index("새 소식")
    assert lines.index("• 0.50 B 후속") > lines.index("이어지는 흐름")
    assert lines.index("새 소식") < lines.index("이어지는 흐름")


def test_render_telegram_includes_entity_source_line():
    report = (
        "새 소식\n"
        "• (중요도 0.8) 엔비디아 실적 상회. 본문 문장.\n"
        "  엔티티: 엔비디아, TSMC / 출처: Bloomberg\n"
        "## Graph updates\n"
    )
    lines = script._render_telegram(report, IgnoreList([]))
    assert "• 0.80 엔비디아 실적 상회" in lines
    assert "  엔티티: 엔비디아, TSMC / 출처: Bloomberg" in lines   # C restored


def test_render_telegram_includes_quiet_and_open_threads():
    report = (
        "새 소식\n"
        "• (중요도 0.8) A 발표.\n"
        "조용한 영역\n"
        "• 12시 사이클에 예상된 FOMC 코멘트 관측 안 됨\n"
        "오픈 스레드\n"
        "• 디든로보틱스 추가 수주 여부\n"
        "## Graph updates\n"
    )
    lines = script._render_telegram(report, IgnoreList([]))
    assert "조용한 영역" in lines                                  # A restored
    assert "• 12시 사이클에 예상된 FOMC 코멘트 관측 안 됨" in lines
    assert "오픈 스레드" in lines
    assert "• 디든로보틱스 추가 수주 여부" in lines


def test_render_telegram_tolerates_decorated_section_headers():
    # Header drift (## / ** / trailing count / dropped space) must still render items.
    for header in ("## 새 소식", "**새 소식**", "새 소식 (3건)", "새소식", "새 소식:"):
        report = f"{header}\n• (중요도 0.8) A 발표.\n## Graph updates\n"
        lines = script._render_telegram(report, IgnoreList([]))
        assert lines == ["새 소식", "• 0.80 A 발표"], f"header {header!r} not recognized"


def test_render_telegram_attaches_meta_across_blank_and_wrapped_lines():
    report = (
        "새 소식\n"
        "• (중요도 0.8) A 발표. 본문 첫 문장.\n"
        "본문이 다음 줄로 이어짐.\n"        # wrapped body line (no bullet)
        "\n"                               # blank line
        "  엔티티: 엔비디아 / 출처: BBG\n"
        "## Graph updates\n"
    )
    lines = script._render_telegram(report, IgnoreList([]))
    assert "• 0.80 A 발표" in lines
    assert "  엔티티: 엔비디아 / 출처: BBG" in lines   # meta survives the gap


def test_render_telegram_drops_meta_line_with_ignored_entity():
    report = (
        "새 소식\n"
        "• (중요도 0.8) 엔비디아 실적 상회.\n"
        "  엔티티: 엔비디아, TSMC / 출처: BBG\n"
        "## Graph updates\n"
    )
    ignore = IgnoreList([IgnoreEntry(kind="entity", target="TSMC")])
    lines = script._render_telegram(report, ignore)
    assert "• 0.80 엔비디아 실적 상회" in lines       # headline kept (not ignored)
    assert all("TSMC" not in ln for ln in lines)      # ignored entity does not leak via meta


def test_render_telegram_preserves_meta_when_higher_dup_has_none():
    report = (
        "새 소식\n"
        "• (중요도 0.5) 환율 급등.\n"
        "  엔티티: 원달러 / 출처: 한은\n"
        "이어지는 흐름\n"
        "• (중요도 0.8) 환율 급등. 추가 본문.\n"   # higher score, no meta line follows
        "## Graph updates\n"
    )
    lines = script._render_telegram(report, IgnoreList([]))
    assert lines == ["이어지는 흐름", "• 0.80 환율 급등", "  엔티티: 원달러 / 출처: 한은"]


def test_render_telegram_warns_on_orphaned_scored_item(caplog):
    import logging
    # Well-formed scored item under a header that is NOT recognizable at all.
    report = "헤더없는잡음\n• (중요도 0.8) A 발표.\n## Graph updates\n"
    with caplog.at_level(logging.WARNING):
        lines = script._render_telegram(report, IgnoreList([]), label="tech/slot")
    assert lines == []                                       # orphaned item not rendered
    assert any("format drift" in r.getMessage() for r in caplog.records)


def test_render_telegram_omits_empty_and_none_sections():
    report = (
        "새 소식\n"
        "• (중요도 0.8) A 발표.\n"
        "조용한 영역\n"
        "• 없음\n"
        "오픈 스레드\n"
        "• 없음\n"
        "## Graph updates\n"
    )
    lines = script._render_telegram(report, IgnoreList([]))
    assert "조용한 영역" not in lines      # `• 없음` placeholder → section omitted
    assert "오픈 스레드" not in lines
    assert "없음" not in "\n".join(lines)


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


def test_run_cycle_kill_aborts_remaining_categories(tmp_path):
    """A ClaudeKilled (intentional kill) must propagate out of main() — not be
    swallowed by the per-category error guard — so the JobManager can consume
    the kill marker and report 🛑, and no further categories run."""
    from newsparser.claude.runner import ClaudeKilled

    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")
    insert_article("g2", "src", "t2", "u2", None, "body", category="markets")

    calls: list[str] = []

    def fake_claude(prompt, **kw):
        calls.append(prompt)
        raise ClaudeKilled("killed by kill request")

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=fake_claude), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message"), \
         pytest.raises(ClaudeKilled):
        script.main("2026-05-08-12")

    assert len(calls) == 1


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


# --- backlog wedge regression -------------------------------------------------
# A failing category run skips the guids/mark_processed safety net, so the same
# articles stay unprocessed and next cycle's input file is strictly larger. Left
# unbounded that turns one failure into a permanent outage (tech stalled 5 days
# behind a 112-article backlog). The batch cap is what stops the regrowth.

def test_run_cycle_caps_batch_size(tmp_path):
    for i in range(script.CYCLE_MAX_ARTICLES + 15):
        insert_article(f"g{i:04d}", "src", f"t{i}", f"u{i}",
                       f"2026-05-08T{i % 24:02d}:00:00Z", "body", category="tech")

    seen_articles: list[list[dict]] = []

    with patch("newsparser.scripts.run_cycle.run_claude",
               side_effect=_fake_run_claude_writes_report), \
         patch("newsparser.scripts.run_cycle.build_input_file",
               side_effect=lambda s, c, articles=None: seen_articles.append(articles)), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message"):
        script.main("2026-05-08-12")

    assert len(seen_articles) == 1
    assert len(seen_articles[0]) == script.CYCLE_MAX_ARTICLES

    # Only the capped batch is claimed for processing — the rest stays pending
    # and drains on the next cycle instead of inflating the same input file.
    remaining = get_unprocessed(category="tech")
    assert len(remaining) == 15


def test_run_cycle_retires_stale_articles_unanalyzed(tmp_path):
    """Articles older than CYCLE_MAX_AGE_DAYS at slot time are marked processed
    without entering the input file — a stale backlog must not monopolize the
    oldest-first cap. Age is judged against the slot, and RFC-822 dates parse."""
    insert_article("fresh", "src", "fresh title", "u1",
                   "2026-05-08T01:00:00Z", "body", category="tech")
    insert_article("stale", "src", "stale title", "u2",
                   "Mon, 20 Apr 2026 09:00:00 GMT", "body", category="tech")

    seen_articles: list[list[dict]] = []

    with patch("newsparser.scripts.run_cycle.run_claude",
               side_effect=_fake_run_claude_writes_report), \
         patch("newsparser.scripts.run_cycle.build_input_file",
               side_effect=lambda s, c, articles=None: seen_articles.append(articles)), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message"):
        script.main("2026-05-08-12")

    assert [a["guid"] for a in seen_articles[0]] == ["fresh"]
    # Retired, not deferred: the stale article must not resurface next cycle.
    assert get_unprocessed(category="tech") == []


def test_run_cycle_passes_same_articles_to_guids_and_input_file(tmp_path):
    """The guids file and the input file must describe the identical article set.
    Two independent get_unprocessed() calls can diverge when the poller inserts
    between them, leaking articles that are indexed but never marked processed."""
    insert_article("g1", "src", "t1", "u1", "2026-05-08T01:00:00Z", "body", category="tech")
    insert_article("g2", "src", "t2", "u2", "2026-05-08T02:00:00Z", "body", category="tech")

    seen_articles: list[list[dict]] = []

    def fake_build(slot, category, articles=None):
        seen_articles.append(articles)
        # Simulate the poller landing a new article mid-run.
        insert_article("g3", "src", "t3", "u3", "2026-05-08T03:00:00Z", "body", category="tech")

    guids_text: list[str] = []

    def fake_claude(prompt, **kw):
        ws = Path(os.environ["WORKSPACE_DIR"])
        slot, category = prompt.strip().split()[1:3]
        guids_text.append((ws / "input" / category / f"{slot}-guids.txt").read_text())
        return _fake_run_claude_writes_report(prompt, **kw)

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=fake_claude), \
         patch("newsparser.scripts.run_cycle.build_input_file", side_effect=fake_build), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message"):
        script.main("2026-05-08-12")

    assert seen_articles[0] is not None
    assert [a["guid"] for a in seen_articles[0]] == guids_text[0].split()


def test_run_cycle_records_category_failure_in_daily_log(tmp_path):
    """A category that blows up must leave a FAIL line in the daily workspace log.
    Today it only reaches the systemd journal, which is why a 5-day tech outage
    read as 'no tech lines at all' instead of a recorded error."""
    insert_article("g1", "src", "t1", "u1", None, "body", category="tech")

    def fake_claude(prompt, **kw):
        if " tech" in prompt:
            raise RuntimeError("claude timed out after 1500s")
        return _fake_run_claude_writes_report(prompt, **kw)

    with patch("newsparser.scripts.run_cycle.run_claude", side_effect=fake_claude), \
         patch("newsparser.scripts.run_cycle.build_input_file"), \
         patch("newsparser.scripts.run_cycle.classify_article", return_value="tech"), \
         patch("newsparser.scripts.run_cycle.send_long_message"):
        script.main("2026-05-08-12")

    log_text = (tmp_path / "workspace" / "logs" / "2026-05-08.log").read_text()
    assert "cycle tech-2026-05-08-12 FAIL" in log_text
    assert "claude timed out after 1500s" in log_text


def test_report_feed_health_sends_only_when_persistent():
    from newsparser.store.sqlite import record_feed_failure

    # 임계 미만(11회) — 발송 없음
    for _ in range(script.FEED_HEALTH_MIN_FAILURES - 1):
        record_feed_failure("중앙일보", "HTTP 404")
    with patch.object(script, "send_long_message") as mock_send:
        script._report_feed_health()
    mock_send.assert_not_called()

    # 임계 도달 — 소스명·횟수·에러가 담긴 메시지 발송
    record_feed_failure("중앙일보", "HTTP 404")
    with patch.object(script, "send_long_message") as mock_send:
        script._report_feed_health()
    mock_send.assert_called_once()
    msg = mock_send.call_args[0][0]
    assert "피드 이상" in msg
    assert "중앙일보" in msg
    assert f"{script.FEED_HEALTH_MIN_FAILURES}회 연속 실패" in msg
    assert "HTTP 404" in msg
