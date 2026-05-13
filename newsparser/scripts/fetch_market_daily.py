import logging
from datetime import date, timedelta

from newsparser._env_loader import load_env
load_env()

from newsparser.market import fetcher, store

logger = logging.getLogger(__name__)
_BACKFILL_DAYS = 365 * 5


def main() -> None:
    store.init_market_db()
    today = date.today()

    for alias in fetcher.TICKERS:
        try:
            last = store.latest_daily_date(alias)
            start = last + timedelta(days=1) if last else today - timedelta(days=_BACKFILL_DAYS)
            if start > today:
                logger.info("%s: up to date", alias)
                continue
            bars = fetcher.fetch_daily(alias, start, today)
            store.upsert_daily(bars)
            logger.info("%s: +%d rows", alias, len(bars))
        except Exception as exc:
            logger.error("%s: failed (%s)", alias, exc)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    main()
