# 스케줄 작업 API 직접 호출 전환 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** cycle·weekly·reflect를 `claude -p` 서브프로세스에서 Anthropic SDK 직접 호출(도구 없는 단일 호출)로 옮기고, 사이클 상한을 120건으로 올린다. tracker는 그대로 둔다.

**Architecture:** 신규 `newsparser/claude/api.py`가 공용 SDK 클라이언트(`NEWSBOT_API_KEY`)와 스트리밍 단일 호출 `ask_claude()`를 제공하고, 실행을 `runner.py`의 기존 레지스트리에 등록해 `job_status`/`kill_job`이 그대로 동작한다. Python이 입력(기사·직전 리포트·관심사·무시목록·수요 다이제스트)을 모아 프롬프트로 넘기고, 출력(리포트 텍스트 또는 JSON)을 받아 파일 쓰기·그래프 반영·mark_processed를 직접 수행한다. 프롬프트는 `newsparser/prompts/*.md`로 옮기고, 스타일 규칙은 `CLAUDE.md`의 Style 절에서 읽어 붙인다.

**Tech Stack:** Python 3.12+, `anthropic` 1.x SDK (`messages.stream` + `get_final_message`, `output_config.format` 구조화 출력), pytest.

**Spec:** `docs/superpowers/specs/2026-10-18-scheduled-jobs-api-migration-design.md`

## Global Constraints

- API 키 환경변수는 `NEWSBOT_API_KEY` 하나. `ANTHROPIC_API_KEY` 금지(tracker `claude -p`가 상속해 API 과금으로 넘어감). `NEWSBOT_HAIKU_API_KEY`·OAuth 대체 경로는 코드·문서에서 제거.
- 스케줄 작업 모델은 `claude-sonnet-5` (`SCHEDULED_MODEL`), `thinking={"type": "adaptive"}`, `output_config.effort="high"`, `max_tokens=64000`, 스트리밍.
- 벽시계 타임아웃: cycle 3600초, weekly·reflect 1500초.
- `CYCLE_MAX_ARTICLES = 120`. `CYCLE_CLAUDE_ATTEMPTS = 3` 유지.
- 오류 계약: API/전송/비정상 stop_reason은 `ClaudeError`, kill은 `ClaudeKilled` (둘 다 `newsparser.claude.runner`).
- tracker(`newsparser/bot/tracker.py`, `run_claude`)는 변경하지 않는다.
- 텔레그램 메시지 형식(`[{CATEGORY}]` 렌더, `[WEEKLY] `, `[REFLECT] ` 접두)과 워크스페이스 파일 경로는 유지.
- 테스트는 `.venv/bin/pytest`(Windows 개발 환경이면 `.venv/Scripts/pytest`)로 실행. `uv run` 금지.

## Review Focus

1. **진행 중 kill** — 스트림 도중 `kill_job`이 오면 `ClaudeKilled`가 나야 한다(`ClaudeError` 일반 실패로 보고되면 JobManager가 ❌로 오인). → Task 2 `test_ask_claude_kill_raises_claude_killed`.
2. **같은 슬롯 재실행/캐치업** — 직전 리포트로 자기 자신이나 더 뒤 슬롯을 집으면 안 된다. → Task 3 `test_previous_report_skips_current_and_later_slots`.
3. **잘린 출력** — `stop_reason == "max_tokens"`인 cycle 응답은 리포트로 저장되면 안 되고 재시도돼야 한다. → Task 2 `test_ask_claude_raises_on_max_tokens`, Task 5 `test_run_cycle_retries_on_truncated_output`.
4. **그래프 반영 실패** — 리포트 저장 후 그래프 반영이 실패해도 기사는 processed, 텔레그램 경고, daily log `GRAPH-FAIL`. → Task 5 `test_run_cycle_graph_failure_marks_processed_and_warns`.
5. **reflect가 빈/깨진 값 반환** — 빈 관심사 프로필이 기존 파일을 덮어쓰거나, 범위 밖 웨이트가 그대로 기록되면 안 된다. → Task 7 `test_run_reflect_keeps_profile_when_empty`, Task 6 `test_sanitize_weights_clamps_floors_and_ceiling`.

---

## File Structure

- Create `newsparser/claude/api.py` — 공용 SDK 클라이언트, `ask_claude()`, `check_credentials()`.
- Modify `newsparser/claude/runner.py` — `_Run`을 취소 콜러블 기반으로 일반화, 공개 등록 API.
- Modify `newsparser/claude/haiku.py` — 클라이언트를 `api.get_client()`로 위임.
- Create `newsparser/prompts/__init__.py`, `cycle.md`, `weekly.md`, `reflect.md` — 프롬프트 로딩과 Style 절 추출.
- Create `newsparser/scheduler/reports.py` — 리포트 목록·직전 리포트·Graph 블록 제거.
- Modify `newsparser/scripts/apply_graph.py` — 예외를 던지는 `apply()` 분리, 마커 제거.
- Modify `newsparser/scripts/run_cycle.py`, `run_weekly.py`, `run_reflect.py`.
- Modify `newsparser/triage.py` — `sanitize_weights()`, 스냅샷 제거.
- Modify `newsparser/scheduler/demand.py` — 파일 쓰기 제거.
- Modify `newsparser/mcp_server.py` — pid 표시(None → `api`), start_job docstring. (`jobs.py`는 pid를 그대로 JSON에 실으므로 변경 없음.)
- Delete `newsparser/claude/policy.py`, `.claude/commands/{cycle,weekly,reflect}.md`.
- Docs: `CLAUDE_DEV.md`, `CLAUDE.md`, `README.md`, `.env.example`.

