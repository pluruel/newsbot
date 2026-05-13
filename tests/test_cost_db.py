# tests/test_cost_db.py
import sqlite3
import importlib
import pytest


def test_record_run_creates_table(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    from newsparser.bots.core import cost_db
    importlib.reload(cost_db)
    cost_db.record_run(bot="cycle", meta={"duration_ms": 1000, "input_tokens": 100, "output_tokens": 50, "cost_usd": 0.001})
    db_path = tmp_path / "state" / "claude_runs.db"
    assert db_path.exists()
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT bot, ok FROM runs").fetchall()
    conn.close()
    assert rows == [("cycle", 1)]


def test_record_run_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    from newsparser.bots.core import cost_db
    importlib.reload(cost_db)
    cost_db.record_run(bot="cycle", meta={}, ok=False, error="timeout")
    db_path = tmp_path / "state" / "claude_runs.db"
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT ok, error FROM runs").fetchone()
    conn.close()
    assert row == (0, "timeout")
