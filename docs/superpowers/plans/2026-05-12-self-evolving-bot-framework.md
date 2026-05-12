# Self-Evolving Bot Framework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace host cron + host telegram bot with a unified Python dispatcher that discovers bots from `newsparser/bots/*/bot.py`, manages scheduling via APScheduler, routes Telegram messages by regex, and lets the tracker create new bots via chat.

**Architecture:** A single `dispatcher` Docker container runs PTB's `Application` (which embeds APScheduler via `JobQueue`) and a `BotRegistry` that scans `newsparser/bots/*/bot.py` for `BOT` objects. Existing scripts (`run_cycle.py` etc.) are kept intact; each bot is a thin async wrapper around them. The full repo is bind-mounted into the container so file edits take effect on `/reload` without a rebuild; `docker compose up -d --build dispatcher` (triggered via Telegram) is only needed when a new package is required.

**Tech Stack:** Python 3.12, python-telegram-bot 20+, APScheduler 3.10+ (already deps), SQLite, Docker Compose.

---

## File Map

### Create
| File | Responsibility |
|---|---|
| `newsparser/bots/__init__.py` | Re-exports `Bot`, `Cron`, `TelegramMatch`, `Context` |
| `newsparser/bots/core/__init__.py` | Empty package marker |
| `newsparser/bots/core/types.py` | `Bot`, `Cron`, `TelegramMatch` dataclasses |
| `newsparser/bots/core/context.py` | `Context`, `TelegramSender` |
| `newsparser/bots/core/cost_db.py` | SQLite helper → `workspace/state/claude_runs.db` |
| `newsparser/bots/core/registry.py` | `BotRegistry`: scan, load, reload |
| `newsparser/dispatcher.py` | PTB app + APScheduler + `/reload` handler |
| `newsparser/bots/cycle/__init__.py` | Empty |
| `newsparser/bots/cycle/bot.py` | Wraps `run_cycle.main` |
| `newsparser/bots/weekly/__init__.py` | Empty |
| `newsparser/bots/weekly/bot.py` | Wraps `run_weekly.main` |
| `newsparser/bots/reflect/__init__.py` | Empty |
| `newsparser/bots/reflect/bot.py` | Wraps `run_reflect.main` |
| `newsparser/bots/market_daily/__init__.py` | Empty |
| `newsparser/bots/market_daily/bot.py` | Wraps `fetch_market_daily.main` |
| `newsparser/bots/tracker/__init__.py` | Empty |
| `newsparser/bots/tracker/bot.py` | Wraps `run_tracker`; handles `/rebuild` command |
| `tests/test_bot_types.py` | Tests for `Bot`, `Cron`, `TelegramMatch` |
| `tests/test_bot_context.py` | Tests for `Context`, `TelegramSender` |
| `tests/test_cost_db.py` | Tests for `record_run` |
| `tests/test_bot_registry.py` | Tests for `BotRegistry.load()` |

### Modify
| File | Change |
|---|---|
| `newsparser/claude/runner.py` | Add `run_claude_json()` returning `(text, meta_dict)` |
| `docker-compose.yml` | Add `dispatcher` service with docker socket + full repo mount |
| `run.sh` | Remove (replaced by `docker compose up -d`) |

### Keep unchanged
All files under `newsparser/scripts/`, `newsparser/bot/`, `newsparser/claude/runner.py::run_claude`.

---

## Task 1: Core trigger types

**Files:**
- Create: `newsparser/bots/core/types.py`
- Create: `newsparser/bots/__init__.py`
- Create: `newsparser/bots/core/__init__.py`
- Test: `tests/test_bot_types.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bot_types.py
import re
import pytest
from newsparser.bots import Bot, Cron, TelegramMatch

async def _noop(ctx): pass

def test_bot_defaults():
    bot = Bot(name="test", triggers=[Cron("0 9 * * *")], run=_noop)
    assert bot.enabled is True
    assert bot.name == "test"

def test_bot_disabled():
    bot = Bot(name="test", triggers=[Cron("0 9 * * *")], run=_noop, enabled=False)
    assert not bot.enabled

def test_cron_default_tz():
    c = Cron("0 9 * * *")
    assert c.tz == "Asia/Seoul"

def test_cron_custom_tz():
    c = Cron("0 9 * * *", tz="UTC")
    assert c.tz == "UTC"

def test_telegram_match_pattern():
    t = TelegramMatch(r"^/cycle\b")
    assert re.search(t.pattern, "/cycle")
    assert not re.search(t.pattern, "/cycleXYZ")

def test_telegram_match_catch_all():
    t = TelegramMatch(r".*")
    assert re.search(t.pattern, "anything")
```

- [ ] **Step 2: Run to verify FAIL**