---

### Task 1: runner 레지스트리를 API 실행과 공유하도록 일반화

**Files:**
- Modify: `newsparser/claude/runner.py:49-124, 158-176, 208-285`
- Modify: `newsparser/mcp_server.py:504` (pid 표시)
- Test: `tests/test_runner.py`

**Interfaces:**
- Produces:
  - `runner.register_run(prompt: str, cancel: Callable[[], None], pid: int | None = None) -> _Run` — `CURRENT_JOB.get()`로 job_id를 잡아 `_ACTIVE`에 등록.
  - `runner.unregister_run(run: _Run) -> None` — `_ACTIVE`에서 빼고 denials를 `_JOB_DENIALS`에 옮김(기존 `_run_stream` finally 블록 로직).
  - `runner.note_activity(run: _Run, desc: str, *, turn: bool = False) -> None` — `last_event_at`/`activity` 갱신, `turn=True`면 `turns += 1`, 기존 `_NOTIFY_INTERVAL` 스로틀로 `on_activity` 호출.
  - `_Run` 속성: `run_id, job_id, pid, prompt_head, started_at, last_event_at, activity, turns, killed, denials` + `cancel`. `proc` 속성은 제거.
  - `kill_job(job_id)`는 `run.killed = True` 후 `run.cancel()` 호출(예외는 삼킴).
  - `active_runs()`의 `pid`는 API 실행이면 `None`.

- [ ] **Step 1: 실패하는 테스트 작성** (`tests/test_runner.py`)

```python
def test_register_run_visible_in_active_runs_and_killable():
    cancelled = []
    token = runner.CURRENT_JOB.set(42)
    try:
        run = runner.register_run("weekly prompt", cancel=lambda: cancelled.append(True))
    finally:
        runner.CURRENT_JOB.reset(token)
    snap = [r for r in runner.active_runs() if r["run_id"] == run.run_id]
    assert snap and snap[0]["job_id"] == 42 and snap[0]["pid"] is None
    assert runner.kill_job(42) == 1
    assert cancelled == [True] and run.killed
    runner.unregister_run(run)
    assert all(r["run_id"] != run.run_id for r in runner.active_runs())

def test_note_activity_updates_desc_and_turns():
    run = runner.register_run("p", cancel=lambda: None)
    runner.note_activity(run, "응답 작성", turn=True)
    snap = next(r for r in runner.active_runs() if r["run_id"] == run.run_id)
    assert snap["activity"] == "응답 작성" and snap["turns"] == 1
    runner.unregister_run(run)
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/pytest tests/test_runner.py -k "register_run or note_activity" -v` → `AttributeError: ... register_run`.

- [ ] **Step 3: 구현** — `_Run.__init__(self, run_id, job_id, prompt, cancel, pid=None)`. `_run_stream`은 `register_run(prompt, cancel=proc.kill, pid=proc.pid)` / finally에서 `unregister_run(run)`을 쓰도록 바꾸고, `_note_event`는 `note_activity`를 거치게 한다(denials 수집은 `_note_event`에 그대로). `mcp_server.py:504`의 `pid {act.get('pid', '?')}`는 `pid`가 None이면 `api`로 표시.

- [ ] **Step 4: 통과 확인** — `.venv/bin/pytest tests/test_runner.py tests/test_jobs.py tests/test_mcp_jobs.py -v` → 전부 PASS (기존 서브프로세스 테스트 포함).

- [ ] **Step 5: Commit** — `git commit -m "refactor: runner 실행 레지스트리를 취소 콜러블 기반으로 일반화"`

---

### Task 2: `api.py` — 공용 클라이언트·`ask_claude`·인증 통일

**Files:**
- Create: `newsparser/claude/api.py`
- Modify: `newsparser/claude/haiku.py:1-52` (클라이언트 위임, OAuth 제거)
- Modify: `newsparser/dispatcher.py:207`, `newsparser/collector/run_poller.py:141` (시작 시 `check_credentials()`)
- Test: `tests/test_api.py` (신규)

**Interfaces:**
- Consumes: Task 1의 `register_run` / `unregister_run` / `note_activity`, `ClaudeError`, `ClaudeKilled`.
- Produces:
  - `api.API_KEY_ENV = "NEWSBOT_API_KEY"`, `api.SCHEDULED_MODEL = "claude-sonnet-5"`.
  - `api.get_client() -> anthropic.Anthropic` — 스레드 안전 싱글톤, `NEWSBOT_API_KEY` 없으면 `ClaudeError("no API credential: set NEWSBOT_API_KEY")`.
  - `api.reset_client() -> None`.
  - `api.check_credentials() -> bool` — 키 없으면 `logger.error(...)` 후 False.
  - `api.ask_claude(prompt: str, system: str, *, timeout: float, max_tokens: int = 64000, output_schema: dict | None = None, model: str = SCHEDULED_MODEL) -> str` — 텍스트 블록 연결 반환. `output_schema`가 있으면 반환 텍스트는 JSON 문서.
  - `haiku.reset_client`는 `api.reset_client`를 그대로 노출(기존 호출부 호환).

