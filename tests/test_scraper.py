from unittest.mock import patch
from newsparser.collector.scraper import fetch_body

def test_fetch_body_returns_text_on_success():
    with patch("newsparser.collector.scraper.trafilatura.fetch_url", return_value="<html><body><p>Article text</p></body></html>"), \
         patch("newsparser.collector.scraper.trafilatura.extract", return_value="Article text"):
        result = fetch_body("https://example.com/article")
    assert result == "Article text"

def test_fetch_body_returns_none_on_download_failure():
    with patch("newsparser.collector.scraper.trafilatura.fetch_url", return_value=None):
        result = fetch_body("https://example.com/article")
    assert result is None

def test_fetch_body_returns_none_on_extract_failure():
    with patch("newsparser.collector.scraper.trafilatura.fetch_url", return_value="<html></html>"), \
         patch("newsparser.collector.scraper.trafilatura.extract", return_value=None):
        result = fetch_body("https://example.com/article")
    assert result is None
