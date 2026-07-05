from unittest.mock import MagicMock, patch

import scripts.apply_renames as script


def _session(count_by_name):
    """Session whose count(e) query returns count_by_name[name]; SET is a no-op."""
    def run(query, **kw):
        res = MagicMock()
        if "count(e)" in query:
            res.single.return_value = {"c": count_by_name.get(kw.get("name"), 0)}
        else:
            res.single.return_value = {}
        return res
    s = MagicMock()
    s.run.side_effect = run
    return s


def test_rename_dry_run_reports_without_mutating():
    s = _session({"SpaceX_나스닥_상장": 1})  # source exists, target absent
    out = script.rename_node(s, {"name": "SpaceX_나스닥_상장", "label": "Event",
                                 "new_name": "SpaceX 나스닥 상장"}, apply=False)
    assert out["applied"] is False and out.get("would_rename") is True
    joined = " ".join(str(c.args[0]) for c in s.run.call_args_list)
    assert "SET" not in joined


def test_rename_skips_when_source_missing():
    out = script.rename_node(_session({}), {"name": "X", "label": "Event", "new_name": "Y"}, apply=True)
    assert out["applied"] is False and "not found" in out["skipped"]


def test_rename_skips_on_target_collision():
    out = script.rename_node(_session({"X": 1, "Y": 1}),
                             {"name": "X", "label": "Event", "new_name": "Y"}, apply=True)
    assert out["applied"] is False and "already exists" in out["skipped"]


def test_rename_skips_when_name_unchanged():
    out = script.rename_node(_session({"X": 1}),
                             {"name": "X", "label": "Event", "new_name": "X"}, apply=True)
    assert out["skipped"] == "name unchanged"


def test_rename_apply_sets_name_and_keeps_old_as_alias():
    s = _session({"X": 1})  # source exists, target absent
    out = script.rename_node(s, {"name": "X", "label": "Event", "new_name": "Y"}, apply=True)
    assert out["applied"] is True
    setq = [c for c in s.run.call_args_list if "SET" in str(c.args[0])]
    assert setq, "expected a SET query"
    q = str(setq[0].args[0])
    assert "e.canonical_name = $new" in q and "e.aliases" in q


def test_apply_renames_records_error_for_bad_entry():
    fake_driver = MagicMock()
    fake_driver.session.return_value.__enter__.return_value = MagicMock()
    with patch("scripts.apply_renames.get_driver", return_value=fake_driver):
        out = script.apply_renames([{"name": "X"}], apply=False)  # missing keys
    assert "error" in out[0]
