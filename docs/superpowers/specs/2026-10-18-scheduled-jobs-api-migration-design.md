# 스케줄 작업(cycle·weekly·reflect) API 직접 호출 전환 — 설계

작성일 2026-10-18

## 의도와 배경

약관 변경으로 구독(OAuth) 기반 헤드리스 `claude -p`를 스케줄 작업에 계속 쓰기 어렵게 됐다. 구독에 딸린
월 200달러 API 크레딧이 있으므로, 사람 없이 도는 분석 작업(cycle·weekly·reflect)은 API 키로
`/v1/messages`를 직접 호출하도록 옮긴다.

부수 목표: 헤드리스 시절 실패 연쇄를 끊으려고 넣은 사이클 입력 상한(`CYCLE_MAX_ARTICLES = 60`)을
120으로 올린다. API 단일 호출은 Claude Code 스캐폴딩·도구 왕복이 없어 같은 입력에서 더 가볍고 빠르다.

## 목표

- `run_cycle.py`, `run_weekly.py`, `run_reflect.py`가 `run_claude`(CLI 서브프로세스)를 더는 부르지 않고
  Anthropic SDK로 API를 직접 호출한다.
- `CYCLE_MAX_ARTICLES = 120`.
- 직접 API 호출(신규 경로 + 기존 Haiku `ask_haiku`)의 인증을 API 키 하나로 통일한다.
- 텔레그램에 나가는 결과물(사이클 렌더, `[WEEKLY]`, `[REFLECT]`)과 워크스페이스 파일 포맷은 그대로 유지한다.

## 비목표

- **tracker(텔레그램 답변)는 옮기지 않는다.** 사용자가 tracker를 통해 Claude Code에 작업을 시키기도 하므로
  `claude -p` + Bash/Edit + MCP + 구독 OAuth(`CLAUDE_CODE_OAUTH_TOKEN`)를 그대로 쓴다. `runner.py`는
  tracker 전용으로 남는다.
- 토큰 사용량 기록 기능을 새로 만들지 않는다 — 비용은 콘솔에서 본다. (기존 `haiku_usage`는 그대로 둔다.)
- Batch API, 모델 업그레이드, 프롬프트 캐싱 최적화는 이번 범위가 아니다.
- 상한을 토큰 예산 방식으로 바꾸지 않는다 — 건수 상한 그대로, 숫자만 120.

## 안티 목표 (기술적으로 돌아가도 실패인 것)

- **API 키를 `ANTHROPIC_API_KEY`라는 이름으로 두는 것.** tracker의 `claude -p` 서브프로세스가 환경을
  상속하고 CLI는 이 변수를 OAuth 토큰보다 우선하므로, tracker까지 크레딧 과금으로 넘어간다. (현재
  `haiku.py`가 같은 이유로 `NEWSBOT_HAIKU_API_KEY`를 쓰는 것과 같은 규칙.)
- cycle/weekly/reflect 작업이 `job_status`에 안 보이거나 `kill_job`으로 멈출 수 없게 되는 것.
- 그래프 업데이트·`mark_processed`가 조용히 누락되는 것 (리포트는 있는데 그래프 반영이 안 된 상태).
- 잘린 출력(`max_tokens` 도달)을 정상 리포트로 저장하는 것 — 리포트 끝의 `## Graph updates` 블록이
  사라지거나 반쯤 잘린 채 반영된다.

## 제약

- 월 예산 200달러(API 크레딧). 추정치(실측 아님): 120건 사이클 월 ~180달러, weekly·reflect 월 10달러 이하,
  Haiku 1달러 미만. tracker는 구독이라 크레딧과 무관.
- 사이클은 하루 8슬롯 × 2카테고리, `timeout=3600`초, 시도 3회(`CYCLE_CLAUDE_ATTEMPTS`) 구조 유지.
- 기존 오류 계약 유지: API/전송 실패는 `ClaudeError`, 사용자 kill은 `ClaudeKilled`
  (`newsparser/claude/runner.py`). JobManager(`newsparser/bots/core/jobs.py`)와 `run_cycle.main`의
  예외 처리가 이 두 타입에 기대고 있다.
