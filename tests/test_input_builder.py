import os
import pytest
from newsparser.store.sqlite import init_db, insert_article
from newsparser.claude.input_builder import build_input_file


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    init_db()


def test_build_input_file_writes_under_category_subfolder(tmp_path):
    insert_article("g1", "TechCrunch AI", "Model release", "https://x.com/1", None, "body", category="tech")
    path = build_input_file("2026-05-05-00", "tech")
    assert path.parent.name == "tech"
    assert path.name == "2026-05-05-00-input.md"
    assert path.exists()


def test_build_input_file_only_includes_matching_category(tmp_path):
    insert_article("g1", "TechCrunch AI", "Model release", "https://x.com/1", None, "tech body", category="tech")
    insert_article("g2", "FT", "Fed cuts", "https://x.com/2", None, "markets body", category="markets")
    tech_path = build_input_file("2026-05-05-00", "tech")
    markets_path = build_input_file("2026-05-05-00", "markets")
    assert "tech body" in tech_path.read_text()
    assert "markets body" not in tech_path.read_text()
    assert "markets body" in markets_path.read_text()
    assert "tech body" not in markets_path.read_text()


def test_build_input_file_marks_category_in_header(tmp_path):
    insert_article("g1", "TechCrunch AI", "T", "https://x.com/1", None, "b", category="tech")
    path = build_input_file("2026-05-05-00", "tech")
    content = path.read_text()
    assert "# Input 2026-05-05-00 KST [tech]" in content


def test_build_input_file_zero_articles_for_category(tmp_path):
    path = build_input_file("2026-05-05-00", "tech")
    assert "0 total" in path.read_text()


def test_build_input_file_assigns_A_indices(tmp_path):
    insert_article("g1", "Bloomberg", "T1", "https://x.com/1", None, "b1", category="markets")
    insert_article("g2", "FT", "T2", "https://x.com/2", None, "b2", category="markets")
    insert_article("g3", "AP", "T3", "https://x.com/3", None, "b3", category="markets")
    path = build_input_file("2026-05-09-12", "markets")
    text = path.read_text()
    assert "[A001]" in text
    assert "[A002]" in text
    assert "[A003]" in text


def test_build_input_file_emits_guid_lines(tmp_path):
    insert_article("guid-abc", "Bloomberg", "T", "https://x.com/1", None, "b", category="markets")
    path = build_input_file("2026-05-09-12", "markets")
    text = path.read_text()
    assert "- GUID: guid-abc" in text


def test_index_order_matches_db_order(tmp_path):
    # The index order in the input file must match the order get_unprocessed returns,
    # which is what {slot}-guids.txt is written from in run_cycle.py.
    insert_article("g-first", "Bloomberg", "T1", "u1", "2026-05-09T01:00:00Z", "b", category="markets")
    insert_article("g-second", "FT", "T2", "u2", "2026-05-09T02:00:00Z", "b", category="markets")
    path = build_input_file("2026-05-09-12", "markets")
    text = path.read_text()
    a001 = text.index("[A001]")
    a002 = text.index("[A002]")
    g_first = text.index("g-first")
    g_second = text.index("g-second")
    assert a001 < g_first < a002 < g_second


def test_build_input_file_uses_supplied_articles(tmp_path):
    """run_cycle.py passes the article list it already claimed, so the input file
    and {slot}-guids.txt can't describe different sets."""
    insert_article("g1", "Bloomberg", "T1", "u1", None, "body one", category="markets")
    insert_article("g2", "FT", "T2", "u2", None, "body two", category="markets")

    from newsparser.store.sqlite import get_unprocessed
    only_first = get_unprocessed(category="markets")[:1]

    path = build_input_file("2026-05-09-12", "markets", articles=only_first)
    text = path.read_text()
    assert "1 total" in text
    assert "body one" in text
    assert "body two" not in text
