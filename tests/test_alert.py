import pytest
from datetime import datetime, timedelta
from newsparser.collector.alert import detect_convergence, detect_spike, _jaccard

def test_jaccard_overlap():
    assert _jaccard({"fed", "rate", "cut"}, {"fed", "rate", "hike"}) == pytest.approx(0.5)

def test_jaccard_no_overlap():
    assert _jaccard({"apple", "earnings"}, {"fed", "rate"}) == 0.0

def test_jaccard_empty():
    assert _jaccard(set(), {"fed"}) == 0.0

def test_detect_convergence_finds_cluster():
    now = datetime.utcnow()
    articles = [
        {"guid": "1", "source": "Reuters",      "title": "Fed cuts rates emergency meeting", "fetched_at": now.isoformat()},
        {"guid": "2", "source": "매일경제",      "title": "Fed cuts rates emergency", "fetched_at": now.isoformat()},
        {"guid": "3", "source": "Financial Times","title": "Fed emergency rate cut announced", "fetched_at": now.isoformat()},
    ]
    clusters = detect_convergence(articles)
    assert len(clusters) == 1
    assert len(clusters[0]) == 3

def test_detect_convergence_ignores_single_source():
    now = datetime.utcnow()
    articles = [
        {"guid": "1", "source": "Reuters", "title": "Fed cuts rates", "fetched_at": now.isoformat()},
        {"guid": "2", "source": "Reuters", "title": "Fed rate decision", "fetched_at": now.isoformat()},
    ]
    clusters = detect_convergence(articles)
    assert len(clusters) == 0

def test_detect_convergence_ignores_old_articles():
    old = (datetime.utcnow() - timedelta(minutes=20)).isoformat()
    now = datetime.utcnow().isoformat()
    articles = [
        {"guid": "1", "source": "Reuters",  "title": "Fed cuts rates emergency", "fetched_at": old},
        {"guid": "2", "source": "매일경제", "title": "Fed cuts rates emergency", "fetched_at": now},
        {"guid": "3", "source": "FT",       "title": "Fed emergency cut rates", "fetched_at": now},
    ]
    clusters = detect_convergence(articles)
    assert len(clusters) == 0

def test_detect_spike_triggers_on_burst():
    now = datetime.utcnow()
    articles = [
        {"source": "Reuters", "fetched_at": (now - timedelta(minutes=i)).isoformat()}
        for i in range(20)  # 20 articles in last hour
    ]
    baseline = {"Reuters": 5.0}  # avg 5/hour
    spiking = detect_spike(articles, baseline)
    assert "Reuters" in spiking

def test_detect_spike_no_trigger_normal_volume():
    now = datetime.utcnow()
    articles = [
        {"source": "Reuters", "fetched_at": (now - timedelta(minutes=i*10)).isoformat()}
        for i in range(4)  # 4 articles in last hour
    ]
    baseline = {"Reuters": 5.0}
    spiking = detect_spike(articles, baseline)
    assert "Reuters" not in spiking