```bash
.venv/bin/pytest tests/test_bot_types.py -v
```
Expected: `ModuleNotFoundError: No module named 'newsparser.bots'`

- [ ] **Step 3: Create the types**

```python
# newsparser/bots/core/__init__.py
# (empty)
```

```python
# newsparser/bots/core/types.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable

if TYPE_CHECKING:
    from newsparser.bots.core.context import Context


@dataclass
class Cron:
    schedule: str
    tz: str = "Asia/Seoul"


@dataclass
class TelegramMatch:
    pattern: str


Trigger = Cron | TelegramMatch


@dataclass
class Bot:
    name: str
    triggers: list[Trigger]
    run: Callable[["Context"], Awaitable[None]]
    enabled: bool = True
```

```python
# newsparser/bots/__init__.py
from newsparser.bots.core.types import Bot, Cron, TelegramMatch
from newsparser.bots.core.context import Context

__all__ = ["Bot", "Cron", "TelegramMatch", "Context"]
```

- [ ] **Step 4: Run to verify PASS**

```bash
.venv/bin/pytest tests/test_bot_types.py -v
```
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add newsparser/bots/__init__.py newsparser/bots/core/__init__.py newsparser/bots/core/types.py tests/test_bot_types.py
git commit -m "feat(bots): add Bot, Cron, TelegramMatch dataclasses"
```

---

## Task 2: Context and TelegramSender

**Files:**
- Create: `newsparser/bots/core/context.py`
- Test: `tests/test_bot_context.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bot_context.py
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from newsparser.bots.core.context import Context, TelegramSender


def make_ctx(bot=None, chat_id=None, alert_chat_id=""):
    sender = TelegramSender(bot=bot, chat_id=chat_id, alert_chat_id=alert_chat_id)
    return Context(bot_name="test", workspace=Path("workspace"), telegram=sender)


def test_context_logger_name():
    ctx = make_ctx()
    assert ctx.logger.name == "newsparser.bots.test"


def test_telegram_sender_uses_chat_id():
    mock_bot = AsyncMock()
    sender = TelegramSender(bot=mock_bot, chat_id="123", alert_chat_id="999")
    asyncio.get_event_loop().run_until_complete(sender.send("hello"))
    mock_bot.send_message.assert_called_once_with(chat_id="123", text="hello")


def test_telegram_sender_falls_back_to_alert_chat_id():
    mock_bot = AsyncMock()
    sender = TelegramSender(bot=mock_bot, chat_id=None, alert_chat_id="999")
    asyncio.get_event_loop().run_until_complete(sender.send("hello"))
    mock_bot.send_message.assert_called_once_with(chat_id="999", text="hello")


def test_telegram_sender_truncates_long_text():
    mock_bot = AsyncMock()
    sender = TelegramSender(bot=mock_bot, chat_id="123")
    long_text = "x" * 5000
    asyncio.get_event_loop().run_until_complete(sender.send(long_text))
    sent = mock_bot.send_message.call_args[1]["text"]
    assert len(sent) <= 4096


def test_telegram_sender_does_nothing_without_bot():
    sender = TelegramSender(bot=None, chat_id="123")
    # Should not raise
    asyncio.get_event_loop().run_until_complete(sender.send("hello"))


@pytest.mark.asyncio
async def test_context_run_in_thread():
    ctx = make_ctx()
    result = await ctx.run_in_thread(lambda x: x * 2, 5)
    assert result == 10
```

- [ ] **Step 2: Run to verify FAIL**

```bash
.venv/bin/pytest tests/test_bot_context.py -v
```
Expected: `ImportError: cannot import name 'Context'`

- [ ] **Step 3: Implement Context**

```python
# newsparser/bots/core/context.py
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


@dataclass
class TelegramSender:
    bot: Any = None
    chat_id: str | None = None
    alert_chat_id: str = field(default_factory=lambda: os.environ.get("TELEGRAM_ALERT_CHAT_ID", ""))

    async def send(self, text: str) -> None:
        target = self.chat_id or self.alert_chat_id
        if self.bot and target:
            await self.bot.send_message(chat_id=target, text=text[:4096])


@dataclass
class Context:
    bot_name: str
    workspace: Path
    telegram: TelegramSender
    message: Any = None  # telegram.Message | None

    @property
    def logger(self) -> logging.Logger:
        return logging.getLogger(f"newsparser.bots.{self.bot_name}")

    async def claude(self, prompt: str, **kwargs) -> str:
        from newsparser.bots.core.cost_db import record_run
        from newsparser.claude.runner import run_claude_json
        try:
            text, meta = await asyncio.to_thread(run_claude_json, prompt, **kwargs)
            await asyncio.to_thread(record_run, bot=self.bot_name, meta=meta, ok=True)
            return text
        except Exception as exc:
            await asyncio.to_thread(record_run, bot=self.bot_name, meta={}, ok=False, error=str(exc))
            raise

    async def run_in_thread(self, fn: Callable, *args, **kwargs) -> Any:
        return await asyncio.to_thread(fn, *args, **kwargs)