- [ ] **Step 1: 실패하는 테스트 작성** — `api.get_client`를 패치해 가짜 클라이언트를 돌려준다. 가짜 `messages.stream(**kw)`는 컨텍스트 매니저로, 이터레이션 시 이벤트 몇 개를 내고 `get_final_message()`가 `stop_reason`·`content`(text 블록)를 가진 객체를 돌려준다. `close()` 호출 시 이터레이션이 예외로 끝나게 한다.

```python
def test_ask_claude_returns_text_and_passes_params(fake_client):
    fake_client.final = _msg("end_turn", "리포트 본문")
    assert api.ask_claude("u", "sys", timeout=10) == "리포트 본문"
    kw = fake_client.last_kwargs
    assert kw["model"] == "claude-sonnet-5" and kw["max_tokens"] == 64000
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["output_config"]["effort"] == "high"
    assert kw["system"] == "sys"

def test_ask_claude_sets_json_schema_format(fake_client):
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    fake_client.final = _msg("end_turn", "{}")
    api.ask_claude("u", "s", timeout=10, output_schema=schema)
    assert fake_client.last_kwargs["output_config"]["format"] == {"type": "json_schema", "schema": schema}

def test_ask_claude_raises_on_max_tokens(fake_client):
    fake_client.final = _msg("max_tokens", "잘린")
    with pytest.raises(ClaudeError, match="max_tokens"):
        api.ask_claude("u", "s", timeout=10)

def test_ask_claude_raises_on_refusal(fake_client):
    fake_client.final = _msg("refusal", "")
    with pytest.raises(ClaudeError, match="refusal"):
        api.ask_claude("u", "s", timeout=10)

def test_ask_claude_kill_raises_claude_killed(fake_client):
    fake_client.block_until_closed = True     # 이터레이션이 close()까지 대기
    token = runner.CURRENT_JOB.set(7)
    try:
        t = threading.Timer(0.2, lambda: runner.kill_job(7)); t.start()
        with pytest.raises(ClaudeKilled):
            api.ask_claude("u", "s", timeout=10)
    finally:
        runner.CURRENT_JOB.reset(token)
    assert not [r for r in runner.active_runs() if r["job_id"] == 7]

def test_ask_claude_wall_clock_timeout(fake_client):
    fake_client.block_until_closed = True
    with pytest.raises(ClaudeError, match="timed out after 0.3s"):
        api.ask_claude("u", "s", timeout=0.3)

def test_ask_claude_wraps_api_errors(fake_client):
    fake_client.raise_on_enter = anthropic.APIConnectionError(request=httpx.Request("POST", "https://x"))
    with pytest.raises(ClaudeError):
        api.ask_claude("u", "s", timeout=10)

def test_get_client_requires_newsbot_api_key(monkeypatch):
    monkeypatch.delenv("NEWSBOT_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "tok")   # 더 이상 대체 경로 아님
    api.reset_client()
    with pytest.raises(ClaudeError, match="NEWSBOT_API_KEY"):
        api.get_client()

def test_check_credentials_logs_error_when_missing(monkeypatch, caplog):
    monkeypatch.delenv("NEWSBOT_API_KEY", raising=False)
    assert api.check_credentials() is False
    assert "NEWSBOT_API_KEY" in caplog.text
```

(`httpx`는 SDK 1.x에서 `httpx2` — import는 `import httpx2 as httpx`.)

- [ ] **Step 2: 실패 확인** — `.venv/bin/pytest tests/test_api.py -v` → `ModuleNotFoundError: newsparser.claude.api`.

- [ ] **Step 3: 구현** — `ask_claude`:
  - `client.messages.stream(model, max_tokens, system, messages=[{"role":"user","content":prompt}], thinking={"type":"adaptive"}, output_config={"effort":"high", ["format": ...]})`를 연다.
  - 열린 stream으로 `register_run(prompt, cancel=stream.close)`; `threading.Timer(timeout, ...)`가 `timed_out` 플래그를 세우고 `stream.close()`.
  - 이벤트를 돌며 `note_activity(run, "응답 작성")`(스로틀은 runner가 함), 끝나면 `get_final_message()`.
  - 예외 처리 순서: `run.killed`면 `ClaudeKilled`, `timed_out`면 `ClaudeError(f"claude timed out after {timeout}s")`, `anthropic.APIStatusError`/`APIError`는 `ClaudeError`로 감싼다. finally에서 타이머 취소·`unregister_run`.
  - `stop_reason != "end_turn"`이면 `ClaudeError(f"claude stopped with stop_reason={...}")`.
  - SDK 재시도는 기본값 유지(벽시계 상한은 타이머가 보장 — 모듈 주석에 한 줄).
  - `haiku.py`: `_build_client`/`_get_client`/`_OAUTH_BETA`/`_API_KEY_ENV` 제거, `api.get_client()` 사용, 모듈 docstring과 15-17행 주석을 새 인증 규칙에 맞게.
  - dispatcher·poller의 `init_db()` 직후 `check_credentials()` 호출.

