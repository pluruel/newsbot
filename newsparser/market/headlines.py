"""Pick the headlines that plausibly explain a price move.

Runs only when a volatility alert fires (~3 times a day), so one haiku call per
invocation is affordable. Two properties matter more than recall here:

* **Untrusted input.** Headlines are scraped web content. The call gets no tools
  and permission_mode="default", exactly like classifier.py, and the model is
  asked for *indices only* — the message is rendered from the DB rows those
  indices point at, never from model prose. Same rule run_cycle.py:304 follows
  for the cycle digest.
* **Asymmetric window.** News moves price, so the cause sits *before* the bar.
  The poller also lags reality by up to one poll interval, so the window opens
  well before the bar and closes at "now" rather than at the bar's end.
"""
import logging
import re
from datetime import datetime, timedelta, timezone

from newsparser.claude.haiku import ask_haiku
from newsparser.claude.runner import ClaudeError
from newsparser.collector.alert import _jaccard, _tokenize
from newsparser.store.sqlite import get_between

logger = logging.getLogger(__name__)

_MAX_TOKENS = 32

# Cause precedes effect: reach back well past the bar's open, and forward only
# to "now" (which already trails the bar by the feed delay).
WINDOW_BEFORE_MIN = 30
MAX_CANDIDATES = 40
MAX_PICKS = 3
# Same threshold detect_convergence uses to call two headlines the same story.
DEDUP_THRESHOLD = 0.15

_SYSTEM_PROMPT = (
    "You are a headline selector. Reply with only comma-separated numbers, "
    "or the single word none. No explanations, no punctuation."
)

_PROMPT = (
    "{display} {interval} 변동: {delta}\n"
    "구간: {window} KST\n\n"
    "아래는 그 무렵 수집된 경제·시장 기사 제목이다. "
    "이 가격 움직임을 설명할 만한 것을 최대 {k}개 골라 번호만 쉼표로 답해라.\n"
    "설명할 만한 게 없으면 정확히 none 이라고만 답해라. "
    "억지로 고르지 마라 — 관련 없는 기사를 고르는 것보다 none이 낫다.\n\n"
    "{numbered}"
)


def _dedupe(articles: list[dict]) -> list[dict]:
    """Collapse the same story filed by several outlets, keeping the earliest.

    Korean wire feeds (매일경제/한국경제/연합인포맥스) republish the same story
    within minutes; without this the candidate list is mostly duplicates and the
    picks come back as three copies of one headline.
    """
    kept: list[dict] = []
    kept_tokens: list[set] = []
    for a in articles:
        tokens = _tokenize(a["title"])
        if any(_jaccard(tokens, seen) >= DEDUP_THRESHOLD for seen in kept_tokens):
            continue
        kept.append(a)
        kept_tokens.append(tokens)
    return kept


def _parse_picks(raw: str, n: int) -> list[int]:
    """Extract 1-based indices from the model reply, dropping anything out of
    range. Returns [] for 'none' or unparseable output — a wrong pick is worse
    than no pick, so this never guesses."""
    text = (raw or "").strip().lower()
    if not text or text.startswith("none"):
        return []
    seen: list[int] = []
    for token in re.findall(r"\d+", text):
        idx = int(token)
        if 1 <= idx <= n and idx not in seen:
            seen.append(idx)
    return seen[:MAX_PICKS]


def candidates(bar_start: datetime, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    start = bar_start - timedelta(minutes=WINDOW_BEFORE_MIN)
    rows = get_between(start, now, category="markets", limit=MAX_CANDIDATES * 3)
    # Newest-first before the cap: when a burst overflows MAX_CANDIDATES, the
    # headlines nearest the move are the ones worth keeping.
    return _dedupe(list(reversed(rows)))[:MAX_CANDIDATES]


def select(display: str, interval: str, delta_label: str, window_label: str,
           articles: list[dict]) -> list[dict]:
    """Return the subset of `articles` the model flags as explaining the move.
    Falls back to [] on any error — the alert still goes out with its price
    line, just without a headline section."""
    if not articles:
        return []
    numbered = "\n".join(f"{i}. {a['title']}" for i, a in enumerate(articles, 1))
    prompt = _PROMPT.format(display=display, interval=interval, delta=delta_label,
                            window=window_label, k=MAX_PICKS, numbered=numbered)
    try:
        raw = ask_haiku(prompt, _SYSTEM_PROMPT, _MAX_TOKENS, timeout=30)
    except (ClaudeError, RuntimeError, OSError) as exc:
        logger.warning("headline select failed (%s) — sending price line only", exc)
        return []
    return [articles[i - 1] for i in _parse_picks(raw, len(articles))]
