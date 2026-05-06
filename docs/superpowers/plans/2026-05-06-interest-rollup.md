# Interest Rollup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automatically update `interests.md` before each morning brief by analyzing recent tracker query events (query text + graph hit entities) with Claude.

**Architecture:** Three changes — (1) enrich tracker event logging with graph hit entities, (2) new `interests_rollup()` function that feeds events to Claude for synthesis, (3) morning scheduler calls rollup before composing the brief.

**Tech Stack:** Python 3.12, existing `run_claude()` subprocess wrapper, `interest-events.jsonl` JSONL log, `interests.md` markdown profile.

---

### Task 1: Enrich tracker event logging with graph hit entities

**Files:**
- Modify: `newsparser/bot/tracker.py`
- Test: `tests/test_tracker.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_tracker.py`:

```python
def test_log_interest_event_includes_graph_entities():
    workspace = Path(os.environ["WORKSPACE_DIR"])
    with patch("newsparser.bot.tracker.get_context", return_value=[
            {"name": "삼성전자", "label": "Company", "mentions": 5},
            {"name": "TSMC", "label": "Company", "mentions": 3},
         ]), \
         patch("newsparser.bot.tracker.get_influence_chain", return_value=[]), \
         patch("newsparser.bot.tracker.run_claude", return_value="답변"):
        run_tracker(chat_id="chat123", query="반도체 업황")
    events_path = workspace / "me" / "interest-events.jsonl"
    event = json.loads(events_path.read_text().strip().splitlines()[-1])
    assert "삼성전자" in event["entities"]
    assert "TSMC" in event["entities"]

def test_log_interest_event_empty_entities_on_graph_failure():
    workspace = Path(os.environ["WORKSPACE_DIR"])
    with patch("newsparser.bot.tracker.get_context", side_effect=RuntimeError("DB down")), \
         patch("newsparser.bot.tracker.run_claude", return_value="답변"):
        run_tracker(chat_id="chat123", query="반도체 업황")
    event = json.loads((workspace / "me" / "interest-events.jsonl").read_text().strip().splitlines()[-1])
    assert event["entities"] == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_tracker.py::test_log_interest_event_includes_graph_entities tests/test_tracker.py::test_log_interest_event_empty_entities_on_graph_failure -v
```

Expected: FAIL (entities always `[]`)

- [ ] **Step 3: Update `_log_interest_event` signature and call site**

In `newsparser/bot/tracker.py`, replace:

```python
def _log_interest_event(query: str) -> None:
    event = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "type": "query",
        "entities": [],
        "themes": [query[:50]],
        "depth": "shallow",
    }
    path = _workspace() / "me" / "interest-events.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
```

with:

```python
def _log_interest_event(query: str, entities: list[str]) -> None:
    event = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "type": "query",
        "entities": entities,
        "themes": [query[:50]],
        "depth": "shallow",
    }
    path = _workspace() / "me" / "interest-events.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
```

- [ ] **Step 4: Update `run_tracker` to capture and pass entities**

In `newsparser/bot/tracker.py`, replace the graph traversal + log call block:

```python
    entity_hint = query.split()[0] if query.split() else query
    try:
        neighbors = get_context(entity_hint, days=7)
        chains = get_influence_chain(entity_hint)
        graph_ctx = format_context_for_claude(entity_hint, neighbors, chains)
    except Exception:
        logger.warning("Graph traversal failed for %r — proceeding without context", entity_hint)
        graph_ctx = ""
```

with:

```python
    entity_hint = query.split()[0] if query.split() else query
    neighbors = []
    try:
        neighbors = get_context(entity_hint, days=7)
        chains = get_influence_chain(entity_hint)
        graph_ctx = format_context_for_claude(entity_hint, neighbors, chains)
    except Exception:
        logger.warning("Graph traversal failed for %r — proceeding without context", entity_hint)
        graph_ctx = ""
```

And replace the log call at the bottom:

```python
    _log_interest_event(query)
```

with:

```python
    hit_entities = [n["name"] for n in neighbors]
    _log_interest_event(query, hit_entities)
```

- [ ] **Step 5: Run all tracker tests**

```bash
.venv/bin/pytest tests/test_tracker.py -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add newsparser/bot/tracker.py tests/test_tracker.py
git commit -m "feat: log graph hit entities in interest events"
```

---

### Task 2: Create `interests_rollup()` module

**Files:**
- Create: `newsparser/scheduler/interests.py`
- Create: `tests/test_interests.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_interests.py`:

```python
import json
import os
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from newsparser.scheduler.interests import interests_rollup


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    ws = tmp_path / "workspace" / "me"
    ws.mkdir(parents=True)
    (ws / "interests.md").write_text(
        "# Interests Profile\nLast updated: 2026-05-01\n\n## Themes\n\n## User overrides\n- 항상 포함: 반도체\n",
        encoding="utf-8",
    )
    (ws / "interest-events.jsonl").touch()


def _write_event(tmp_path, query: str, entities: list[str], days_ago: int = 1):
    ts = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z")
    event = {"ts": ts, "type": "query", "entities": entities, "themes": [query], "depth": "shallow"}
    ws = Path(os.environ["WORKSPACE_DIR"])
    with (ws / "me" / "interest-events.jsonl").open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def test_rollup_skips_when_no_events():
    with patch("newsparser.scheduler.interests.run_claude") as mock_claude:
        interests_rollup()
    mock_claude.assert_not_called()


def test_rollup_skips_events_older_than_14_days(tmp_path):
    _write_event(tmp_path, "테크기사", ["삼성전자"], days_ago=15)
    with patch("newsparser.scheduler.interests.run_claude") as mock_claude:
        interests_rollup()
    mock_claude.assert_not_called()


def test_rollup_calls_claude_with_event_data(tmp_path):
    _write_event(tmp_path, "반도체 업황", ["삼성전자", "TSMC"], days_ago=1)
    with patch("newsparser.scheduler.interests.run_claude", return_value="# Interests Profile\nLast updated: 2026-05-06\n\n## Themes\n- 반도체\n\n## User overrides\n- 항상 포함: 반도체\n") as mock_claude:
        interests_rollup()
    mock_claude.assert_called_once()
    prompt = mock_claude.call_args[0][0]
    assert "삼성전자" in prompt
    assert "TSMC" in prompt
    assert "반도체 업황" in prompt
    assert "User overrides" in prompt


def test_rollup_writes_claude_output_to_interests_md(tmp_path):
    _write_event(tmp_path, "AI 반도체", ["엔비디아"], days_ago=2)
    new_content = "# Interests Profile\nLast updated: 2026-05-06\n\n## Themes\n- AI 반도체\n\n## User overrides\n- 항상 포함: 반도체\n"
    with patch("newsparser.scheduler.interests.run_claude", return_value=new_content):
        interests_rollup()
    ws = Path(os.environ["WORKSPACE_DIR"])
    written = (ws / "me" / "interests.md").read_text(encoding="utf-8")
    assert written == new_content
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_interests.py -v
```

Expected: FAIL with `ModuleNotFoundError: No module named 'newsparser.scheduler.interests'`

- [ ] **Step 3: Create `newsparser/scheduler/interests.py`**

