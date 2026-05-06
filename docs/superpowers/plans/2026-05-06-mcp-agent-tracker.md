# MCP Agent Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace naive entity-hint graph lookup in `run_tracker()` with a full MCP-based agent where Claude decides which tools to call.

**Architecture:** FastMCP server runs as a Docker service exposing 4 tools over HTTP/SSE on port 8766. The `bot` (running on the host) invokes `claude -p ... --mcp-config mcp.json`, and Claude autonomously calls graph/cycle/history/interests tools as needed before synthesizing an answer.

**Tech Stack:** `fastmcp>=2.0`, Claude CLI `--mcp-config` flag, Docker Compose SSE transport

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Create | `newsparser/mcp_server.py` | FastMCP server; 4 tools + interest event logging |
| Create | `mcp.json` | Host claude CLI config pointing to `http://localhost:8766/sse` |
| Create | `tests/test_mcp_server.py` | Tests for all 4 tools |
| Modify | `newsparser/claude/runner.py` | Add `mcp_config` param + `--model` flag |
| Modify | `newsparser/bot/tracker.py` | Simplified prompt; remove manual context assembly |
| Modify | `tests/test_runner.py` | Tests for new mcp_config param |
| Modify | `tests/test_tracker.py` | Remove graph patches; update to new contract |
| Modify | `docker-compose.yml` | Add `mcp-server` service |
| Modify | `pyproject.toml` | Add `fastmcp` dependency |

---

## Task 1: Update `runner.py` — add `mcp_config` and `--model` flag

**Files:**
- Modify: `newsparser/claude/runner.py`
- Modify: `tests/test_runner.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_runner.py`:

```python
def test_run_claude_includes_model_flag():
    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result) as mock_run:
        run_claude("query")
    cmd = mock_run.call_args[0][0]
    assert "--model" in cmd
    assert "claude-sonnet-4-6" in cmd

def test_run_claude_includes_mcp_config_when_given():
    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result) as mock_run:
        run_claude("query", mcp_config="mcp.json")
    cmd = mock_run.call_args[0][0]
    assert "--mcp-config" in cmd
    assert "mcp.json" in cmd

def test_run_claude_omits_mcp_config_by_default():
    mock_result = MagicMock(returncode=0, stdout="ok", stderr="")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result) as mock_run:
        run_claude("query")
    cmd = mock_run.call_args[0][0]
    assert "--mcp-config" not in cmd
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_runner.py -v
```

Expected: 3 new tests FAIL, 3 existing tests pass.

- [ ] **Step 3: Update `runner.py`**

Replace the entire file with:

```python
import subprocess


class ClaudeError(RuntimeError):
    pass


def run_claude(prompt: str, timeout: int = 1500, mcp_config: str | None = None) -> str:
    """Invoke claude CLI headless and return stdout. Raises ClaudeError on failure."""
    cmd = ["claude", "-p", prompt, "--output-format", "text", "--model", "claude-sonnet-4-6"]
    if mcp_config:
        cmd += ["--mcp-config", mcp_config]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        raise ClaudeError(f"claude exited {result.returncode}: {result.stderr[:500]}")
    return result.stdout
```

- [ ] **Step 4: Run all runner tests**

```bash
.venv/bin/pytest tests/test_runner.py -v
```

Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/claude/runner.py tests/test_runner.py
git commit -m "feat: add mcp_config param and --model flag to run_claude"
```

---

## Task 2: Add `fastmcp` dependency + MCP server foundation with `graph_query`

**Files:**
- Modify: `pyproject.toml`
- Create: `newsparser/mcp_server.py`
- Create: `tests/test_mcp_server.py`

- [ ] **Step 1: Add `fastmcp` to `pyproject.toml`**

In the `dependencies` list, add `"fastmcp>=2.0"`:

```toml
dependencies = [
    "feedparser>=6.0",
    "trafilatura>=1.9",
    "python-telegram-bot>=20.0",
    "python-dotenv>=1.0",
    "apscheduler>=3.10,<4",
    "neo4j>=6.2.0",
    "fastmcp>=2.0",
]
```

Then sync the venv:

```bash
uv sync --frozen
```

If uv complains about `--frozen` with a new dependency, run without it:

```bash
uv sync
```

- [ ] **Step 2: Write failing test for `graph_query`**

Create `tests/test_mcp_server.py`:

```python
import json
import pytest
from pathlib import Path
from unittest.mock import patch