- [ ] **Step 4: 통과 확인** — `.venv/bin/pytest tests/test_api.py tests/test_classifier.py tests/test_triage.py tests/test_resolver.py tests/test_market_headlines.py -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat: API 직접 호출 헬퍼 ask_claude 추가, 인증을 NEWSBOT_API_KEY로 통일"`

---

### Task 3: 프롬프트 패키지와 리포트 컨텍스트 헬퍼

**Files:**
- Create: `newsparser/prompts/__init__.py`, `newsparser/prompts/cycle.md`, `newsparser/prompts/weekly.md`, `newsparser/prompts/reflect.md`
- Create: `newsparser/scheduler/reports.py`
- Test: `tests/test_prompts.py`, `tests/test_reports.py` (신규)

**Interfaces:**
- Produces:
  - `prompts.style_rules() -> str` — 프로젝트 루트 `CLAUDE.md`의 `## Style` 줄부터 다음 `## ` 줄 직전까지(헤더 제외). 두 경로(tracker·스케줄)가 같은 문구를 쓰도록 복제하지 않고 읽는다.
  - `prompts.system_prompt(name: str) -> str` — `style_rules() + "\n\n" + (<name>.md 내용)`. `name ∈ {"cycle","weekly","reflect"}`.
  - `reports.strip_graph_block(text: str) -> str` — `## Graph updates`부터 그 블록 끝까지 제거하되, 블록 뒤에 오는 `트리아지:`로 시작하는 줄은 보존.
  - `reports.previous_report(workspace: Path, category: str, slot: str) -> Path | None` — `cycles/{category}/*.md` 중 stem이 `slot`보다 사전순으로 작은 것의 최댓값.
  - `reports.reports_between(workspace: Path, categories: Sequence[str], start: date, end: date, max_chars: int = 1_000_000) -> list[tuple[str, str]]` — `(f"{category}/{stem}", strip_graph_block(text))` 목록, 날짜(파일명 앞 10자) `start <= d <= end`, 시간순. 합계 글자수가 `max_chars`를 넘으면 오래된 것부터 빼고 경고 로그.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_style_rules_reads_claude_md_style_section():
    s = prompts.style_rules()
    assert "Korean by default" in s and "No filler phrases" in s
    assert "MCP tools" not in s and "## Style" not in s

def test_system_prompt_has_no_tool_instructions():
    for name in ("cycle", "weekly", "reflect"):
        p = prompts.system_prompt(name)
        for banned in ("$ARGUMENTS", "Glob", "Read `", ".venv/bin/python", "stdout", "start_job"):
            assert banned not in p, (name, banned)

def test_cycle_prompt_keeps_report_format_verbatim():
    p = prompts.system_prompt("cycle")
    assert "• (중요도 0.NN) 한 줄 헤드라인. 본문 2–4문장." in p
    assert "## Graph updates" in p and "유효 Predicate: INFLUENCES" in p

def test_strip_graph_block_keeps_triage_stats():
    text = "새 소식\n• a\n\n## Graph updates\n### Entities\n- NEW | X\n\n\n트리아지: 후보 3건 · threshold 0.30 통과 2건\n"
    out = reports.strip_graph_block(text)
    assert "Graph updates" not in out and "NEW | X" not in out
    assert "트리아지: 후보 3건" in out and "• a" in out

def test_previous_report_skips_current_and_later_slots(tmp_path):
    d = tmp_path / "cycles" / "tech"; d.mkdir(parents=True)
    for s in ("2026-05-08-09", "2026-05-08-12", "2026-05-08-15"):
        (d / f"{s}.md").write_text(s)
    assert reports.previous_report(tmp_path, "tech", "2026-05-08-12").stem == "2026-05-08-09"
    assert reports.previous_report(tmp_path, "tech", "2026-05-08-09") is None

