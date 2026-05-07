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
