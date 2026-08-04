from unittest.mock import patch
import pytest

from newsparser.classifier import (
    classify_article, classify_query, CATEGORIES, _normalize_article_response, _normalize_query_response,
)


def test_categories_constant():
    assert CATEGORIES == ("tech", "markets")


def test_normalize_article_response_accepts_exact():
    assert _normalize_article_response("tech") == "tech"
    assert _normalize_article_response("markets") == "markets"


def test_normalize_article_response_strips_whitespace_and_punctuation():
    assert _normalize_article_response(" tech\n") == "tech"
    assert _normalize_article_response("tech.") == "tech"


def test_normalize_article_response_falls_back_to_markets_on_garbage():
    assert _normalize_article_response("maybe both?") == "markets"
    assert _normalize_article_response("") == "markets"


def test_normalize_query_response_accepts_three_values():
    assert _normalize_query_response("tech") == "tech"
    assert _normalize_query_response("markets") == "markets"
    assert _normalize_query_response("both") == "both"


def test_normalize_query_response_falls_back_to_both_on_garbage():
    assert _normalize_query_response("???") == "both"
    assert _normalize_query_response("") == "both"


def test_classify_article_calls_haiku_and_returns_tech():
    with patch("newsparser.classifier.ask_haiku", return_value="tech") as mock:
        result = classify_article("OpenAI launches GPT-X", "Body about model release")
    assert result == "tech"
    args, kwargs = mock.call_args
    assert kwargs.get("timeout") == 15
    prompt = args[0]
    assert "OpenAI launches GPT-X" in prompt
    assert "Body about model release" in prompt


def test_classify_article_falls_back_to_markets_on_subprocess_error():
    with patch("newsparser.classifier.ask_haiku", side_effect=RuntimeError("boom")):
        assert classify_article("x", "y") == "markets"


def test_classify_article_truncates_long_body():
    long_body = "x" * 5000
    captured = {}
    def fake(prompt, *a, **kw):
        captured["prompt"] = prompt
        return "markets"
    with patch("newsparser.classifier.ask_haiku", side_effect=fake):
        classify_article("title", long_body)
    # Body excerpt should be capped — entire 5000-char body MUST NOT be in prompt
    assert "x" * 5000 not in captured["prompt"]


def test_classify_query_returns_both_for_cross_category():
    with patch("newsparser.classifier.ask_haiku", return_value="both"):
        assert classify_query("AI 발표가 NVDA 주가에 미친 영향") == "both"


def test_classify_query_falls_back_to_both_on_error():
    with patch("newsparser.classifier.ask_haiku", side_effect=RuntimeError("boom")):
        assert classify_query("hello") == "both"
