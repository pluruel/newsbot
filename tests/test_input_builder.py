import os
import pytest
from newsparser.store.sqlite import init_db, insert_article
from newsparser.claude.input_builder import build_input_file

@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    init_db()

def test_build_input_file_creates_markdown(tmp_path):
    insert_article("g1", "Reuters", "Fed cuts rates", "https://reuters.com/1", "2026-05-05T00:00:00", "Full article body here.")
    insert_article("g2", "매일경제", "연준 금리 인하", "https://mk.co.kr/1", "2026-05-05T01:00:00", "기사 본문입니다.")
    path = build_input_file("2026-05-05-00")
    content = path.read_text()
    assert "# Input 2026-05-05-00 KST" in content
    assert "Reuters" in content
    assert "Fed cuts rates" in content
    assert "Full article body here." in content
    assert "매일경제" in content

def test_build_input_file_path(tmp_path):
    insert_article("g1", "Reuters", "Title", "https://example.com", None, "body")
    path = build_input_file("2026-05-05-00")
    assert path.name == "2026-05-05-00-input.md"
    assert path.exists()

def test_build_input_file_empty_when_no_articles(tmp_path):
    path = build_input_file("2026-05-05-00")
    content = path.read_text()
    assert "0 total" in content
