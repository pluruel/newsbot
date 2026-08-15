import json
from unittest.mock import patch

import pytest

from newsparser import triage
from newsparser.claude.runner import ClaudeError
from newsparser.store.sqlite import (
    get_haiku_usage,
    get_untriaged,
    insert_article,
    record_haiku_usage,
    update_triage,
)
from newsparser.triage import (
    DEFAULT_SCORE,
    FALLBACK_BUCKETS,
    NOISE_BUCKET,
    TriageResult,
    _parse_response,
    article_score,
    load_weights,
    select,
    triage_article,
)


# --- axis invariants ---

def test_bucket_names_unique_across_categories():
    names = [name for defs in triage.BUCKETS.values() for name, _ in defs]
    assert len(names) == len(set(names))
    assert NOISE_BUCKET not in names


def test_fallback_buckets_exist_in_axis():
    for cat, bucket in FALLBACK_BUCKETS.items():
        assert bucket in [name for name, _ in triage.BUCKETS[cat]]


# --- response parsing ---

def test_parse_valid_markets_bucket():
    r = _parse_response("한국증시 0.7", None)
    assert r == TriageResult("markets", "한국증시", 0.7)


def test_parse_valid_tech_bucket_resolves_category():
    r = _parse_response("AI릴리스 0.9", None)
    assert r.category == "tech"


def test_parse_clamps_salience():
    assert _parse_response("환율 1.8", None).salience == 1.0
    assert _parse_response("환율 1", None).salience == 1.0


def test_parse_noise_uses_hint_category():
    r = _parse_response("노이즈 0.1", "tech")
    assert r == TriageResult("tech", NOISE_BUCKET, 0.1)


def test_parse_noise_defaults_to_markets_without_hint():
    assert _parse_response("노이즈 0.1", None).category == "markets"


def test_parse_unknown_bucket_falls_back_with_hint():
    r = _parse_response("경제기타뭐시기 0.5", "markets")
    assert r == TriageResult("markets", "기타경제", 0.5)


def test_parse_unknown_bucket_without_hint_returns_none():
    assert _parse_response("이상한버킷 0.5", None) is None


def test_parse_garbage_returns_none():
    assert _parse_response("", None) is None
    assert _parse_response("설명이 길어요", None) is None


def test_parse_tolerates_surrounding_text():
    r = _parse_response("답: 통화정책 0.85", None)
    assert r == TriageResult("markets", "통화정책", 0.85)


# --- triage_article ---

def test_triage_article_returns_result():
    with patch("newsparser.triage.ask_haiku", return_value="채권금리 0.6") as mock:
        r = triage_article("미 국채 금리 급등", "본문")
    assert r == TriageResult("markets", "채권금리", 0.6)
    assert mock.call_args.kwargs.get("usage_tag") == "triage"


def test_triage_article_returns_none_on_api_error():
    with patch("newsparser.triage.ask_haiku", side_effect=ClaudeError("down")):
        assert triage_article("t", "b") is None


# --- weights ---

def test_load_weights_missing_file_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    assert load_weights("markets") == {}


def test_load_weights_reads_and_clamps(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    p = tmp_path / "me"
    p.mkdir(parents=True)
    (p / "triage_weights_markets.json").write_text(
        json.dumps({"한국증시": 0.9, "노이즈": 2.0, "bad": "x"}), encoding="utf-8"
    )
    w = load_weights("markets")
    assert w == {"한국증시": 0.9, "노이즈": 1.0}


def test_load_weights_invalid_json_is_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    p = tmp_path / "me"
    p.mkdir(parents=True)
    (p / "triage_weights_markets.json").write_text("{broken", encoding="utf-8")
    assert load_weights("markets") == {}


# --- scoring & selection ---

def _row(guid, bucket=None, salience=None, published="2026-08-15T00:00:00+00:00"):
    return {"guid": guid, "bucket": bucket, "salience": salience,
            "published": published, "fetched_at": published}


def test_article_score_fail_open_when_untriaged():
    assert article_score(_row("a"), {}) == DEFAULT_SCORE


def test_article_score_multiplies_weight_and_salience():
    assert article_score(_row("a", "한국증시", 0.8), {"한국증시": 0.5}) == 0.4


def test_article_score_unknown_bucket_weight_defaults_to_one():
    assert article_score(_row("a", "한국증시", 0.8), {}) == 0.8


def test_select_cuts_below_threshold_and_caps():
    articles = [
        _row("hi", "한국증시", 0.9, "2026-08-15T01:00:00+00:00"),
        _row("mid", "환율", 0.5, "2026-08-15T02:00:00+00:00"),
        _row("noise", NOISE_BUCKET, 0.9, "2026-08-15T03:00:00+00:00"),
    ]
    weights = {NOISE_BUCKET: 0.05}
    selected, cut, n_passed = select(articles, weights, cap=1, threshold=0.2)
    assert [a["guid"] for a in selected] == ["hi"]
    assert [a["guid"] for a in cut] == ["noise"]  # 0.05 * 0.9 < 0.2
    assert n_passed == 2  # hi + mid passed; mid missed the cap, stays pending


def test_select_orders_by_score_then_recency():
    articles = [
        _row("old_high", "한국증시", 0.9, "2026-08-14T00:00:00+00:00"),
        _row("new_high", "환율", 0.9, "2026-08-15T00:00:00+00:00"),
        _row("low", "환율", 0.3, "2026-08-15T12:00:00+00:00"),
    ]
    selected, cut, n_passed = select(articles, {}, cap=3, threshold=0.2)
    assert [a["guid"] for a in selected] == ["new_high", "old_high", "low"]
    assert not cut


def test_select_fail_open_rows_pass_default_threshold():
    selected, cut, n_passed = select([_row("untriaged")], {}, cap=5)
    assert selected and not cut


# --- store roundtrip ---

def test_untriaged_roundtrip():
    insert_article("g1", "src", "t", "http://u", None, "b", category="markets")
    rows = get_untriaged(limit=10)
    assert [r["guid"] for r in rows] == ["g1"]
    update_triage("g1", "tech", "AI릴리스", 0.9)
    assert get_untriaged(limit=10) == []


def test_haiku_usage_accumulates():
    record_haiku_usage("triage", 100, 5)
    record_haiku_usage("triage", 50, 3)
    record_haiku_usage("other", 10, 1)
    rows = {r["tag"]: r for r in get_haiku_usage(days=1)}
    assert rows["triage"]["calls"] == 2
    assert rows["triage"]["input_tokens"] == 150
    assert rows["triage"]["output_tokens"] == 8
    assert rows["other"]["calls"] == 1


# --- axis snapshot ---

def test_write_axis_snapshot(tmp_path):
    path = triage.write_axis_snapshot(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data["buckets"]) == {"tech", "markets"}
    assert data["noise_bucket"] == NOISE_BUCKET