```python
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from newsparser.claude.runner import run_claude
from newsparser.scheduler.workspace import ensure_workspace

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 14


def interests_rollup() -> None:
    """Analyze recent tracker events and update interests.md via Claude."""
    workspace = ensure_workspace()
    events_path = workspace / "me" / "interest-events.jsonl"
    interests_path = workspace / "me" / "interests.md"

    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    events: list[dict] = []
    if events_path.exists():
        for line in events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
                ts = datetime.fromisoformat(e["ts"].replace("Z", "+00:00"))
                if ts >= cutoff:
                    events.append(e)
            except Exception:
                continue

    if not events:
        logger.info("No interest events in last %d days — skipping rollup", LOOKBACK_DAYS)
        return

    current_interests = interests_path.read_text(encoding="utf-8") if interests_path.exists() else ""

    events_block = "\n".join(
        f"- [{e['ts']}] query: {e['themes'][0] if e.get('themes') else ''} | entities: {', '.join(e.get('entities', []))}"
        for e in events
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    prompt = (
        f"아래는 사용자의 최근 {LOOKBACK_DAYS}일간 tracker 쿼리 이벤트야.\n"
        "각 이벤트에는 쿼리 텍스트와 실제 graph에서 히트된 엔티티가 포함되어 있어.\n"
        "현재 interests.md도 같이 줄게.\n\n"
        f"## 쿼리 이벤트\n{events_block}\n\n"
        f"## 현재 interests.md\n{current_interests}\n\n"
        "위 데이터를 분석해서 새 interests.md를 작성해줘. 규칙:\n"
        "- 반복 등장하는 엔티티나 테마를 관심사로 추론해\n"
        "- '안녕', '기사 보여줘', '요약해줘' 같은 메타 쿼리는 무시해\n"
        "- 기존 ## User overrides 내용은 반드시 그대로 보존하면서 병합해\n"
        "- ## Themes 섹션을 업데이트해\n"
        f"- Last updated를 {today}로 갱신해\n"
        "- 파일 전체 내용을 raw markdown으로만 출력해. 설명이나 코드블록 없이."
    )

    updated = run_claude(prompt)
    interests_path.write_text(updated, encoding="utf-8")
    logger.info("interests.md updated via rollup")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_interests.py -v
```

Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add newsparser/scheduler/interests.py tests/test_interests.py
git commit -m "feat: add interests_rollup — Claude-powered interest profile synthesis"
```

---

### Task 3: Wire rollup into morning scheduler

**Files:**
- Modify: `newsparser/scheduler/morning.py`
- Test: `tests/test_morning.py`

- [ ] **Step 1: Write failing test**

Add to `tests/test_morning.py`:

```python
def test_run_morning_calls_interests_rollup_before_brief():
    call_order = []
    def fake_rollup():
        call_order.append("rollup")
    def fake_claude(prompt):
        call_order.append("claude")
        return SAMPLE_BRIEF
    with patch("newsparser.scheduler.morning.interests_rollup", side_effect=fake_rollup), \
         patch("newsparser.scheduler.morning.run_claude", side_effect=fake_claude), \
         patch("newsparser.scheduler.morning.send_message"):
        run_morning("2026-05-05")
    assert call_order == ["rollup", "claude"]

def test_run_morning_continues_if_rollup_fails():
    with patch("newsparser.scheduler.morning.interests_rollup", side_effect=RuntimeError("rollup error")), \
         patch("newsparser.scheduler.morning.run_claude", return_value=SAMPLE_BRIEF) as mock_claude, \
         patch("newsparser.scheduler.morning.send_message"):
        run_morning("2026-05-05")  # must not raise
    mock_claude.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_morning.py::test_run_morning_calls_interests_rollup_before_brief tests/test_morning.py::test_run_morning_continues_if_rollup_fails -v
```

Expected: FAIL (`interests_rollup` not imported)

- [ ] **Step 3: Update `morning.py`**

Add import at the top of `newsparser/scheduler/morning.py`:

```python
from newsparser.scheduler.interests import interests_rollup
```

Add rollup call at the start of `run_morning`, before the cycle files block:

```python
def run_morning(date_str: str) -> None:
    """Compose and send the daily brief."""
    try:
        interests_rollup()
    except Exception:
        logger.warning("Interest rollup failed — proceeding with existing interests.md")

    workspace = ensure_workspace()
    # ... rest unchanged
```

- [ ] **Step 4: Run all morning tests**

```bash
.venv/bin/pytest tests/test_morning.py -v
```

Expected: all PASS

- [ ] **Step 5: Run full test suite**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add newsparser/scheduler/morning.py tests/test_morning.py
git commit -m "feat: run interests rollup before morning brief"
```