```

- [ ] **Step 4: Run to verify PASS**

```bash
.venv/bin/pytest tests/test_bot_context.py -v
```
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add newsparser/bots/core/context.py tests/test_bot_context.py
git commit -m "feat(bots): add Context and TelegramSender"
```

---

## Task 3: Cost DB + runner JSON mode

**Files:**
- Create: `newsparser/bots/core/cost_db.py`
- Modify: `newsparser/claude/runner.py` (add `run_claude_json`)
- Test: `tests/test_cost_db.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_cost_db.py
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_record_run_creates_table(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    from newsparser.bots.core import cost_db
    import importlib
    importlib.reload(cost_db)  # pick up monkeypatched env
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
    import importlib
    importlib.reload(cost_db)
    cost_db.record_run(bot="cycle", meta={}, ok=False, error="timeout")
    db_path = tmp_path / "state" / "claude_runs.db"
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT ok, error FROM runs").fetchone()
    conn.close()
    assert row == (0, "timeout")
```

```python
# Append to tests/test_runner.py
def test_run_claude_json_returns_text_and_meta():
    payload = json.dumps({
        "result": "analysis",
        "duration_ms": 5000,
        "usage": {"input_tokens": 100, "output_tokens": 50},
        "cost_usd": 0.002,
    })
    mock_result = MagicMock(returncode=0, stdout=payload, stderr="")
    with patch("newsparser.claude.runner.subprocess.run", return_value=mock_result):
        from newsparser.claude.runner import run_claude_json
        text, meta = run_claude_json("/cycle")
    assert text == "analysis"
    assert meta["duration_ms"] == 5000
    assert meta["input_tokens"] == 100
    assert meta["cost_usd"] == 0.002
```

- [ ] **Step 2: Run to verify FAIL**

```bash
.venv/bin/pytest tests/test_cost_db.py tests/test_runner.py -v
```
Expected: `ImportError` on cost_db, `ImportError` on run_claude_json

- [ ] **Step 3: Implement cost_db**

```python
# newsparser/bots/core/cost_db.py
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def _db_path() -> Path:
    return Path(os.environ.get("WORKSPACE_DIR", "workspace")) / "state" / "claude_runs.db"


def _init(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS runs (
            ts            TEXT,
            bot           TEXT,
            model         TEXT,
            duration_ms   INTEGER,
            input_tokens  INTEGER,
            output_tokens INTEGER,
            cost_usd      REAL,
            ok            INTEGER,
            error         TEXT
        )
    """)
    conn.commit()


def record_run(
    bot: str,
    meta: dict,
    model: str = "claude-sonnet-4-6",
    ok: bool = True,
    error: str | None = None,
) -> None:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    _init(conn)
    conn.execute(
        "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
        (
            datetime.now(timezone.utc).isoformat(),
            bot,
            model,
            meta.get("duration_ms"),
            meta.get("input_tokens"),
            meta.get("output_tokens"),
            meta.get("cost_usd"),
            1 if ok else 0,
            error,
        ),
    )
    conn.commit()
    conn.close()
```

- [ ] **Step 4: Add `run_claude_json` to runner.py**

Open `newsparser/claude/runner.py`. After the existing `run_claude` function, add:

```python
import json as _json  # add to top-level imports


def run_claude_json(
    prompt: str,
    timeout: int = 1500,
    mcp_config: str | None = None,
    model: str = "claude-sonnet-4-6",
    system_prompt: str | None = None,
) -> tuple[str, dict]:
    """Like run_claude() but uses --output-format json. Returns (text, meta)."""
    cmd = [_claude_bin(), "-p", prompt, "--output-format", "json", "--model", model]
    if mcp_config is not None:
        cmd += ["--mcp-config", mcp_config]
    if system_prompt is not None:
        cmd += ["--system-prompt", system_prompt]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=_PROJECT_ROOT)
    if result.returncode != 0:
        raise ClaudeError(f"claude exited {result.returncode}: stderr={result.stderr[:500]}")
    data = _json.loads(result.stdout)
    text = data.get("result", "")
    meta = {
        "duration_ms": data.get("duration_ms"),
        "input_tokens": (data.get("usage") or {}).get("input_tokens"),
        "output_tokens": (data.get("usage") or {}).get("output_tokens"),
        "cost_usd": data.get("cost_usd"),
    }
    return text, meta
```

Also add `import json as _json` at the top of `runner.py`.

- [ ] **Step 5: Run to verify PASS**

```bash
.venv/bin/pytest tests/test_cost_db.py tests/test_runner.py -v
```
Expected: all pass (existing runner tests must still pass)