from newsparser.mcp_server import graph_query


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    (tmp_path / "workspace" / "me").mkdir(parents=True)
    (tmp_path / "workspace" / "me" / "interest-events.jsonl").touch()
    (tmp_path / "workspace" / "sessions").mkdir(parents=True)
    (tmp_path / "workspace" / "cycles").mkdir(parents=True)


def test_graph_query_returns_formatted_context():
    with patch("newsparser.mcp_server.get_context", return_value=[
        {"name": "삼성전자", "label": "Company", "mentions": 5}
    ]), patch("newsparser.mcp_server.get_influence_chain", return_value=[]):
        result = graph_query("삼성전자")
    assert "삼성전자" in result


def test_graph_query_logs_interest_event(tmp_path):
    events_path = Path(tmp_path / "workspace" / "me" / "interest-events.jsonl")
    with patch("newsparser.mcp_server.get_context", return_value=[]), \
         patch("newsparser.mcp_server.get_influence_chain", return_value=[]):
        graph_query("TSMC")
    event = json.loads(events_path.read_text().strip())
    assert "TSMC" in event["entities"]
    assert event["type"] == "query"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_mcp_server.py -v
```

Expected: ImportError or ModuleNotFoundError (file doesn't exist yet).

- [ ] **Step 4: Create `newsparser/mcp_server.py`**

```python
import json
import os
from datetime import datetime
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from newsparser.graph.traversal import get_context, get_influence_chain, format_context_for_claude

mcp = FastMCP("newsparser")


def _workspace() -> Path:
    return Path(os.environ.get("WORKSPACE_DIR", "workspace"))


def _log_interest_event(entity: str) -> None:
    event = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "type": "query",
        "entities": [entity],
        "themes": [entity],
        "depth": "shallow",
    }
    path = _workspace() / "me" / "interest-events.jsonl"
    with path.open("a") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


@mcp.tool()
def graph_query(entity: str, days: int = 7) -> str:
    """Query the knowledge graph for context about an entity."""
    neighbors = get_context(entity, days)
    chains = get_influence_chain(entity)
    _log_interest_event(entity)
    return format_context_for_claude(entity, neighbors, chains)


if __name__ == "__main__":
    mcp.run(transport="sse", host="0.0.0.0", port=8766)
```

- [ ] **Step 5: Run tests**

```bash
.venv/bin/pytest tests/test_mcp_server.py -v
```

Expected: 2 tests pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock newsparser/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add mcp_server with graph_query tool"
```

---

## Task 3: Add `read_cycle_reports` tool

**Files:**
- Modify: `newsparser/mcp_server.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_mcp_server.py`:

```python
from newsparser.mcp_server import read_cycle_reports


def test_read_cycle_reports_returns_n_most_recent(tmp_path):
    cycles = Path(tmp_path / "workspace" / "cycles")
    (cycles / "2026-05-04-10.md").write_text("cycle A")
    (cycles / "2026-05-05-10.md").write_text("cycle B")
    (cycles / "2026-05-06-10.md").write_text("cycle C")

    result = read_cycle_reports(n=2)
    assert "cycle B" in result
    assert "cycle C" in result
    assert "cycle A" not in result


def test_read_cycle_reports_empty_dir():
    result = read_cycle_reports()
    assert "No cycle reports found" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_mcp_server.py::test_read_cycle_reports_returns_n_most_recent tests/test_mcp_server.py::test_read_cycle_reports_empty_dir -v
```

Expected: ImportError (`read_cycle_reports` not defined yet).

- [ ] **Step 3: Add `read_cycle_reports` to `mcp_server.py`**

Add after the `graph_query` definition:

```python
@mcp.tool()
def read_cycle_reports(n: int = 4) -> str:
    """Read the N most recent cycle reports."""
    cycles_dir = _workspace() / "cycles"
    if not cycles_dir.exists():
        return "No cycle reports found."
    files = sorted(cycles_dir.glob("*.md"), reverse=True)[:n]
    if not files:
        return "No cycle reports found."
    return "\n\n---\n\n".join(f.read_text() for f in reversed(files))
```

- [ ] **Step 4: Run all mcp_server tests**

```bash
.venv/bin/pytest tests/test_mcp_server.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add read_cycle_reports tool to mcp_server"
```

---

## Task 4: Add `read_conversation_history` tool

**Files:**
- Modify: `newsparser/mcp_server.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_mcp_server.py`:

```python
from newsparser.mcp_server import read_conversation_history
from newsparser.bot.tracker import save_history


def test_read_conversation_history_returns_formatted_turns(tmp_path):
    save_history("chat99", [
        {"role": "user", "content": "안녕", "ts": "2026-05-05T00:00:00"},
        {"role": "assistant", "content": "안녕하세요", "ts": "2026-05-05T00:00:01"},
    ])
    result = read_conversation_history("chat99")
    assert "USER: 안녕" in result
    assert "ASSISTANT: 안녕하세요" in result


def test_read_conversation_history_empty():
    result = read_conversation_history("nonexistent_chat")
    assert "No conversation history" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_mcp_server.py::test_read_conversation_history_returns_formatted_turns tests/test_mcp_server.py::test_read_conversation_history_empty -v
```

Expected: ImportError (`read_conversation_history` not defined yet).

- [ ] **Step 3: Add `read_conversation_history` to `mcp_server.py`**

Add after `read_cycle_reports`:

```python
@mcp.tool()
def read_conversation_history(chat_id: str, n: int = 10) -> str:
    """Read recent conversation turns for a given chat."""
    from newsparser.bot.tracker import load_history
    history = load_history(chat_id)[-n:]
    if not history:
        return "No conversation history."
    return "\n".join(f"{t['role'].upper()}: {t['content']}" for t in history)
```

- [ ] **Step 4: Run all mcp_server tests**

```bash
.venv/bin/pytest tests/test_mcp_server.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add read_conversation_history tool to mcp_server"
```

---

## Task 5: Add `read_interests` tool

**Files:**
- Modify: `newsparser/mcp_server.py`
- Modify: `tests/test_mcp_server.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_mcp_server.py`:

```python
from newsparser.mcp_server import read_interests


def test_read_interests_returns_content(tmp_path):
    (tmp_path / "workspace" / "me" / "interests.md").write_text("## 관심 분야\n- 반도체")
    result = read_interests()
    assert "반도체" in result


def test_read_interests_missing_file():
    result = read_interests()
    assert "No interests file found" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_mcp_server.py::test_read_interests_returns_content tests/test_mcp_server.py::test_read_interests_missing_file -v
```

Expected: ImportError (`read_interests` not defined yet).

- [ ] **Step 3: Add `read_interests` to `mcp_server.py`**

Add after `read_conversation_history`:

```python
@mcp.tool()
def read_interests() -> str:
    """Read the user's interest profile."""
    path = _workspace() / "me" / "interests.md"
    if not path.exists():
        return "No interests file found."
    return path.read_text()
```

- [ ] **Step 4: Run all mcp_server tests**

```bash
.venv/bin/pytest tests/test_mcp_server.py -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add newsparser/mcp_server.py tests/test_mcp_server.py
git commit -m "feat: add read_interests tool to mcp_server"
```

---

## Task 6: Simplify `tracker.py`

**Files:**
- Modify: `newsparser/bot/tracker.py`
- Modify: `tests/test_tracker.py`

- [ ] **Step 1: Update tests first**

Replace `tests/test_tracker.py` with:

```python
import pytest
from pathlib import Path
from unittest.mock import patch
from newsparser.bot.tracker import run_tracker, load_history, save_history


@pytest.fixture(autouse=True)
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path / "workspace"))
    (tmp_path / "workspace" / "sessions").mkdir(parents=True)
    (tmp_path / "workspace" / "me").mkdir(parents=True)


def test_load_history_empty_for_new_chat():
    history = load_history("chat123")
    assert history == []


def test_save_and_load_history():
    save_history("chat123", [
        {"role": "user", "content": "안녕"},
        {"role": "assistant", "content": "안녕하세요"},
    ])
    history = load_history("chat123")
    assert len(history) == 2
    assert history[0]["content"] == "안녕"


def test_load_history_returns_last_10_turns():
    turns = [{"role": "user", "content": str(i), "ts": "2026-05-05T00:00:00"} for i in range(15)]
    save_history("chat123", turns)
    history = load_history("chat123")
    assert len(history) == 10
    assert history[0]["content"] == "5"


def test_run_tracker_calls_claude_with_mcp_config():
    with patch("newsparser.bot.tracker.run_claude", return_value="Claude answer") as mock_claude:
        answer = run_tracker(chat_id="chat123", query="FOMC 어떻게 됐어?")
    mock_claude.assert_called_once()
    _, kwargs = mock_claude.call_args
    prompt = mock_claude.call_args[0][0]
    assert "FOMC" in prompt
    assert kwargs.get("mcp_config") is not None
    assert answer == "Claude answer"


def test_run_tracker_appends_to_history():
    with patch("newsparser.bot.tracker.run_claude", return_value="답변"):
        run_tracker(chat_id="chat123", query="질문")
    history = load_history("chat123")
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["role"] == "assistant"
```

