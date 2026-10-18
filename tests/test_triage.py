import json
from datetime import datetime, timezone
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
    recency_factor,
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

def _j(bucket, salience):
    return json.dumps({"bucket": bucket, "salience": salience}, ensure_ascii=False)


def test_parse_valid_markets_bucket():
    r = _parse_response(_j("한국증시", 0.7), None)
    assert r == TriageResult("markets", "한국증시", 0.7)


def test_parse_valid_tech_bucket_resolves_category():
    r = _parse_response(_j("AI릴리스", 0.9), None)
    assert r == TriageResult("tech", "AI릴리스", 0.9)


def test_parse_clamps_salience():
    assert _parse_response(_j("환율", 1.8), None).salience == 1.0
    assert _parse_response(_j("환율", -0.2), None).salience == 0.0


def test_parse_noise_uses_hint_category():
    r = _parse_response(_j("노이즈", 0.1), "tech")
    assert r == TriageResult("tech", NOISE_BUCKET, 0.1)


def test_parse_noise_defaults_to_markets_without_hint():
    assert _parse_response(_j("노이즈", 0.1), None).category == "markets"


def test_parse_unknown_bucket_falls_back_with_hint():
    r = _parse_response(_j("경제기타뭐시기", 0.5), "markets")
    assert r == TriageResult("markets", FALLBACK_BUCKETS["markets"], 0.5)


def test_parse_unknown_bucket_without_hint_returns_none():
    assert _parse_response(_j("이상한버킷", 0.5), None) is None


def test_parse_garbage_returns_none():
    assert _parse_response("", None) is None
    assert _parse_response("설명이 길어요", None) is None
    assert _parse_response('{"bucket": "환율", "sal', None) is None  # truncated
    assert _parse_response('{"bucket": "환율"}', None) is None
    assert _parse_response('["환율", 0.5]', None) is None


def test_response_schema_enum_covers_axis():
    enum = triage._RESPONSE_SCHEMA["properties"]["bucket"]["enum"]
    axis = {name for defs in triage.BUCKETS.values() for name, _ in defs}
    assert set(enum) == axis | {NOISE_BUCKET}


# --- triage_article ---

def test_triage_article_returns_result():
    with patch("newsparser.triage.ask_haiku", return_value=_j("채권금리", 0.6)) as mock:
        r = triage_article("미 국채 금리 급등", "본문")
    assert r == TriageResult("markets", "채권금리", 0.6)
    assert mock.call_args.kwargs.get("usage_tag") == "triage"
    assert mock.call_args.kwargs.get("output_schema") is triage._RESPONSE_SCHEMA


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


# --- recency boost ---

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=timezone.utc)


def test_recency_factor_fresh_is_one_and_decays_to_floor():
    fresh = _row("f", published="2026-08-15T12:00:00+00:00")
    day_old = _row("d", published="2026-08-14T12:00:00+00:00")
    ancient = _row("a", published="2026-08-01T12:00:00+00:00")
    future = _row("x", published="2026-08-16T12:00:00+00:00")
    assert recency_factor(fresh, NOW) == 1.0
    assert recency_factor(day_old, NOW) == pytest.approx(
        triage.RECENCY_FLOOR + (1 - triage.RECENCY_FLOOR) * 0.5
    )
    assert recency_factor(ancient, NOW) == pytest.approx(triage.RECENCY_FLOOR, abs=1e-3)
    assert recency_factor(future, NOW) == 1.0


def test_recency_factor_is_discrete_per_cycle_window():
    # Same 3h cycle window → identical factor; the step drops only at the
    # window boundary.
    in_window_a = _row("a", published="2026-08-15T11:00:00+00:00")  # 1h old
    in_window_b = _row("b", published="2026-08-15T09:30:00+00:00")  # 2.5h old
    next_window = _row("c", published="2026-08-15T08:30:00+00:00")  # 3.5h old
    assert recency_factor(in_window_a, NOW) == recency_factor(in_window_b, NOW) == 1.0
    assert recency_factor(next_window, NOW) < 1.0


def test_select_recency_boost_lets_fresh_article_outrank_older_higher_salience():
    articles = [
        _row("old_high", "한국증시", 0.8, "2026-08-13T12:00:00+00:00"),
        _row("new_mid", "환율", 0.6, "2026-08-15T11:00:00+00:00"),
    ]
    # old_high raw 0.8 decays two days (×~0.625 → ~0.5); new_mid ~0.6 wins.
    selected, _, _ = select(articles, {}, cap=2, threshold=0.2, now=NOW)
    assert [a["guid"] for a in selected] == ["new_mid", "old_high"]


def test_select_threshold_cut_ignores_recency():
    # Raw score 0.25 passes threshold 0.2 even though the decayed rank score
    # (~0.125) would not — recency must never retire an article.
    old = _row("old", "환율", 0.25, "2026-08-01T00:00:00+00:00")
    selected, cut, n_passed = select([old], {}, cap=5, threshold=0.2, now=NOW)
    assert selected == [old] and not cut and n_passed == 1


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


# --- weight validation ---

def test_sanitize_weights_clamps_floors_and_ceiling():
    names = triage.bucket_names("markets")
    raw = {n: 0.0 for n in names} | {"노이즈": 0.9}
    raw[names[0]] = 1.7
    out = triage.sanitize_weights("markets", raw)
    assert out[names[0]] == 1.0 and out["기타경제"] == 0.4 and out["노이즈"] == 0.1
    assert set(out) == set(names)


def test_sanitize_weights_rejects_missing_bucket():
    with pytest.raises(ValueError):
        triage.sanitize_weights("tech", {"기타기술": 0.5})


def test_sanitize_weights_drops_unknown_keys_and_rejects_non_numbers():
    names = triage.bucket_names("tech")
    raw = {n: 0.5 for n in names} | {"없는버킷": 0.9}
    assert "없는버킷" not in triage.sanitize_weights("tech", raw)
    with pytest.raises(ValueError):
        triage.sanitize_weights("tech", raw | {names[0]: "높음"})


def test_bucket_names_is_category_buckets_plus_noise():
    assert triage.bucket_names("tech")[-1] == NOISE_BUCKET
    assert "기타기술" in triage.bucket_names("tech")
    assert "기타경제" not in triage.bucket_names("tech")


def test_axis_dict_has_axis_only():
    data = triage.axis_dict()
    assert set(data["buckets"]) == {"tech", "markets"}
    assert data["noise_bucket"] == NOISE_BUCKET
    assert "weights_files" not in data