- [ ] **Step 6: Commit**

```bash
git add newsparser/bots/core/cost_db.py newsparser/claude/runner.py tests/test_cost_db.py tests/test_runner.py
git commit -m "feat(bots): add cost_db and run_claude_json for cost tracking"
```

---

## Task 4: BotRegistry

**Files:**
- Create: `newsparser/bots/core/registry.py`
- Test: `tests/test_bot_registry.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_bot_registry.py
import textwrap
from pathlib import Path
import pytest
from newsparser.bots.core.registry import BotRegistry
from newsparser.bots.core.types import Cron, TelegramMatch


def _write_bot(bots_dir: Path, name: str, enabled: bool = True) -> None:
    pkg_dir = bots_dir / name
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("")
    trigger = "Cron('0 9 * * *')" if enabled else "Cron('0 9 * * *')"
    enabled_str = str(enabled)
    (pkg_dir / "bot.py").write_text(textwrap.dedent(f"""
        from newsparser.bots.core.types import Bot, Cron
        async def _run(ctx): pass
        BOT = Bot(name="{name}", triggers=[{trigger}], run=_run, enabled={enabled_str})
    """))


def test_registry_loads_enabled_bots(tmp_path):
    _write_bot(tmp_path, "alpha")
    _write_bot(tmp_path, "beta")
    registry = BotRegistry(bots_dir=tmp_path)
    registry.load()
    assert sorted(registry.names()) == ["alpha", "beta"]


def test_registry_skips_disabled(tmp_path):
    _write_bot(tmp_path, "alpha", enabled=True)
    _write_bot(tmp_path, "beta", enabled=False)
    registry = BotRegistry(bots_dir=tmp_path)
    registry.load()
    assert registry.names() == ["alpha"]


def test_registry_reload_picks_up_changes(tmp_path):
    _write_bot(tmp_path, "alpha")
    registry = BotRegistry(bots_dir=tmp_path)
    registry.load()
    assert "alpha" in registry.names()
    _write_bot(tmp_path, "gamma")
    registry.load()
    assert "gamma" in registry.names()


def test_cron_bots_returns_cron_triggers(tmp_path):
    _write_bot(tmp_path, "alpha")
    registry = BotRegistry(bots_dir=tmp_path)
    registry.load()
    pairs = registry.cron_bots()
    assert len(pairs) == 1
    bot, trigger = pairs[0]
    assert bot.name == "alpha"
    assert isinstance(trigger, Cron)


def test_telegram_bots_returns_telegram_triggers(tmp_path):
    pkg_dir = tmp_path / "mybot"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text("")
    (pkg_dir / "bot.py").write_text(textwrap.dedent("""
        from newsparser.bots.core.types import Bot, TelegramMatch
        async def _run(ctx): pass
        BOT = Bot(name="mybot", triggers=[TelegramMatch(r"^/foo")], run=_run)
    """))
    registry = BotRegistry(bots_dir=tmp_path)
    registry.load()
    pairs = registry.telegram_bots()
    assert len(pairs) == 1
    bot, trigger = pairs[0]
    assert isinstance(trigger, TelegramMatch)


def test_telegram_bots_catch_all_is_last(tmp_path):
    """Catch-all '.*' must come after specific patterns regardless of directory sort order."""
    for name, pattern in [("aaa", r".*"), ("zzz", r"^/specific")]:
        pkg_dir = tmp_path / name
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "bot.py").write_text(textwrap.dedent(f"""
            from newsparser.bots.core.types import Bot, TelegramMatch
            async def _run(ctx): pass
            BOT = Bot(name="{name}", triggers=[TelegramMatch(r"{pattern}")], run=_run)
        """))
    registry = BotRegistry(bots_dir=tmp_path)
    registry.load()
    pairs = registry.telegram_bots()
    # specific pattern must come before catch-all
    assert pairs[-1][1].pattern == ".*"
```

- [ ] **Step 2: Run to verify FAIL**

```bash
.venv/bin/pytest tests/test_bot_registry.py -v
```
Expected: `ImportError: cannot import name 'BotRegistry'`

- [ ] **Step 3: Implement BotRegistry**