def test_reports_between_filters_dates_and_drops_oldest_over_budget(tmp_path, caplog):
    ...  # 5/1, 5/7, 5/8 파일 → start=5/2,end=5/8 이면 5/7·5/8만, max_chars를 한 파일 크기로 주면 5/8만 + 경고
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/pytest tests/test_prompts.py tests/test_reports.py -v` → ModuleNotFoundError.

- [ ] **Step 3: 구현 — 프롬프트 재작성 규칙**
  - `cycle.md`: `.claude/commands/cycle.md`에서 1-5행($ARGUMENTS·start_job), "작업" 1·2·4-7단계(파일 읽기·Glob·Bash·stdout), "Bash는 … 두 개만 허용" 문장을 뺀다. "사용자 관심사"·"무시 목록"의 "`…md`를 읽고/Read"는 "입력의 `<interests>`/`<ignore>` 블록"으로, 직전 리포트는 `<previous_report>`, 입력파일은 `<input>`으로 바꾼다. 응답 지시 추가: "응답 전체가 그대로 리포트 파일 내용이 된다. 리포트 앞뒤에 다른 말을 붙이지 않는다." 그 밖의 규칙·형식·라벨·Event 이름 규칙은 **글자 그대로** 옮긴다. `{category}`·`{slot}` 같은 자리표시는 시스템 프롬프트에 남기지 않는다(카테고리별 지시는 "카테고리 컨텍스트" 절을 그대로 두고, 이번 카테고리는 사용자 메시지에 명시).
  - `weekly.md`: 1-3단계(파일 나열·읽기)는 "입력의 `<reports>`(Graph 블록 제거됨)와 `<demand>`"로, 5-6단계는 "`briefing`에 브리핑 전문, `headlines`에 3–5개 한 줄 핵심(불릿 기호 없이)"으로.
  - `reflect.md`: 1-3단계는 `<interests_tech>`·`<interests_markets>`·`<reports>`·`<demand>`·`<bucket_axis>` 블록으로, 5단계 "덮어쓴다"는 "`interests_tech`/`interests_markets`에 같은 마크다운 표 형식의 전체 프로필", 6단계 파일 쓰기는 "`triage_weights_tech`/`triage_weights_markets`"로, 7단계 stdout은 "`changes`에 한 줄씩(변경 없으면 빈 배열)"으로. 6단계의 하한·상한 문구는 유지(Python도 클램프하지만 모델이 알아야 함).
  - `newsparser/prompts/`의 `.md`가 패키지 데이터로 읽히도록 `Path(__file__).parent / f"{name}.md"`로 연다.

- [ ] **Step 4: 통과 확인** — `.venv/bin/pytest tests/test_prompts.py tests/test_reports.py -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat: API용 프롬프트 패키지와 리포트 컨텍스트 헬퍼 추가"`

---

### Task 4: `apply_graph`에서 예외를 던지는 `apply()` 분리

**Files:**
- Modify: `newsparser/scripts/apply_graph.py:18-84`
- Test: `tests/test_apply_graph.py`

**Interfaces:**
- Produces:
  - `apply_graph.apply(category: str, slot: str) -> tuple[int, int]` — (entities, relations) 개수. 리포트 없으면 `FileNotFoundError`. 그래프 쓰기 예외는 그대로 전파. annotate 실패는 지금처럼 경고만.
  - `apply_graph.main(argv)` — 인자 검사 후 `apply()` 호출, `FileNotFoundError`면 stderr + `sys.exit(1)`(CLI 동작 유지), 성공 시 기존 print.
  - `marker_path()`와 `.graph-applied` 마커 쓰기는 제거.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_apply_returns_counts_and_raises_when_report_missing(tmp_path):
    # 기존 test_apply_graph_calls_apply_graph_updates와 같은 리포트·패치 구성으로
    entities, relations = script.apply("tech", "2026-05-08-12")
    ents_arg, rels_arg = mock_apply_graph_updates.call_args[0][:2]
    assert (entities, relations) == (len(ents_arg), len(rels_arg))
    with pytest.raises(FileNotFoundError):
        script.apply("tech", "9999-99-99-99")

def test_apply_does_not_write_marker(tmp_path):
    script.apply("tech", "2026-05-08-12")
    assert not list((tmp_path / "workspace" / "cycles" / "tech").glob("*.graph-applied"))
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/pytest tests/test_apply_graph.py -v` → `AttributeError: apply`.
- [ ] **Step 3: 구현** — 위 Interfaces대로.
- [ ] **Step 4: 통과 확인** — `.venv/bin/pytest tests/test_apply_graph.py tests/test_restore_graph_from_cycles.py -v` → PASS (기존 `main` 테스트 포함).
- [ ] **Step 5: Commit** — `git commit -m "refactor: apply_graph에 예외 기반 apply() 분리, 적용 마커 제거"`

---

### Task 5: run_cycle을 API 호출로 전환, 상한 120

**Files:**
- Modify: `newsparser/scripts/run_cycle.py:13-16, 41-49, 326-399`
- Test: `tests/test_run_cycle_script.py` (전면 수정)

**Interfaces:**
- Consumes: `api.ask_claude`, `prompts.system_prompt("cycle")`, `reports.previous_report`, `apply_graph.apply`, `mark_processed`(store).
- Produces: `run_cycle.CYCLE_MAX_ARTICLES = 120`; 내부 `_build_cycle_prompt(slot: str, category: str, workspace: Path, input_text: str) -> str`.

사용자 메시지 형식(`_build_cycle_prompt`):
```
이번 사이클: {slot} KST, 카테고리 {category}

<interests>
{interests_{category}.md 내용, 없으면 "없음"}
</interests>

<ignore>
{ignore.md 내용, 없으면 "없음"}
</ignore>

<previous_report slot="{prev.stem}">
{직전 리포트 전문 — 없으면 태그 대신 "<previous_report>없음</previous_report>"}
</previous_report>

