import os
from datetime import datetime, timedelta
from pathlib import Path


class LockError(RuntimeError):
    pass


STALE_MINUTES = 30


def acquire_lock(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        ts_str = path.read_text().strip()
        try:
            ts = datetime.fromisoformat(ts_str)
            if datetime.utcnow() - ts < timedelta(minutes=STALE_MINUTES):
                raise LockError(f"active lock from {ts_str}")
        except ValueError:
            pass  # parse failure → treat as stale
    path.write_text(datetime.utcnow().isoformat())


def release_lock(path: Path) -> None:
    if path.exists():
        path.unlink()