```python
# newsparser/bots/core/registry.py
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from newsparser.bots.core.types import Bot, Cron, TelegramMatch

_DEFAULT_BOTS_DIR = Path(__file__).parent.parent  # newsparser/bots/


class BotRegistry:
    def __init__(self, bots_dir: Path | None = None) -> None:
        self._bots_dir = bots_dir or _DEFAULT_BOTS_DIR
        self._bots: list[Bot] = []

    def load(self) -> None:
        self._bots = []
        for bot_file in sorted(self._bots_dir.glob("*/bot.py")):
            mod_name = f"_bots_dynamic.{bot_file.parent.name}"
            sys.modules.pop(mod_name, None)
            spec = importlib.util.spec_from_file_location(mod_name, bot_file)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            try:
                spec.loader.exec_module(mod)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error("Failed to load %s: %s", bot_file, exc)
                continue
            bot = getattr(mod, "BOT", None)
            if isinstance(bot, Bot) and bot.enabled:
                self._bots.append(bot)

    def all(self) -> list[Bot]:
        return list(self._bots)

    def names(self) -> list[str]:
        return [b.name for b in self._bots]

    def cron_bots(self) -> list[tuple[Bot, Cron]]:
        return [
            (bot, t)
            for bot in self._bots
            for t in bot.triggers
            if isinstance(t, Cron)
        ]

    def telegram_bots(self) -> list[tuple[Bot, TelegramMatch]]:
        pairs = [
            (bot, t)
            for bot in self._bots
            for t in bot.triggers
            if isinstance(t, TelegramMatch)
        ]
        # Ensure catch-all patterns sort last so specific patterns match first.
        # Without this, 'tracker' (t) would beat 'weekly' (w) alphabetically.
        pairs.sort(key=lambda pair: pair[1].pattern == ".*")
        return pairs
```

- [ ] **Step 4: Run to verify PASS**

```bash
.venv/bin/pytest tests/test_bot_registry.py -v
```
Expected: all pass

- [ ] **Step 5: Commit**

```bash
git add newsparser/bots/core/registry.py tests/test_bot_registry.py
git commit -m "feat(bots): add BotRegistry with load/reload and trigger accessors"
```

---

## Task 5: Dispatcher

**Files:**
- Create: `newsparser/dispatcher.py`

No new test file — integration-level smoke only (PTB app is hard to unit-test; critical paths are covered by registry/context tests).

- [ ] **Step 1: Create the dispatcher**

```python
# newsparser/dispatcher.py
import asyncio
import logging
import os
import re
import traceback
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from apscheduler.triggers.cron import CronTrigger

from newsparser.bots.core.context import Context, TelegramSender
from newsparser.bots.core.registry import BotRegistry
from newsparser.bots.core.types import Bot, Cron

load_dotenv()
logger = logging.getLogger(__name__)

_WORKSPACE = Path(os.environ.get("WORKSPACE_DIR", "workspace"))
registry = BotRegistry()


def _make_ctx(bot_name: str, ptb_bot, chat_id: str | None = None, message=None) -> Context:
    return Context(
        bot_name=bot_name,
        workspace=_WORKSPACE,
        telegram=TelegramSender(
            bot=ptb_bot,
            chat_id=chat_id,
            alert_chat_id=os.environ.get("TELEGRAM_ALERT_CHAT_ID", ""),
        ),
        message=message,
    )


async def _run_with_guard(bot: Bot, ctx: Context) -> None:
    try:
        await bot.run(ctx)
    except Exception:
        tb = traceback.format_exc()
        logger.exception("Bot %s failed", bot.name)
        try:
            await ctx.telegram.send(f"❌ {bot.name} 실패\n{tb[-1500:]}")
        except Exception:
            pass


async def _handle_message(update: Update, ptb_ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    text = update.message.text.strip()
    chat_id = str(update.message.chat_id)

    allowed = os.environ.get("ALLOWED_CHAT_ID")
    if allowed and chat_id != allowed:
        logger.warning("Unauthorized chat_id %s — ignoring", chat_id)
        return

    matched: Bot | None = None
    for bot, trigger in registry.telegram_bots():
        if re.search(trigger.pattern, text):
            matched = bot
            break

    if matched is None:
        return

    ctx = _make_ctx(matched.name, ptb_ctx.bot, chat_id=chat_id, message=update.message)
    await _run_with_guard(matched, ctx)


async def _handle_reload(update: Update, ptb_ctx: ContextTypes.DEFAULT_TYPE) -> None:
    before = set(registry.names())
    job_queue = ptb_ctx.application.job_queue
    for job in job_queue.jobs():
        if job.name in before:
            job.schedule_removal()
    registry.load()
    _register_cron_jobs(ptb_ctx.application)
    after = set(registry.names())
    added = sorted(after - before)
    removed = sorted(before - after)
    lines = [f"✅ Reload 완료 — 활성: {sorted(after)}"]
    if added:
        lines.append(f"추가: {added}")
    if removed:
        lines.append(f"제거: {removed}")
    await update.message.reply_text("\n".join(lines))


def _make_cron_callback(bot: Bot):
    async def _cb(ptb_ctx: ContextTypes.DEFAULT_TYPE) -> None:
        ctx = _make_ctx(bot.name, ptb_ctx.bot)
        await _run_with_guard(bot, ctx)
    return _cb


def _register_cron_jobs(app: Application) -> None:
    for bot, trigger in registry.cron_bots():
        app.job_queue.run_custom(
            callback=_make_cron_callback(bot),
            job_kwargs={
                "trigger": CronTrigger.from_crontab(trigger.schedule, timezone=trigger.tz)
            },
            name=bot.name,
        )
        logger.info("Registered cron bot: %s  schedule=%s tz=%s", bot.name, trigger.schedule, trigger.tz)


def start() -> None:
    from newsparser.store.sqlite import init_db
    init_db()
    registry.load()
    logger.info("Loaded bots: %s", registry.names())

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    app = (
        Application.builder()
        .token(token)
        .read_timeout(30)
        .connect_timeout(10)
        .build()
    )

    _register_cron_jobs(app)
    app.add_handler(CommandHandler("reload", _handle_reload))
    app.add_handler(MessageHandler(filters.TEXT, _handle_message))

    logger.info("Dispatcher polling")
    app.run_polling()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    start()
```

