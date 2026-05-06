import time
import pytest
from pathlib import Path
from newsparser.scheduler.lock import acquire_lock, release_lock, LockError

@pytest.fixture
def lock_path(tmp_path):
    return tmp_path / "state" / "lockfile"

def test_acquire_creates_lockfile(lock_path):
    acquire_lock(lock_path)
    assert lock_path.exists()

def test_acquire_raises_if_recent_lock_exists(lock_path):
    acquire_lock(lock_path)
    with pytest.raises(LockError, match="active"):
        acquire_lock(lock_path)

def test_acquire_succeeds_if_stale_lock(lock_path, monkeypatch):
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text("2000-01-01T00:00:00")  # very old lock
    acquire_lock(lock_path)  # stale → overwrite
    assert lock_path.exists()

def test_release_removes_lockfile(lock_path):
    acquire_lock(lock_path)
    release_lock(lock_path)
    assert not lock_path.exists()

def test_release_noop_if_no_lock(lock_path):
    release_lock(lock_path)  # no exception