<input>
{스냅샷이 붙은 input.md 전문}
</input>
```

흐름 변경:
1. 입력파일 생성·스냅샷 prepend까지는 그대로. guids 파일도 그대로 쓴다(`apply()`가 src 인덱스 해석에 씀).
2. 시도 루프: `ask_claude(prompt, system_prompt("cycle"), timeout=3600)`. `ClaudeKilled`는 재전파, `ClaudeError`는 로그 후 다음 시도, 빈 문자열(공백만)도 실패 시도로 보고 결과 일부를 로그. 3회 실패면 `ClaudeError(f"no report after {CYCLE_CLAUDE_ATTEMPTS} claude attempts ({report_path})")` — 기사는 pending.
3. 성공 텍스트를 `report_path`에 쓴다(이미 있으면 경고 로그 후 덮어쓰기).
4. `apply_graph.apply(category, slot)`; 예외 시 `logger.error`, daily log `cycle {category}-{slot} GRAPH-FAIL {type}: {exc}`, `send_long_message(f"⚠️ [{category.upper()}] {slot} 그래프 반영 실패: {exc}")`(전송 실패는 삼킴). 어느 경우든 다음 단계로.
5. guids 파일의 guid를 `mark_processed`하고 guids 파일 삭제(기존 safety-net 코드가 이제 정규 경로).
6. 텔레그램 렌더·트리아지 통계 append·daily log OK는 그대로.
- 제거: `policy` import, `.graph-applied` 안전망, "guids file still present" 경고 문구(정규 경로가 됐으므로 info로 바꾸지 말고 그냥 삭제).
- `CYCLE_MAX_ARTICLES` 주석의 "~10x normal per-slot volume"을 120 기준으로 갱신하고, 상한을 올린 이유(헤드리스 → 단일 API 호출)를 한 줄.

- [ ] **Step 1: 테스트 수정·추가** — 모든 `patch("newsparser.scripts.run_cycle.run_claude", ...)`를 `patch("newsparser.scripts.run_cycle.ask_claude", ...)`로 바꾸고, 가짜는 파일을 쓰는 대신 `SAMPLE_REPORT`를 **반환**한다. 가짜 시그니처는 `(prompt, system, **kw)`이고 카테고리는 프롬프트의 `카테고리 {category}`에서 읽는다. `apply_graph.apply`는 기본적으로 `patch("newsparser.scripts.run_cycle.apply_graph.apply", return_value=(1, 0))`. 새 테스트:

```python
def test_run_cycle_writes_returned_report_and_marks_processed(tmp_path): ...
    # report 파일 내용 == SAMPLE_REPORT(+ 트리아지 통계 줄), get_unprocessed("tech") == [], guids 파일 없음

def test_run_cycle_prompt_contains_input_previous_report_interests_ignore(tmp_path): ...
    # 직전 슬롯 리포트 "PREV-REPORT-MARKER", interests "INTEREST-MARKER", ignore "IGNORE-MARKER"를 깔고
    # build_input_file은 실제로 돌려 입력 기사 제목이 prompt에 들어가는지, system 인자에 "리포트 파일 형식"이 있는지 확인

def test_run_cycle_retries_on_truncated_output(tmp_path):
    # 1회차 ClaudeError("claude stopped with stop_reason=max_tokens"), 2회차 SAMPLE_REPORT → 리포트 저장, 호출 2회

def test_run_cycle_treats_blank_output_as_failed_attempt(tmp_path):
    # 세 번 모두 "  \n" → ClaudeError로 FAIL 로그, 기사 pending, 리포트 파일 없음

def test_run_cycle_graph_failure_marks_processed_and_warns(tmp_path):
    # apply가 RuntimeError("neo4j down") → 기사 processed, 전송 메시지 중 "그래프 반영 실패" 포함,
    # daily log에 "GRAPH-FAIL", 사이클 렌더 메시지도 여전히 전송

def test_cycle_max_articles_is_120():
    assert script.CYCLE_MAX_ARTICLES == 120
```
  기존 `test_run_cycle_writes_guids_file_before_calling_run_claude`는 이름을 `..._before_calling_claude`로, `test_run_cycle_renders_from_file_ignoring_stdout`은 의미가 없어졌으므로 삭제, `test_run_cycle_retries_until_report_appears`·`..._keeps_articles_pending_when_no_report_after_all_attempts`는 위 새 테스트로 대체.

- [ ] **Step 2: 실패 확인** — `.venv/bin/pytest tests/test_run_cycle_script.py -v` → `ask_claude` 속성 없음으로 FAIL.
- [ ] **Step 3: 구현** — 위 흐름대로.
- [ ] **Step 4: 통과 확인** — `.venv/bin/pytest tests/test_run_cycle_script.py tests/test_input_builder.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: 사이클을 API 직접 호출로 전환하고 입력 상한을 120건으로 상향"`

---

### Task 6: weekly 전환 + demand 파일 쓰기 제거

**Files:**
- Modify: `newsparser/scripts/run_weekly.py`
- Modify: `newsparser/scheduler/demand.py:1-10, 66-71` (`write_demand_digest`·`DIGEST_FILE` 제거, docstring 갱신)
- Test: `tests/test_run_weekly.py` (신규), `tests/test_demand.py:62-70` (파일 쓰기 테스트 삭제)

**Interfaces:**
- Consumes: `api.ask_claude`, `prompts.system_prompt("weekly")`, `reports.reports_between`, `demand.build_demand_digest(date, days)`.
- Produces: `run_weekly.WEEKLY_SCHEMA` —
  `{"type":"object","properties":{"briefing":{"type":"string"},"headlines":{"type":"array","items":{"type":"string"}}},"required":["briefing","headlines"],"additionalProperties":False}`