- [ ] **Step 2: Verify module imports cleanly**

```bash
.venv/bin/python -c "import newsparser.dispatcher; print('OK')"
```
Expected: `OK` (no import errors)

- [ ] **Step 3: Commit**

```bash
git add newsparser/dispatcher.py
git commit -m "feat: add unified dispatcher with APScheduler + Telegram routing + /reload"
```

---

## Task 6: Migrate cycle bot

**Files:**
- Create: `newsparser/bots/cycle/__init__.py`
- Create: `newsparser/bots/cycle/bot.py`

- [ ] **Step 1: Create the bot**

```python
# newsparser/bots/cycle/__init__.py
# (empty)
```

```python
# newsparser/bots/cycle/bot.py
from datetime import datetime
from zoneinfo import ZoneInfo

from newsparser.bots import Bot, Cron, TelegramMatch, Context
from newsparser.scripts.run_cycle import main as _run_cycle

_KST = ZoneInfo("Asia/Seoul")


async def run(ctx: Context) -> None:
    slot = datetime.now(_KST).strftime("%Y-%m-%d-%H")
    if ctx.message:
        await ctx.telegram.send(f"⚙️ /cycle 시작: {slot}")
    await ctx.run_in_thread(_run_cycle, slot)
    if ctx.message:
        await ctx.telegram.send("✅ Cycle 완료")


BOT = Bot(
    name="cycle",
    triggers=[
        Cron("0 12,18,0,6 * * *", tz="Asia/Seoul"),
        TelegramMatch(r"^/cycle\b"),
    ],
    run=run,
)
```

- [ ] **Step 2: Verify bot loads via registry**

```bash
.venv/bin/python -c "
from newsparser.bots.core.registry import BotRegistry
r = BotRegistry()
r.load()
print(r.names())
"
```
Expected: `['cycle']` (only cycle bot exists so far)

- [ ] **Step 3: Commit**

```bash
git add newsparser/bots/cycle/__init__.py newsparser/bots/cycle/bot.py
git commit -m "feat(bots): migrate cycle job to bot framework"
```

---

## Task 7: Migrate weekly bot

**Files:**
- Create: `newsparser/bots/weekly/__init__.py`
- Create: `newsparser/bots/weekly/bot.py`

- [ ] **Step 1: Create the bot**

```python
# newsparser/bots/weekly/__init__.py
# (empty)
```

```python
# newsparser/bots/weekly/bot.py
from datetime import datetime
from zoneinfo import ZoneInfo

from newsparser.bots import Bot, Cron, TelegramMatch, Context
from newsparser.scripts.run_weekly import main as _run_weekly

_KST = ZoneInfo("Asia/Seoul")


async def run(ctx: Context) -> None:
    date = datetime.now(_KST).strftime("%Y-%m-%d")
    if ctx.message:
        await ctx.telegram.send(f"⚙️ /weekly 시작: {date}")
    await ctx.run_in_thread(_run_weekly, date)
    if ctx.message:
        await ctx.telegram.send("✅ Weekly 완료")


BOT = Bot(
    name="weekly",
    triggers=[
        Cron("0 9 * * 1", tz="Asia/Seoul"),
        TelegramMatch(r"^/weekly\b"),
    ],
    run=run,
)
```

- [ ] **Step 2: Verify**

```bash
.venv/bin/python -c "
from newsparser.bots.core.registry import BotRegistry
r = BotRegistry(); r.load(); print(r.names())
"
```
Expected: `['cycle', 'weekly']`

- [ ] **Step 3: Commit**

```bash
git add newsparser/bots/weekly/__init__.py newsparser/bots/weekly/bot.py
git commit -m "feat(bots): migrate weekly job to bot framework"
```

---

## Task 8: Migrate reflect bot

