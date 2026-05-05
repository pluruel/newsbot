from collections import defaultdict
from datetime import datetime, timedelta

WINDOW_MINUTES = 15
CONVERGENCE_MIN_SOURCES = 3
JACCARD_THRESHOLD = 0.15
SPIKE_MULTIPLIER = 3.0


def _tokenize(title: str) -> set[str]:
    return {w.lower() for w in title.split() if len(w) > 2}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def detect_convergence(articles: list[dict]) -> list[list[dict]]:
    """Return clusters of articles from 3+ sources with overlapping titles within 15 min."""
    cutoff = datetime.utcnow() - timedelta(minutes=WINDOW_MINUTES)
    window = [
        a for a in articles
        if datetime.fromisoformat(a["fetched_at"]) > cutoff
    ]

    clusters: list[list[dict]] = []
    used: set[int] = set()

    for i, a in enumerate(window):
        if i in used:
            continue
        tokens_a = _tokenize(a["title"])
        cluster = [a]

        for j, b in enumerate(window):
            if j <= i or j in used or b["source"] == a["source"]:
                continue
            if _jaccard(tokens_a, _tokenize(b["title"])) >= JACCARD_THRESHOLD:
                cluster.append(b)
                used.add(j)

        sources_in_cluster = {x["source"] for x in cluster}
        if len(sources_in_cluster) >= CONVERGENCE_MIN_SOURCES:
            used.add(i)
            clusters.append(cluster)

    return clusters


def detect_spike(articles: list[dict], baseline: dict[str, float]) -> list[str]:
    """Return sources whose article count in the last hour exceeds SPIKE_MULTIPLIER × baseline."""
    cutoff = datetime.utcnow() - timedelta(hours=1)
    recent = [a for a in articles if datetime.fromisoformat(a["fetched_at"]) > cutoff]

    count_by_source: dict[str, int] = defaultdict(int)
    for a in recent:
        count_by_source[a["source"]] += 1

    return [
        source for source, count in count_by_source.items()
        if count >= baseline.get(source, 5.0) * SPIKE_MULTIPLIER
    ]