- 배포 호스트 `.env`에 새 API 키를 넣는 것은 사람이 한다.

## 정해진 방식

### 1. 호출 구조: 도구 없는 단일 호출, I/O는 Python

세 작업 모두 모델에게 도구를 주지 않는다. Python이 필요한 파일 내용을 모두 읽어 프롬프트에 넣고, 모델은
결과 텍스트(또는 JSON)만 돌려주며, 파일 쓰기·스크립트 실행은 Python이 한다.

- 도구가 없으므로 기사 원문 속 프롬프트 인젝션이 실행할 수 있는 것이 없다. `policy.py`의
  `TAINTED_FILE_TOOLS`/`CYCLE_TOOLS`와 "허용목록이 우회돼 그래프 반영이 누락되면 직접 반영" 안전망은
  존재 이유가 사라진다.

### 2. 작업별 입출력

**cycle** (`run_cycle._run_for_category`)
- 입력: 기존대로 만든 `{slot}-input.md`(시장 스냅샷 포함) 전문, 같은 카테고리 직전 리포트 1개,
  `interests_{category}.md`, `ignore.md`.
- 직전 리포트 = `workspace/cycles/{category}/*.md` 중 파일명이 현재 `{slot}`보다 **앞선** 것의 사전순 마지막.
  (현재 cycle.md의 "사전순 마지막"은 catch-up 실행 때 더 뒤 슬롯을, 같은 슬롯 재실행 때 자기 자신을 집는다.)
  `{slot}.md`가 이미 있으면 경고 로그 후 덮어쓴다.
- 출력: 리포트 전문(다이제스트 + `## Graph updates`) 평문. Python이 `{slot}.md`로 저장한 뒤 그래프 반영과
  `mark_processed`를 **프로세스 안에서 직접** 호출한다.
- 시도 성공 조건: `stop_reason == "end_turn"`이고 텍스트가 비어 있지 않을 것. 아니면 해당 시도 실패로 보고
  재시도(최대 3회), 3회 모두 실패면 `ClaudeError` — 기사는 pending으로 남는다(현재 동작과 동일).
- 그래프 반영 실패 시: 리포트는 이미 저장됐으므로 기사는 **processed로 처리**한다(현재 `run_cycle.py:383-399`와
  같은 결과 — pending으로 남기면 다음 슬롯이 같은 기사를 다시 분석해 중복된다). 대신 조용히 넘어가지 않도록
  error 로그 + daily log에 `GRAPH-FAIL` 줄 + 텔레그램 경고 한 줄. `apply_graph.main`은 `sys.exit`로 실패를
  알리므로(SystemExit), 예외를 던지는 함수로 분리해 in-process 호출에 쓰고 CLI `main`은 그 래퍼로 남긴다.
- 이후 텔레그램 렌더·트리아지 통계 append·daily log는 현재 코드 그대로.

**weekly** (`run_weekly.main`)
- 입력: 지난 7일 tech·markets 리포트(`## Graph updates` **블록만** 제거 — 블록 뒤에 붙는 `트리아지: …` 통계
  줄은 남긴다), `interest-demand.md` 내용.
- 출력: 구조화 출력(JSON 스키마) `{briefing: str, headlines: [str]}`. Python이
  `workspace/cycles/weekly/{date}.md`에 briefing을 쓰고 `[WEEKLY]` + headlines를 텔레그램으로 보낸다.

**reflect** (`run_reflect.main`)
- 입력: 두 관심사 프로필, 지난 14일 양쪽 리포트(weekly와 같은 방식으로 Graph 블록만 제거 — reflect는
  `트리아지:` 통계 줄로 가중치 게이팅을 판별하므로 이 줄이 반드시 남아야 한다), `interest-demand.md`,
  버킷 축(`triage.axis_dict()`).