**Files:**
- Create: `newsparser/bots/reflect/__init__.py`
- Create: `newsparser/bots/reflect/bot.py`

- [ ] **Step 1: Create the bot**

```python
# newsparser/bots/reflect/__init__.py
# (empty)
```

```python
# newsparser/bots/reflect/bot.py
from datetime import datetime
from zoneinfo import ZoneInfo

from newsparser.bots import Bot, Cron, TelegramMatch, Context
from newsparser.scripts.run_reflect import main as _run_reflect

_KST = ZoneInfo("Asia/Seoul")


async def run(ctx: Context) -> None:
    date = datetime.now(_KST).strftime("%Y-%m-%d")
    if ctx.message:
        await ctx.telegram.send(f"⚙️ /reflect 시작: {date}")
    await ctx.run_in_thread(_run_reflect, date)
    if ctx.message:
        await ctx.telegram.send("✅ Reflect 완료")


BOT = Bot(
    name="reflect",
    triggers=[
        Cron("0 21 * * 0", tz="Asia/Seoul"),
        TelegramMatch(r"^/reflect\b"),
    ],
    run=run,
)
```

- [ ] **Step 2: Verify**

```bash
.venv/bin/python -c "
from newsparser.bots.core.registry import BotRegistry
r = BotRegistry(); r.load(); print(r.names())
"
```
Expected: `['cycle', 'reflect', 'weekly']`

- [ ] **Step 3: Commit**

```bash
git add newsparser/bots/reflect/__init__.py newsparser/bots/reflect/bot.py
git commit -m "feat(bots): migrate reflect job to bot framework"
```

---

## Task 9: Migrate market_daily bot

**Files:**
- Create: `newsparser/bots/market_daily/__init__.py`
- Create: `newsparser/bots/market_daily/bot.py`

- [ ] **Step 1: Create the bot**

```python
# newsparser/bots/market_daily/__init__.py
# (empty)
```

```python
# newsparser/bots/market_daily/bot.py
from newsparser.bots import Bot, Cron, Context
from newsparser.scripts.fetch_market_daily import main as _run_market


async def run(ctx: Context) -> None:
    await ctx.run_in_thread(_run_market)


BOT = Bot(
    name="market_daily",
    triggers=[Cron("30 7 * * *", tz="Asia/Seoul")],
    run=run,
)
```

- [ ] **Step 2: Verify**

```bash
.venv/bin/python -c "
from newsparser.bots.core.registry import BotRegistry
r = BotRegistry(); r.load(); print(r.names())
"
```
Expected: `['cycle', 'market_daily', 'reflect', 'weekly']`

- [ ] **Step 3: Commit**

```bash
git add newsparser/bots/market_daily/__init__.py newsparser/bots/market_daily/bot.py
git commit -m "feat(bots): migrate market_daily job to bot framework"
```

---

## Task 10: Migrate tracker bot (catch-all + /rebuild)

**Files:**
- Create: `newsparser/bots/tracker/__init__.py`
- Create: `newsparser/bots/tracker/bot.py`

The tracker bot is a catch-all (`TelegramMatch(r".*")`). It must fire last. `registry.telegram_bots()` sorts catch-all patterns to the end (fixed in Task 4), so directory naming doesn't matter — `tracker` will always lose to more specific patterns regardless of alphabetical position.

The bot also handles `/rebuild`, which triggers a Docker image rebuild via `Popen(..., start_new_session=True)` so the build survives the dispatcher container being replaced.

- [ ] **Step 1: Create the bot**

```python
# newsparser/bots/tracker/__init__.py
# (empty)
```

```python
# newsparser/bots/tracker/bot.py
import subprocess

from newsparser.bots import Bot, TelegramMatch, Context
from newsparser.bot.tracker import run_tracker


async def run(ctx: Context) -> None:
    if ctx.message is None:
        return

    text = ctx.message.text.strip()
    chat_id = str(ctx.message.chat_id)

    if text == "/rebuild":
        await ctx.telegram.send("🔨 이미지 빌드 시작. 잠시 후 재연결됩니다.")
        _docker_rebuild()
        return

    await ctx.telegram.send("🔍 분석 중...")
    answer = await ctx.run_in_thread(run_tracker, chat_id=chat_id, query=text)
    await ctx.telegram.send(answer)


def _docker_rebuild() -> None:
    subprocess.Popen(
        ["docker", "compose", "up", "-d", "--build", "dispatcher"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


BOT = Bot(
    name="tracker",
    triggers=[TelegramMatch(r".*")],
    run=run,
)
```

- [ ] **Step 2: Verify all bots load**

```bash
.venv/bin/python -c "
from newsparser.bots.core.registry import BotRegistry
r = BotRegistry(); r.load(); print(r.names())
"
```
Expected: `['cycle', 'market_daily', 'reflect', 'tracker', 'weekly']`