흐름: 기간 = `date - 6일 ~ date`(7일), 카테고리 `("tech","markets")`. 사용자 메시지에 `기준일 {date}`, `<reports>`(각 항목 `<report name="tech/2026-05-08-12">…</report>`), `<demand>`. `ask_claude(..., timeout=1500, output_schema=WEEKLY_SCHEMA)` → `json.loads` 실패 시 `ClaudeError("weekly: invalid JSON …")`. `workspace/cycles/weekly/{date}.md`에 briefing, 헤드라인이 있으면 `send_long_message("[WEEKLY] " + "\n".join("• " + h.strip().lstrip("•").strip() for h in headlines))`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_run_weekly_writes_briefing_and_sends_headlines(tmp_path): ...
    # ask_claude → '{"briefing":"주간 브리핑 본문","headlines":["• 첫째","둘째"]}'
    # cycles/weekly/2026-05-08.md == "주간 브리핑 본문", 전송 == "[WEEKLY] • 첫째\n• 둘째"

def test_run_weekly_prompt_has_last_7_days_without_graph_block(tmp_path): ...
    # 2026-05-01(제외)·2026-05-02·2026-05-08 리포트 → prompt에 05-02/05-08만, "## Graph updates" 없음, demand 다이제스트 문구 포함
    # 그리고 timeout=1500, output_schema=WEEKLY_SCHEMA로 호출됐는지

def test_run_weekly_invalid_json_raises_and_writes_nothing(tmp_path):
    # ask_claude → "not json" → ClaudeError, weekly 파일 없음, 전송 없음
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/pytest tests/test_run_weekly.py -v` → FAIL.
- [ ] **Step 3: 구현**.
- [ ] **Step 4: 통과 확인** — `.venv/bin/pytest tests/test_run_weekly.py tests/test_demand.py -v` → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: weekly를 API 구조화 출력 호출로 전환"`

---

### Task 7: reflect 전환 + 웨이트 검증

**Files:**
- Modify: `newsparser/triage.py:258-278` (`write_axis_snapshot` 제거, `axis_dict`에서 `weights_files` 제거·docstring 갱신, `sanitize_weights` 추가)
- Modify: `newsparser/scripts/run_reflect.py`
- Test: `tests/test_triage.py:254-` (`test_write_axis_snapshot` 삭제, 새 테스트), `tests/test_run_reflect.py` (신규)

**Interfaces:**
- Produces:
  - `triage.WEIGHT_FLOORS = {"기타경제": 0.4, "기타기술": 0.4}`, `triage.WEIGHT_CEILINGS = {NOISE_BUCKET: 0.1}`.
  - `triage.bucket_names(category: str) -> list[str]` — 해당 카테고리 버킷 + `NOISE_BUCKET`.
  - `triage.sanitize_weights(category: str, raw: dict) -> dict[str, float]` — `bucket_names(category)`의 키만 남기고, 빠진 키는 `ValueError`, 값은 float로 [0,1] 클램프 후 floor/ceiling 적용.
  - `run_reflect.reflect_schema() -> dict` — `interests_tech`·`interests_markets`(string), `triage_weights_tech`·`triage_weights_markets`(각 `bucket_names(cat)`를 property로, 모두 `{"type":"number"}`, 전부 required, `additionalProperties: False`), `changes`(string 배열). 최상위도 전부 required·`additionalProperties: False`.

흐름: 기간 = `date - 13일 ~ date`(14일). 사용자 메시지에 `기준일 {date}`, `<interests_tech>`, `<interests_markets>`, `<reports>`, `<demand>`(14일), `<bucket_axis>`(`json.dumps(triage.axis_dict(), ensure_ascii=False, indent=2)`). `ask_claude(..., timeout=1500, output_schema=reflect_schema())`, JSON 실패 시 `ClaudeError`, 아무것도 쓰지 않음.
기록 — 카테고리별 독립:
- 프로필: 공백만이거나 `^\|.*\|\s*$` 줄이 없으면 쓰지 않고 경고 로그, 아니면 `workspace/me/interests_{cat}.md`에 쓰기.
- 웨이트: `sanitize_weights` 성공 시 `triage.weights_path(cat)`에 `json.dumps(..., ensure_ascii=False, indent=2)`, `ValueError`면 경고 로그·기존 파일 유지.
- 전송: `changes`가 비면 `"[REFLECT] 변경 없음"`, 아니면 `"[REFLECT] " + "\n".join(changes)`.

- [ ] **Step 1: 실패하는 테스트 작성**

```python
def test_sanitize_weights_clamps_floors_and_ceiling():
    names = triage.bucket_names("markets")
    raw = {n: 0.0 for n in names} | {"노이즈": 0.9}
    raw[names[0]] = 1.7
    out = triage.sanitize_weights("markets", raw)
    assert out[names[0]] == 1.0 and out["기타경제"] == 0.4 and out["노이즈"] == 0.1
    assert set(out) == set(names)

def test_sanitize_weights_rejects_missing_bucket():
    with pytest.raises(ValueError):
        triage.sanitize_weights("tech", {"기타기술": 0.5})

def test_reflect_schema_lists_every_bucket_plus_noise():
    s = run_reflect.reflect_schema()["properties"]["triage_weights_tech"]
    assert set(s["required"]) == set(triage.bucket_names("tech")) and "노이즈" in s["required"]

def test_run_reflect_writes_profiles_weights_and_sends_changes(tmp_path): ...
def test_run_reflect_keeps_profile_when_empty(tmp_path):
    # interests_tech == "" → 기존 interests_tech.md 그대로, markets는 갱신
def test_run_reflect_prompt_keeps_triage_stats_line(tmp_path):
    # 리포트 끝 "트리아지: 후보 9건" 줄이 prompt에 있고 "## Graph updates"는 없음
def test_run_reflect_invalid_json_raises_and_writes_nothing(tmp_path): ...
```

