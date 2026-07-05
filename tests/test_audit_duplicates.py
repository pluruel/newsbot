from unittest.mock import MagicMock, patch

import scripts.audit_duplicates as script


def _e(name, label="Company", aliases=None, mention_count=1):
    return {"name": name, "label": label, "aliases": aliases or [],
            "mention_count": mention_count}


def test_surface_collision_pairs_by_name():
    ents = [_e("NVIDIA"), _e("Nvidia"), _e("Tesla")]
    assert script._surface_collision_pairs(ents) == [(0, 1)]


def test_surface_collision_pairs_by_alias():
    ents = [_e("SK hynix", aliases=["에스케이하이닉스"]),
            _e("SK하이닉스", aliases=["에스케이하이닉스"])]
    assert script._surface_collision_pairs(ents) == [(0, 1)]


def test_surface_collision_no_pair_when_distinct():
    assert script._surface_collision_pairs([_e("A"), _e("B")]) == []


def test_parse_event_date():
    assert str(script._parse_event_date("Claude Fable 5 출시 2026-06-09")) == "2026-06-09"
    assert script._parse_event_date("no date here") is None


def test_event_tokens_drops_date():
    toks = script._event_tokens("Mythos 5 발표 2026-06-09")
    assert "20260609" not in toks
    assert script._normalize("Mythos") in toks


def test_event_pairs_matches_close_dates_and_shared_token():
    events = [
        _e("Claude Fable 5 출시 2026-06-09", label="Event"),
        _e("Claude Fable 5 발표 2026-06-10", label="Event"),  # 1 day apart, shared tokens
        _e("Iran strike 2026-06-30", label="Event"),          # unrelated
    ]
    assert script._event_pairs(events) == [(0, 1)]


def test_event_pairs_rejects_far_dates():
    events = [
        _e("Fable 5 출시 2026-06-09", label="Event"),
        _e("Fable 5 출시 2026-06-20", label="Event"),  # 11 days apart
    ]
    assert script._event_pairs(events) == []


def test_directed_picks_higher_mention_as_survivor():
    low = _e("Citi", label="Institution", mention_count=3)
    high = _e("Citigroup", label="Company", mention_count=40)
    from_e, to_e = script._directed(low, high)
    assert from_e["name"] == "Citi"
    assert to_e["name"] == "Citigroup"


def test_confirm_pairs_parses_same_diff():
    cands = [
        {"from": _e("Citi"), "to": _e("Citigroup"), "reason": "x"},
        {"from": _e("Apple"), "to": _e("Apricot"), "reason": "x"},
    ]
    with patch("scripts.audit_duplicates.run_claude", return_value="P1: SAME\nP2: DIFF"):
        confirmed = script._confirm_pairs(cands)
    assert confirmed == {0}


def test_confirm_pairs_returns_none_on_failure():
    from newsparser.claude.runner import ClaudeError
    cands = [{"from": _e("A"), "to": _e("B"), "reason": "x"}]
    with patch("scripts.audit_duplicates.run_claude", side_effect=ClaudeError("boom")):
        assert script._confirm_pairs(cands) is None


def test_audit_marks_unconfirmed_without_llm():
    def fake_fetch(session, labels):
        if labels == script._ORG_GROUP:
            return [_e("NVIDIA"), _e("Nvidia")]
        return []

    session = MagicMock()
    with patch("scripts.audit_duplicates.fetch_entities", side_effect=fake_fetch):
        out = script.audit(session, use_llm=False)
    assert len(out) == 1
    assert out[0]["verdict"] == "unconfirmed"
    assert out[0]["reason"].startswith("surface-collision")


def test_audit_confirms_via_llm():
    def fake_fetch(session, labels):
        if labels == script._ORG_GROUP:
            # shares the normalized alias "citigroup" → surfaced by rule (a)
            return [_e("Citi", label="Institution", aliases=["Citigroup"], mention_count=3),
                    _e("Citigroup", label="Company", mention_count=40)]
        return []

    session = MagicMock()
    with patch("scripts.audit_duplicates.fetch_entities", side_effect=fake_fetch), \
         patch("scripts.audit_duplicates._confirm_pairs", return_value={0}):
        out = script.audit(session, use_llm=True)
    assert out[0]["verdict"] == "confirmed"
    assert out[0]["from"]["name"] == "Citi"
    assert out[0]["to"]["name"] == "Citigroup"