- [ ] **Step 2: Run tests to confirm failures**

```bash
.venv/bin/pytest tests/test_tracker.py -v
```

Expected: `test_run_tracker_calls_claude_with_mcp_config` fails (mcp_config not passed yet), rest pass.

- [ ] **Step 3: Rewrite `tracker.py`**

Replace the entire file with:

```python
import json
import logging
import os
from datetime import datetime
from pathlib import Path

from newsparser.claude.runner import run_claude

logger = logging.getLogger(__name__)

HISTORY_MAX_TURNS = 10

_MCP_CONFIG = Path(__file__).parent.parent / "mcp.json"


def _workspace() -> Path:
    return Path(os.environ.get("WORKSPACE_DIR", "workspace"))


def load_history(chat_id: str) -> list[dict]:
    path = _workspace() / "sessions" / f"{chat_id}.jsonl"
    if not path.exists():
        return []
    turns = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return turns[-HISTORY_MAX_TURNS:]


def save_history(chat_id: str, turns: list[dict]) -> None:
    path = _workspace() / "sessions" / f"{chat_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(t, ensure_ascii=False) for t in turns))


def run_tracker(chat_id: str, query: str) -> str:
    """Resolve a user query using Claude with MCP tools."""
    history = load_history(chat_id)

    prompt = (
        "You are a market intelligence assistant. Use the available tools "
        "to gather relevant context, then answer the user's query. "
        "Cite cycle reports by date. Lead with TL;DR if the answer is long.\n\n"
        f"User query: {query}\n"
        f"Chat ID (for history tool): {chat_id}"
    )

    answer = run_claude(prompt, mcp_config=str(_MCP_CONFIG))

    now = datetime.utcnow().isoformat()
    new_turns = history + [
        {"role": "user", "content": query, "ts": now},
        {"role": "assistant", "content": answer, "ts": now},
    ]
    save_history(chat_id, new_turns)
    return answer
```

- [ ] **Step 4: Run tracker tests**

```bash
.venv/bin/pytest tests/test_tracker.py -v
```

Expected: all 5 tests pass.

- [ ] **Step 5: Run full test suite to catch regressions**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add newsparser/bot/tracker.py tests/test_tracker.py
git commit -m "refactor: simplify tracker — Claude chooses tools via MCP"
```

---

## Task 7: Docker service + `mcp.json`

**Files:**
- Modify: `docker-compose.yml`
- Create: `mcp.json`

No unit tests for config files. Manual verification at the end.

- [ ] **Step 1: Add `mcp-server` service to `docker-compose.yml`**

Add before the `volumes:` section:

```yaml
  mcp-server:
    build: .
    command: .venv/bin/python -m newsparser.mcp_server
    ports:
      - "8766:8766"
    env_file: .env
    environment:
      NEO4J_URI: bolt://neo4j:7687
    volumes:
      - ./workspace:/app/workspace
    depends_on:
      neo4j:
        condition: service_healthy
    restart: unless-stopped
```

- [ ] **Step 2: Create `mcp.json` in repo root**

```json
{
  "mcpServers": {
    "newsparser": {
      "url": "http://localhost:8766/sse"
    }
  }
}
```

- [ ] **Step 3: Verify `mcp_server.py` is importable as a module**

```bash
.venv/bin/python -c "import newsparser.mcp_server; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Run full test suite one last time**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml mcp.json
git commit -m "feat: add mcp-server Docker service and mcp.json config"
```

---

## Smoke Test (manual, after docker-compose up)

```bash
docker compose up mcp-server -d
# wait ~5s for server to start
curl -N http://localhost:8766/sse
# should see: event: endpoint\ndata: /messages/...
```

Then from the host, test the full tracker flow with a real query via Telegram bot or directly:

```bash
.venv/bin/python -c "
from newsparser.bot.tracker import run_tracker
print(run_tracker('test_chat', '삼성전자 최근 동향'))
"
```