- [ ] **Step 2: 실패 확인** — `.venv/bin/pytest tests/test_triage.py tests/test_run_reflect.py -v` → FAIL.
- [ ] **Step 3: 구현**.
- [ ] **Step 4: 통과 확인** — 같은 명령 → PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: reflect를 API 구조화 출력 호출로 전환, 트리아지 웨이트 검증 추가"`

---

### Task 8: 잔재 삭제와 문서 갱신

**Files:**
- Delete: `newsparser/claude/policy.py`, `.claude/commands/cycle.md`, `.claude/commands/weekly.md`, `.claude/commands/reflect.md`
- Modify: `newsparser/mcp_server.py:534-537` (start_job docstring), `tests/test_tracker.py:75-93`
- Modify: `CLAUDE_DEV.md`, `CLAUDE.md:20`, `README.md:173, 243-244`, `.env.example`

- [ ] **Step 1: 테스트 수정** — `test_start_job_docstring_routes_run_orders_away_from_slash_commands`: "슬래시 커맨드를 직접 실행하는 것도 금지" 단언을 빼고, 남은 두 단언(`slot·category를 사용자에게 되묻지 마라`, `실행 지시에는 언제나 start_job만 쓴다`)은 유지. docstring 설명 문단도 슬래시 커맨드가 없어졌다는 사실에 맞게.
- [ ] **Step 2: 코드 정리** — `mcp_server.start_job` docstring에서 `/cycle`·`/weekly`·`/reflect` 슬래시 커맨드 문장 두 줄 제거. 파일 삭제. `grep -rn "policy\|run_claude" newsparser/scripts` 결과가 비어야 함.
- [ ] **Step 3: 문서**
  - `CLAUDE_DEV.md`: 10-13행 → "스케줄 작업(cycle/weekly/reflect)과 Haiku 분류기는 `newsparser/claude/api.py`로 API 직접 호출, tracker만 `claude -p`(구독 OAuth)". 인증은 `NEWSBOT_API_KEY`, `ANTHROPIC_API_KEY` 금지 이유 유지. 33행 Haiku 재시도 절의 OAuth 언급, 35-36행(.claude/commands), 87-108행(tool policy: tracker만 남음, 허용목록 실측 메모는 tracker에 관한 부분만), 116행(Auth: OAuth는 tracker 전용), 194-198행(Slash Command Behavior → 프롬프트는 `newsparser/prompts/`, 스타일은 `CLAUDE.md` Style 절에서 읽음 — Style 절 제목·구조를 바꾸면 `prompts.style_rules()`가 깨진다는 경고 한 줄).
  - `CLAUDE.md:20`: "Scheduled parsing jobs (cycle/weekly/reflect) call the API directly via `newsparser/claude/api.py` with no tools; market_daily makes no Claude call." 로 교체. Style 절 위에 "이 절은 `newsparser/prompts`가 스케줄 작업 시스템 프롬프트로도 읽는다" 한 줄.
  - `README.md`: 173행 참조를 `newsparser/prompts/cycle.md`로, 243-244행 표에서 `NEWSBOT_HAIKU_API_KEY` 행을 `NEWSBOT_API_KEY`(스케줄 작업+Haiku, API 과금, `ANTHROPIC_API_KEY` 금지) 행으로, `CLAUDE_CODE_OAUTH_TOKEN` 행은 "tracker의 `claude -p` 전용"으로.
  - `.env.example`: `NEWSBOT_HAIKU_API_KEY` 제거, `NEWSBOT_API_KEY=` 추가(주석: ANTHROPIC_API_KEY 이름 금지). (이 파일은 Read 권한이 막혀 있으면 사용자에게 수정 내용을 알려 직접 반영 요청.)
- [ ] **Step 4: 전체 테스트** — `.venv/bin/pytest tests/ -q` → 전부 PASS. `grep -rn "NEWSBOT_HAIKU_API_KEY\|_OAUTH_BETA\|write_axis_snapshot\|TAINTED_FILE_TOOLS\|CYCLE_TOOLS" newsparser tests README.md CLAUDE_DEV.md CLAUDE.md` → 결과 없음.
- [ ] **Step 5: Commit** — `git commit -m "chore: 헤드리스 스케줄 작업 잔재 제거와 문서 갱신"`

---

## 배포 메모 (사람이 할 일)

1. 서버 `.env`에 `NEWSBOT_API_KEY` 추가(기존 `NEWSBOT_HAIKU_API_KEY` 값 재사용 가능), `NEWSBOT_HAIKU_API_KEY` 줄 삭제.
2. 코드 배포 → `uv sync` → `newsbot-dispatcher`, `newsbot-poller` 재시작.
3. 첫 사이클 뒤 daily log·텔레그램 확인, 콘솔에서 사용량 확인.