- 출력: 구조화 출력 `{interests_tech: str, interests_markets: str, triage_weights_tech: {...},
  triage_weights_markets: {...}, changes: [str]}`. Python이 파일을 쓰고 `[REFLECT]` + changes를 보낸다.
- 웨이트 객체는 스키마에서 버킷별 고정 property로 정의한다(축의 해당 카테고리 버킷 + 공용 `노이즈` —
  `axis_dict()["buckets"][cat]`에는 `노이즈`가 없으니 따로 더한다). 키 불일치가 구조적으로 불가능해진다.
- Python 검증: 값은 [0,1]로 클램프, 프롬프트에 있던 하한·상한(`기타경제`/`기타기술` ≥ 0.4, `노이즈` ≤ 0.1)도
  클램프. tech·markets는 각각 따로 검증·기록한다. 관심사 프로필은 비어 있거나 마크다운 표가 없으면 그 파일만
  쓰지 않고(기존 유지) 경고 로그 — cycle이 이 파일을 읽으므로 빈 값으로 덮어쓰면 안 된다.

**weekly·reflect 실패 계약**: 시도 1회. `stop_reason != "end_turn"`(max_tokens, refusal 포함)이거나 JSON
파싱 실패면 아무 파일도 쓰지 않고 `ClaudeError` — JobManager가 ❌로 알린다(현재와 같다).

### 3. 프롬프트 위치

- `.claude/commands/{cycle,weekly,reflect}.md`는 API용 프롬프트로 다시 써서 `newsparser/prompts/`로 옮기고
  원본은 삭제한다. "파일을 Read/Glob 하라", "Bash로 실행하라", "stdout에 출력하라" 같은 도구 지시는 빼고,
  "입력으로 주어진 ○○를 보고"로 바꾼다. 분석 규칙·리포트 형식·문체 규칙은 그대로 옮긴다.
- `CLAUDE.md`는 API 호출에 자동으로 실리지 않는다. 그 "Style" 규칙(한국어, 평문, 숫자 정확 등)을 세 작업의
  시스템 프롬프트에 포함한다.
- `.claude/commands/cycle.md`가 사라지면 tracker가 `/cycle` 슬래시 커맨드를 잘못 집는 경로 자체가 없어진다.
  `tests/test_tracker.py`의 관련 테스트와 `mcp_server.py` `start_job` docstring의 금지 문구는 현실에 맞게
  정리한다. 수동 실행은 텔레그램 봇 트리거(`/cycle` 등 — `bots/*/bot.py`의 TelegramMatch)와 `start_job`이
  이미 담당하므로 슬래시 커맨드가 없어도 잃는 기능은 없다.
- 프롬프트가 Claude Code 스캐폴딩 없이 돌기 때문에, 출력 형식이 그대로 유지되는지는 모델 고정이 아니라
  재작성한 프롬프트에 달려 있다. 리포트 형식·`(중요도 0.NN)` 줄 형식은 문구 그대로 옮긴다.

### 3-1. 정리(삭제) 대상

- `newsparser/claude/policy.py` 및 이를 언급하는 주석/docstring(`demand.py` 등).
- `triage.write_axis_snapshot` / `workspace/me/triage-buckets.json` 생성 — 축은 프롬프트에 직접 넣는다.
- `run_cycle.py`의 `.graph-applied` 마커 확인 안전망. 마커 쓰기 자체는 `test_apply_graph.py`·
  `test_restore_graph_from_cycles.py` 등이 의존하면 남긴다.
- `runner.py`의 denials 수집은 tracker가 계속 쓰므로 유지.

### 4. 인증

- 새 환경변수 `NEWSBOT_API_KEY`(`ANTHROPIC_API_KEY`는 금지) 하나로 신규 경로와 `ask_haiku` 모두 인증한다.
  기존 `NEWSBOT_HAIKU_API_KEY`는 읽지 않는다 — 배선째 제거(코드·`.env.example`·문서). `haiku.py`의 OAuth
  토큰 대체 경로도 제거한다(직접 API 호출에 구독 토큰을 쓰지 않는다).
