from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from newsparser.market import headlines
from newsparser.store.sqlite import insert_article

NOW = datetime(2026, 7, 28, 6, 0, tzinfo=timezone.utc)


def _articles(titles: list[str]) -> list[dict]:
    return [{"guid": f"g{i}", "title": t, "source": "테스트", "url": ""}
            for i, t in enumerate(titles)]


def test_parse_picks_extracts_indices():
    assert headlines._parse_picks("1, 3", 5) == [1, 3]
    assert headlines._parse_picks("2\n", 5) == [2]


def test_parse_picks_treats_none_as_empty():
    assert headlines._parse_picks("none", 5) == []
    assert headlines._parse_picks("None.", 5) == []
    assert headlines._parse_picks("", 5) == []


def test_parse_picks_drops_out_of_range_and_duplicates():
    assert headlines._parse_picks("0, 2, 2, 99, 3", 5) == [2, 3]


def test_parse_picks_caps_at_max():
    assert len(headlines._parse_picks("1,2,3,4,5", 5)) == headlines.MAX_PICKS


def test_parse_picks_ignores_prose_without_numbers():
    """A chatty reply must not be treated as a selection."""
    assert headlines._parse_picks("관련 기사를 찾지 못했습니다", 5) == []


def test_dedupe_collapses_same_story_from_multiple_outlets():
    arts = _articles([
        "한국은행 총재 금리 인하 시점 신중히 검토",
        "한국은행 총재 금리 인하 시점 신중히 발언",
        "삼성전자 신형 반도체 양산 개시",
    ])
    kept = headlines._dedupe(arts)
    assert len(kept) == 2
    assert kept[0]["guid"] == "g0"  # earliest survivor wins
    assert kept[1]["guid"] == "g2"


def test_select_returns_rows_the_model_pointed_at():
    arts = _articles(["첫 기사", "둘째 기사", "셋째 기사"])
    with patch.object(headlines, "run_claude", return_value="3,1") as rc:
        picks = headlines.select("KOSPI", "15분", "-1.30%", "14:15~14:30", arts)
    assert [p["title"] for p in picks] == ["셋째 기사", "첫 기사"]
    # Untrusted input: no tools, deny-by-default.
    kwargs = rc.call_args.kwargs
    assert kwargs["permission_mode"] == "default"
    assert "allowed_tools" not in kwargs
    assert kwargs["model"] == headlines.HAIKU_MODEL


def test_select_survives_claude_failure():
    from newsparser.claude.runner import ClaudeError
    arts = _articles(["첫 기사"])
    with patch.object(headlines, "run_claude", side_effect=ClaudeError("boom")):
        assert headlines.select("KOSPI", "15분", "-1.30%", "14:15~14:30", arts) == []


def test_select_skips_the_call_when_there_are_no_candidates():
    with patch.object(headlines, "run_claude") as rc:
        assert headlines.select("KOSPI", "15분", "+1%", "14:15~14:30", []) == []
    rc.assert_not_called()


def test_candidates_pulls_markets_articles_around_the_bar():
    bar_start = NOW - timedelta(minutes=15)
    for i, (offset, cat) in enumerate([(-40, "markets"),   # before the window
                                       (-20, "markets"),   # inside
                                       (-5, "markets"),    # inside
                                       (-10, "tech")]):    # wrong category
        ts = (bar_start + timedelta(minutes=offset)).isoformat()
        insert_article(f"g{i}", "테스트", f"기사 {i}", f"http://x/{i}", ts, None, cat)
        # insert_article stamps fetched_at itself; rewrite it to the test time.
        from newsparser.store.sqlite import _connection
        with _connection() as conn:
            conn.execute("UPDATE pending_articles SET fetched_at=? WHERE guid=?",
                         (ts, f"g{i}"))

    got = headlines.candidates(bar_start, now=NOW)
    assert {a["guid"] for a in got} == {"g1", "g2"}


def test_candidates_ignores_processed_flag():
    """The cycle marks rows processed on its own schedule; an alert must still
    see them."""
    from newsparser.store.sqlite import _connection, mark_processed
    ts = (NOW - timedelta(minutes=10)).isoformat()
    insert_article("p1", "테스트", "처리된 기사", "http://x/p1", ts, None, "markets")
    with _connection() as conn:
        conn.execute("UPDATE pending_articles SET fetched_at=? WHERE guid=?", (ts, "p1"))
    mark_processed(["p1"])
    got = headlines.candidates(NOW - timedelta(minutes=15), now=NOW)
    assert [a["guid"] for a in got] == ["p1"]
