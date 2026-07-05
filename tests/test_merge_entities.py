from unittest.mock import MagicMock, patch

import pytest

import scripts.merge_entities as script


def _pair(fn="Citi", fl="Institution", tn="Citigroup", tl="Company"):
    return {"from_name": fn, "from_label": fl, "to_name": tn, "to_label": tl}


def test_validate_pair_ok():
    assert script._validate_pair(_pair()) == ("Citi", "Institution", "Citigroup", "Company")


def test_validate_pair_rejects_missing_keys():
    with pytest.raises(ValueError):
        script._validate_pair({"from_name": "A"})


def test_validate_pair_rejects_bad_label():
    with pytest.raises(ValueError):
        script._validate_pair(_pair(fl="Company; DROP"))


def test_validate_pair_rejects_self_merge():
    with pytest.raises(ValueError):
        script._validate_pair(_pair(fn="X", fl="Company", tn="X", tl="Company"))


def test_rel_types_rejects_suspicious_type():
    session = MagicMock()
    session.run.return_value = [{"t": "ANNOUNCED"}, {"t": "bad type"}]
    with pytest.raises(ValueError):
        script._rel_types(session, "Citi", "Institution")


def test_merge_pair_dry_run_does_not_mutate():
    session = MagicMock()
    session.run.return_value.single.return_value = {
        "from_exists": 1, "to_exists": 1, "rel_count": 5}
    summary = script.merge_pair(session, _pair(), apply=False)
    assert summary["applied"] is False
    assert summary["would_move_rels"] == 5
    # only the read-only _describe query ran — no DETACH DELETE / MERGE
    joined = " ".join(str(c.args[0]) for c in session.run.call_args_list)
    assert "DETACH DELETE" not in joined
    assert "MERGE" not in joined


def test_merge_pair_skips_when_from_missing():
    session = MagicMock()
    session.run.return_value.single.return_value = {
        "from_exists": 0, "to_exists": 1, "rel_count": 0}
    summary = script.merge_pair(session, _pair(), apply=True)
    assert summary["applied"] is False
    assert "from node not found" in summary["skipped"]


def test_merge_pair_apply_moves_rels_and_merges_node():
    session = MagicMock()
    # _describe → both exist; _rel_types → one type; move/merge queries → ignored
    describe = MagicMock()
    describe.single.return_value = {"from_exists": 1, "to_exists": 1, "rel_count": 3}
    rel_types = [{"t": "ANNOUNCED"}]

    calls = {"n": 0}

    def fake_run(query, **kw):
        calls["n"] += 1
        if "count(DISTINCT f)" in query:
            return describe
        if "DISTINCT type(r)" in query:
            return rel_types
        return MagicMock()

    session.run.side_effect = fake_run
    summary = script.merge_pair(session, _pair(), apply=True)
    assert summary["applied"] is True
    joined = " ".join(str(c.args[0]) for c in session.run.call_args_list)
    assert "DETACH DELETE f" in joined
    assert ":ANNOUNCED" in joined


def test_merge_all_records_error_for_bad_pair():
    fake_session = MagicMock()
    fake_session.run.return_value.single.return_value = {
        "from_exists": 1, "to_exists": 1, "rel_count": 0}
    fake_driver = MagicMock()
    fake_driver.session.return_value.__enter__.return_value = fake_session
    with patch("scripts.merge_entities.get_driver", return_value=fake_driver):
        out = script.merge_all([{"from_name": "A"}], apply=False)
    assert "error" in out[0]