- [ ] **Step 3: Verify tracker is last in telegram_bots (catch-all must be last)**

```bash
.venv/bin/python -c "
from newsparser.bots.core.registry import BotRegistry
r = BotRegistry(); r.load()
pairs = r.telegram_bots()
print([(b.name, t.pattern) for b, t in pairs])
"
```
Expected: tracker with `.*` appears **after** cycle/weekly/reflect patterns.

- [ ] **Step 4: Commit**

```bash
git add newsparser/bots/tracker/__init__.py newsparser/bots/tracker/bot.py
git commit -m "feat(bots): migrate tracker as catch-all bot with /rebuild support"
```

---

## Task 11: Docker Compose + env + remove run.sh

**Files:**
- Modify: `docker-compose.yml`
- Modify/Create: `.env.example`
- Delete: `run.sh`

- [ ] **Step 1: Add dispatcher service to docker-compose.yml**

Add the following service block (before `volumes:`):

```yaml
  dispatcher:
    build: .
    command: .venv/bin/python -m newsparser.dispatcher
    env_file: .env
    environment:
      NEO4J_URI: bolt://neo4j:7687
      WORKSPACE_DIR: /app/workspace
    volumes:
      - .:/app
      - /var/run/docker.sock:/var/run/docker.sock
    depends_on:
      neo4j:
        condition: service_healthy
    restart: unless-stopped
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "5"
```

Also add `logging` blocks to existing services for consistency:

```yaml
  neo4j:
    # ... existing config ...
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "5"

  poller:
    # ... existing config ...
    logging:
      driver: json-file
      options:
        max-size: "20m"
        max-file: "5"
```

- [ ] **Step 2: Update .env.example**

If `.env.example` exists, add these entries. If not, create it:

```bash
# Telegram
TELEGRAM_BOT_TOKEN=your-token-here
ALLOWED_CHAT_ID=your-chat-id
TELEGRAM_ALERT_CHAT_ID=your-chat-id   # receives cron failure alerts

# Claude
CLAUDE_CODE_OAUTH_TOKEN=your-setup-token-here
CLAUDE_BIN=claude

# Neo4j
NEO4J_PASSWORD=your-password
NEO4J_URI=bolt://localhost:7687

# Other
IS_SANDBOX=
```

- [ ] **Step 3: Verify dispatcher module entry point works**

```bash
.venv/bin/python -m newsparser.dispatcher --help 2>&1 || true
```
Expected: no `ModuleNotFoundError` (may exit non-zero without TELEGRAM_BOT_TOKEN set, that's OK)

- [ ] **Step 4: Delete run.sh**

```bash
git rm run.sh
```

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat: add dispatcher container, docker socket mount, remove run.sh"
```

---

## Task 12: Smoke test full stack

Verify the dispatcher starts and the registry is wired correctly before declaring done.

- [ ] **Step 1: Run full test suite**

```bash
.venv/bin/pytest tests/ -v --tb=short 2>&1 | tail -30
```
Expected: all pre-existing tests pass; new tests pass; no regressions

- [ ] **Step 2: Verify dispatcher imports all bots without errors**

```bash
.venv/bin/python -c "
import os
os.environ.setdefault('TELEGRAM_BOT_TOKEN', 'dummy')
os.environ.setdefault('WORKSPACE_DIR', 'workspace')
from newsparser.bots.core.registry import BotRegistry
r = BotRegistry()
r.load()
print('Bots:', r.names())
print('Cron jobs:', [(b.name, t.schedule) for b, t in r.cron_bots()])
print('Telegram bots:', [(b.name, t.pattern) for b, t in r.telegram_bots()])
"
```
Expected output (tracker must be last in telegram_bots regardless of alphabetical position):
```
Bots: ['cycle', 'market_daily', 'reflect', 'tracker', 'weekly']
Cron jobs: [('cycle', '0 12,18,0,6 * * *'), ('market_daily', '30 7 * * *'), ('reflect', '0 21 * * 0'), ('weekly', '0 9 * * 1')]
Telegram bots: [('cycle', '^/cycle\\b'), ('reflect', '^/reflect\\b'), ('weekly', '^/weekly\\b'), ('tracker', '.*')]
```

- [ ] **Step 3: Final commit**

```bash
git commit --allow-empty -m "chore: bot framework migration complete"
```

---

## What Is NOT In This Plan

- Per-bot log files via `ctx.logger` writing to `workspace/logs/<name>/<ts>.log` — `ctx.logger` returns a stdlib Logger; configuring file handlers per-run is a follow-up
- Neo4j client on Context (`ctx.neo4j`) — add when a bot needs it
- Deletion of old `newsparser/bot/telegram_bot.py` — kept as dead code until confident dispatcher is stable in production
