import logging
from datetime import date

from newsparser.scheduler import reports


def test_strip_graph_block_keeps_triage_stats():
    text = "새 소식\n• a\n\n## Graph updates\n### Entities\n- NEW | X\n\n\n트리아지: 후보 3건 · threshold 0.30 통과 2건\n"
    out = reports.strip_graph_block(text)
    assert "Graph updates" not in out and "NEW | X" not in out
    assert "트리아지: 후보 3건" in out and "• a" in out


def test_previous_report_skips_current_and_later_slots(tmp_path):
    d = tmp_path / "cycles" / "tech"; d.mkdir(parents=True)
    for s in ("2026-05-08-09", "2026-05-08-12", "2026-05-08-15"):
        (d / f"{s}.md").write_text(s)
    assert reports.previous_report(tmp_path, "tech", "2026-05-08-12").stem == "2026-05-08-09"
    assert reports.previous_report(tmp_path, "tech", "2026-05-08-09") is None


def test_reports_between_filters_dates_and_drops_oldest_over_budget(tmp_path, caplog):
    d = tmp_path / "cycles" / "tech"; d.mkdir(parents=True)
    for s in ("2026-05-01-09", "2026-05-07-09", "2026-05-08-09"):
        (d / f"{s}.md").write_text(f"본문 {s}\n\n## Graph updates\n- NEW | X\n", encoding="utf-8")
    # cycles/weekly/ is not a category folder and must never be picked up
    w = tmp_path / "cycles" / "weekly"; w.mkdir()
    (w / "2026-05-08.md").write_text("주간", encoding="utf-8")

    got = reports.reports_between(tmp_path, ["tech"], date(2026, 5, 2), date(2026, 5, 8))
    assert [k for k, _ in got] == ["tech/2026-05-07-09", "tech/2026-05-08-09"]
    assert all("Graph updates" not in t for _, t in got)

    one_file = len(got[1][1])
    with caplog.at_level(logging.WARNING):
        got = reports.reports_between(
            tmp_path, ["tech"], date(2026, 5, 2), date(2026, 5, 8), max_chars=one_file
        )
    assert [k for k, _ in got] == ["tech/2026-05-08-09"]
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_reports_between_orders_across_categories_by_time(tmp_path):
    for cat, slot in (("tech", "2026-05-08-12"), ("markets", "2026-05-08-09")):
        d = tmp_path / "cycles" / cat; d.mkdir(parents=True)
        (d / f"{slot}.md").write_text(slot, encoding="utf-8")
    got = reports.reports_between(tmp_path, ["tech", "markets"], date(2026, 5, 8), date(2026, 5, 8))
    assert [k for k, _ in got] == ["markets/2026-05-08-09", "tech/2026-05-08-12"]