- 이 변경은 스케줄 작업 밖에도 닿는다: 키가 없으면 트리아지·분류기·`tracker_depth`·`apply_graph` 안의
  resolver 등 모든 Haiku 호출이 `ClaudeError`로 떨어진다(각 호출부는 fail-open). 그래서 dispatcher·poller
  시작 시 `NEWSBOT_API_KEY`가 없으면 error 로그를 남긴다. 배포 순서: `.env`에 키 추가 → 코드 배포 → 재시작.

### 5. 모델·호출 파라미터

- 모델은 현재와 같은 `claude-sonnet-5`. 이번 변경에서 모델 교체로 인한 출력 변화를 섞지 않기 위함.
- `thinking: {type: "adaptive"}`, `output_config.effort: "high"`.
- 긴 출력이므로 스트리밍(`messages.stream` + `get_final_message`), `max_tokens`는 넉넉히(예: 64000).
- 벽시계 타임아웃은 지금과 같은 값(cycle 3600초, weekly/reflect 1500초 — 후자는 `run_claude`의 기본값).
  **SDK `timeout`은 스트림에서 읽기 단위로 걸려 벽시계 상한이 되지 못한다.** `threading.Timer`가 스트림을
  닫는 방식으로 상한을 건다(아래 kill과 같은 경로). 이 타이머가 전체 상한을 보장하므로 SDK 재시도는
  기본값(2)을 둬도 된다. 스트림 도중 끊긴 오류는 SDK가 재시도하지 않지만 cycle의 3회 루프가 받는다.

### 6. job_status / kill_job 연동

- API 호출도 `runner.py`의 실행 레지스트리(`_ACTIVE`, `CURRENT_JOB`)에 등록해 `active_runs()`에 보이고,
  `kill_job(job_id)`이 진행 중인 스트림을 끊어 `ClaudeKilled`를 일으키게 한다. `on_activity` 하트비트도
  스트림 이벤트로 갱신한다.
- `_Run`은 서브프로세스 전제(`proc`, `pid`, `proc.kill()`)라, 취소 콜러블을 갖도록 일반화하고 API 실행은
  `pid=None`으로 둔다. `jobs.py`·`mcp_server.py`의 pid 표시는 None일 때 `api`로 보여준다.

### 7. 상한

- `CYCLE_MAX_ARTICLES = 120`. 주석의 "~10x normal per-slot volume" 근거는 갱신.

### 8. 문서

- `CLAUDE_DEV.md` Architecture의 "Do not switch to the Anthropic API directly" 규칙을 새 현실로 고친다:
  스케줄 작업은 API 직접 호출, tracker만 `claude -p`. tool policy 절도 tracker만 남은 상태로 정리.
  대상 줄: `CLAUDE_DEV.md` 10-13, 33(Haiku OAuth 언급), 35-36, 87-108(tool policy·허용목록 실측), 116(Auth),
  194-198(Slash Command Behavior).
- `CLAUDE.md:20`의 "Scheduled parsing jobs (cycle/weekly/reflect/market_daily) call `run_claude()`" 문장
  — market_daily는 원래 Claude를 부르지 않으므로 그 부분도 함께 바로잡는다.
- README의 `.claude/commands/cycle.md` 참조(173행)와 OAuth 대체 경로 설명(243-244행), `.env.example`(새 키).

## 빌더에게 맡기는 것

- 신규 모듈 이름·구조(`newsparser/claude/` 아래 API 호출 헬퍼 등), `runner.py` 레지스트리를 API 실행과
  공유하는 구체적 방식.
- 프롬프트 재작성 문구(규칙 내용은 보존), 구조화 출력 스키마의 세부 필드 설명.
- reflect/weekly 입력이 지나치게 커질 때의 처리: 컨텍스트(1M)에 근접하면 오래된 리포트부터 빼고 경고 로그.
- 테스트 구성. 기존 테스트 중 `run_claude`를 목킹하던 cycle/weekly/reflect 테스트는 새 호출 경로에 맞게 고친다.
