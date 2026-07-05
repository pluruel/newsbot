from unittest.mock import MagicMock, patch

import scripts.alias_cleanup as script


def _session(aliases_row):
    """Session whose alias query returns aliases_row (or None for missing node)."""
    def run(query, **kw):
        res = MagicMock()
        if "RETURN coalesce(e.aliases" in query:
            res.single.return_value = aliases_row
        else:
            res.single.return_value = {}
        return res
    s = MagicMock()
    s.run.side_effect = run
    return s


def test_reports_only_present_aliases_dry_run():
    s = _session({"a": ["SpaceX IPO", "SpaceX 6/12 Nasdaq"]})
    out = script.remove_aliases(s, {"name": "SpaceX_나스닥_상장", "label": "Event",
                                    "remove_aliases": ["SpaceX IPO", "not-there"]}, apply=False)
    assert out["removing"] == ["SpaceX IPO"]
    assert out["applied"] is False
    joined = " ".join(str(c.args[0]) for c in s.run.call_args_list)
    assert "SET" not in joined  # dry-run doesn't mutate


def test_skips_when_node_missing():
    out = script.remove_aliases(_session(None), {"name": "X", "label": "Event",
                                                 "remove_aliases": ["z"]}, apply=True)
    assert "not found" in out["skipped"]


def test_skips_when_no_listed_alias_present():
    out = script.remove_aliases(_session({"a": ["keep"]}),
                                {"name": "X", "label": "Event", "remove_aliases": ["gone"]}, apply=True)
    assert out["applied"] is False and "none of the listed" in out["skipped"]


def test_apply_issues_set_filtering_aliases():
    s = _session({"a": ["SpaceX IPO", "keep"]})
    out = script.remove_aliases(s, {"name": "n", "label": "Event",
                                    "remove_aliases": ["SpaceX IPO"]}, apply=True)
    assert out["applied"] is True
    setq = [c for c in s.run.call_args_list if "SET" in str(c.args[0])]
    assert setq and "WHERE NOT a IN $rm" in str(setq[0].args[0])


def test_apply_cleanups_records_error_for_bad_entry():
    fake_driver = MagicMock()
    fake_driver.session.return_value.__enter__.return_value = MagicMock()
    with patch("scripts.alias_cleanup.get_driver", return_value=fake_driver):
        out = script.apply_cleanups([{"name": "X"}], apply=False)  # missing keys
    assert "error" in out[0]
