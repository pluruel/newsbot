import logging
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from newsparser.claude.output_parser import RelationUpdate
from newsparser.market import fetcher, store
from newsparser.graph.neo4j_client import get_driver

logger = logging.getLogger(__name__)

_TRACKED = {"IMPACTS", "INFLUENCES"}
_KST = ZoneInfo("Asia/Seoul")
_DAILY_CUSHION_DAYS = 3


def _slot_to_utc(slot: str) -> datetime:
    dt = datetime.strptime(slot, "%Y-%m-%d-%H").replace(tzinfo=_KST)
    return dt.astimezone(timezone.utc)


def _apply_annotation_cypher(
    *,
    subject: str,
    predicate: str,
    obj: str,
    delta_pct: float,
    window_literal: str,
) -> None:
    with get_driver().session() as session:
        session.run(
            "MATCH (a {canonical_name: $subject})-[r:" + predicate + "]->(b {canonical_name: $obj}) "
            "SET r.impact_price_delta_pct = $delta, "
            "    r.impact_price_delta_window = $window, "
            "    r.impact_target_instrument = $obj, "
            "    r.annotated_at = datetime()",
            subject=subject, obj=obj, delta=delta_pct, window=window_literal,
        )


def _intraday_delta(alias: str, slot_utc: datetime) -> float | None:
    bars = fetcher.fetch_intraday_hourly(
        alias,
        slot_utc - timedelta(minutes=60),
        slot_utc + timedelta(minutes=60),
    )
    if not bars:
        return None
    store.upsert_intraday(bars)
    before = [b for b in bars if b["ts"] < slot_utc.isoformat()]
    after = [b for b in bars if b["ts"] >= slot_utc.isoformat()]
    if not before or not after:
        return None
    return (after[0]["close"] - before[-1]["close"]) / before[-1]["close"] * 100


def _daily_delta(alias: str, slot_utc: datetime) -> float | None:
    slot_date = slot_utc.date()
    bars = store.get_daily(
        alias,
        slot_date - timedelta(days=_DAILY_CUSHION_DAYS),
        slot_date + timedelta(days=_DAILY_CUSHION_DAYS),
    )
    prev = [b for b in bars if b["date"] < slot_date.isoformat()]
    event = [b for b in bars if b["date"] >= slot_date.isoformat()]
    if not prev or not event:
        return None
    return (event[0]["close"] - prev[-1]["close"]) / prev[-1]["close"] * 100


def maybe_annotate_impacts(relations: list[RelationUpdate], slot: str, category: str) -> int:
    annotated = 0
    try:
        slot_utc = _slot_to_utc(slot)
    except Exception as exc:
        logger.warning("annotate: invalid slot %r (%s)", slot, exc)
        return 0

    for r in relations:
        try:
            if r.predicate not in _TRACKED:
                continue
            if r.obj not in fetcher.TICKERS:
                continue

            delta = _intraday_delta(r.obj, slot_utc)
            window_literal = "[-60m, +60m]"
            if delta is None:
                delta = _daily_delta(r.obj, slot_utc)
                window_literal = "daily"
            if delta is None:
                continue

            _apply_annotation_cypher(
                subject=r.subject,
                predicate=r.predicate,
                obj=r.obj,
                delta_pct=delta,
                window_literal=window_literal,
            )
            annotated += 1
        except Exception as exc:
            logger.warning("annotate failed for %s --%s--> %s: %s",
                           r.subject, r.predicate, r.obj, exc)
    return annotated
