import pytest
from datetime import datetime, timedelta, timezone
from newsparser.collector.alert import detect_convergence, detect_spike, _jaccard, _tokenize

def test_jaccard_overlap():
    assert _jaccard({"fed", "rate", "cut"}, {"fed", "rate", "hike"}) == pytest.approx(0.5)

def test_jaccard_no_overlap():
    assert _jaccard({"apple", "earnings"}, {"fed", "rate"}) == 0.0

def test_jaccard_empty():
    assert _jaccard(set(), {"fed"}) == 0.0

def test_tokenize_lowercases_and_filters_short():
    tokens = _tokenize("Fed cuts rates NOW")
    assert "fed" in tokens
    assert "cuts" in tokens
    assert "rates" in tokens
    assert "now" in tokens  # len==3 — included
    assert "to" not in tokens  # len==2 — excluded

def test_detect_convergence_finds_cluster():
    now = datetime.now(timezone.utc)
    articles = [
        {"guid": "1", "source": "Reuters",      "title": "Fed cuts rates emergency meeting", "fetched_at": now.isoformat()},
        {"guid": "2", "source": "매일경제",      "title": "Fed cuts rates emergency", "fetched_at": now.isoformat()},
        {"guid": "3", "source": "Financial Times","title": "Fed emergency rate cut announced", "fetched_at": now.isoformat()},
    ]
    clusters = detect_convergence(articles)
    assert len(clusters) == 1
    assert len(clusters[0]) == 3

def test_detect_convergence_ignores_single_source():
    now = datetime.now(timezone.utc)
    articles = [
        {"guid": "1", "source": "Reuters", "title": "Fed cuts rates", "fetched_at": now.isoformat()},
        {"guid": "2", "source": "Reuters", "title": "Fed rate decision", "fetched_at": now.isoformat()},
    ]
    clusters = detect_convergence(articles)
    assert len(clusters) == 0

def test_detect_convergence_ignores_old_articles():
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    articles = [
        {"guid": "1", "source": "Reuters",  "title": "Fed cuts rates emergency", "fetched_at": old},
        {"guid": "2", "source": "매일경제", "title": "Fed cuts rates emergency", "fetched_at": now},
        {"guid": "3", "source": "FT",       "title": "Fed emergency cut rates", "fetched_at": now},
    ]
    clusters = detect_convergence(articles)
    assert len(clusters) == 0

def test_detect_spike_triggers_on_burst():
    now = datetime.now(timezone.utc)
    articles = [
        {"source": "Reuters", "title": f"Article {i}", "fetched_at": (now - timedelta(minutes=i)).isoformat()}
        for i in range(20)  # 20 articles in last hour
    ]
    baseline = {"Reuters": 5.0}  # avg 5/hour
    spiking = detect_spike(articles, baseline)
    assert "Reuters" in spiking

def test_detect_spike_no_trigger_normal_volume():
    now = datetime.now(timezone.utc)
    articles = [
        {"source": "Reuters", "title": f"Article {i}", "fetched_at": (now - timedelta(minutes=i*10)).isoformat()}
        for i in range(4)  # 4 articles in last hour
    ]
    baseline = {"Reuters": 5.0}
    spiking = detect_spike(articles, baseline)
    assert "Reuters" not in spiking
