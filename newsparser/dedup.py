"""Collapse re-reported stories before a cycle sees them.

Korean wires and Bloomberg re-file the same story across outlets/feeds within
hours, so the CYCLE_MAX_ARTICLES cap fills with copies of events the run — or
the previous run — already covers. This marks those copies processed (with
`duplicate_of` pointing at the kept row) before the input file is built, so
the cap is spent on distinct events.

Dropping a real article is worse than analyzing a duplicate twice, so the
match rule is deliberately much stricter than headlines._dedupe's 0.15
threshold (there a false merge only shortens a candidate list for a prompt;
here it deletes the article from every future report). Tuned against the 45k
stored articles in the dev DB: 0.6 plain jaccard merged distinct stories
("Peter Neumann has died" / "Peter Salus has died"), while the rules below
collapsed ~1% of volume with no false positive in a manual review of the
full drop list.
"""
import logging
import re
from datetime import datetime, timedelta

from newsparser.store.sqlite import (
    article_ts,
    get_processed_since,
    get_unprocessed,
    init_db,
    mark_duplicates,
)

logger = logging.getLogger(__name__)

# Cross-outlet copies of one story arrive within hours; beyond two days a
# similar headline is far more likely a follow-up than a re-file.
WINDOW_HOURS = 48

# Two ways to be "the same story":
#  * near-identical wording (quote-style/punctuation variants, re-files) — high
#    jaccard on its own is enough;
#  * a title *extension* — one token set contains the other ("[속보] " prefix,
#    " | Odd Lots" suffix, a truncated re-file) — where jaccard alone dips too
#    low but the containment makes the relation unambiguous.
JACCARD_SAME = 0.85
JACCARD_CONTAINED = 0.6
# Containment over a tiny set proves nothing ("[표] 날씨" ⊂ "[표] 오늘의 날씨").
MIN_CONTAINED_TOKENS = 4
# Below 3 tokens jaccard is degenerate — a 2-token title can only score 0, .5
# or 1 — so short titles are never dedup candidates at all.
MIN_TOKENS = 3

_EDGE_PUNCT_RE = re.compile(r"^\W+|\W+$", re.UNICODE)


def _tokenize(title: str) -> frozenset[str]:
    """Meaning-bearing tokens of a headline.

    Unlike alert._tokenize (English-oriented, len>2), this strips edge
    punctuation ("lots:" == "lots"), keeps 2-char Hangul words (인사/구속 —
    most Korean nouns are two syllables, and dropping them collapsed 한겨레's
    distinct daily columns to the shared date token), and keeps any
    digit-bearing token ("May 7" vs "May 8" is all that separates a daily
    series' episodes).
    """
    tokens = set()
    for word in title.split():
        word = _EDGE_PUNCT_RE.sub("", word.lower())
        if not word:
            continue
        if (len(word) > 2
                or any(ch.isdigit() for ch in word)
                or (len(word) == 2 and any("가" <= ch <= "힣" for ch in word))):
            tokens.add(word)
    return frozenset(tokens)


def _is_duplicate(a: frozenset, b: frozenset) -> bool:
    if min(len(a), len(b)) < MIN_TOKENS:
        return False
    jaccard = len(a & b) / len(a | b)
    if jaccard >= JACCARD_SAME:
        return True
    return (jaccard >= JACCARD_CONTAINED
            and min(len(a), len(b)) >= MIN_CONTAINED_TOKENS
            and (a <= b or b <= a))


def dedupe_pending(category: str) -> int:
    """Mark pending articles that duplicate another pending article — or one
    processed within the window — as processed. Returns how many were marked.

    Within a pending-only cluster the copy with the longest body survives (a
    paywalled stub loses to the full-text version of the same story); a
    cluster that matches an already-processed article keeps nothing — the
    story was analyzed last cycle.
    """
    init_db()  # older deployments may predate the duplicate_of column
    pending = get_unprocessed(category=category)
    if not pending:
        return 0

    articles = [{**row, "ts": article_ts(row), "tokens": _tokenize(row["title"]),
                 "frozen": False} for row in pending]
    # Stories analyzed in a recent cycle still absorb copies arriving now —
    # that inter-cycle re-reporting is the bulk of the duplication.
    since = (min(a["ts"] for a in articles) - timedelta(hours=WINDOW_HOURS)).isoformat()
    for row in get_processed_since(category, since):
        articles.append({**row, "ts": article_ts(row), "tokens": _tokenize(row["title"]),
                         "frozen": True})
    articles.sort(key=lambda a: a["ts"])

    window = timedelta(hours=WINDOW_HOURS)
    # Only cluster-founding articles anchor comparisons — no chaining, so every
    # drop is provably within WINDOW_HOURS of the article that absorbed it.
    anchors: list[tuple[datetime, frozenset, dict]] = []
    clusters: list[dict] = []
    for art in articles:
        matched = None
        for ts, tokens, cluster in reversed(anchors):
            if art["ts"] - ts > window:
                break
            if _is_duplicate(art["tokens"], tokens):
                matched = cluster
                break
        if matched is None:
            cluster = {"members": [], "processed_guid": None}
            clusters.append(cluster)
            anchors.append((art["ts"], art["tokens"], cluster))
            matched = cluster
        if art["frozen"]:
            matched["processed_guid"] = matched["processed_guid"] or art["guid"]
        else:
            matched["members"].append(art)

    dropped: list[tuple[str, str]] = []  # (duplicate guid, kept guid)
    for cluster in clusters:
        members = cluster["members"]
        if cluster["processed_guid"] is not None:
            dropped.extend((m["guid"], cluster["processed_guid"]) for m in members)
            continue
        if len(members) < 2:
            continue
        kept = max(members, key=lambda m: len(m["body"] or ""))
        dropped.extend((m["guid"], kept["guid"]) for m in members if m is not kept)

    if dropped:
        mark_duplicates(dropped)
        for dup_guid, kept_guid in dropped:
            logger.info("[%s] duplicate collapsed: %s -> %s", category, dup_guid, kept_guid)
    return len(dropped)
