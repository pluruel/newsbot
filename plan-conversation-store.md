# 대화 저장 JSONL → SQLite 전환 + Neo4j 투영

브랜치: `feat/conversation-store-sqlite`

## 배경 / 문제

현재 대화는 `workspace/sessions/{chat_id}.jsonl`에 저장된다 (`newsparser/bot/tracker.py`).
- 한 줄 = 한 턴 `{role, content, ts}`, `chat_id`가 유일한 스레드 식별자.
- `save_history`가 **전체 파일 재작성**(append 아님) → 동시 메시지 시 read-modify-write 경쟁으로 턴 유실.
- `_extract_pairs`가 user→assistant **엄격 교대**를 가정 → 연속 메시지·백그라운드 리포트 끼어들기 시 페어링이 깨짐 (= "Q&A가 순차적이지 않다"는 문제의 핵심).

## 결정 (확정)

- **SQLite = source of truth.** 기사 DB와 **별개 파일** `workspace/conversations.db` (env `CONV_DB_PATH`,
  기본값은 `${WORKSPACE_DIR}/conversations.db`). 기존 `newsparser.db`·`market.db` 도메인별 분리 관례 그대로.
  `backup.sh`가 `workspace/*.db`를 glob하므로 백업 스크립트 변경 불필요.
- 각 메시지에 고유 `id` + `reply_to_id`(부모 포인터) → **DAG(인접 리스트) 모델**. 스레딩이 줄 순서가 아닌
  엣지로 결정되어 순차성 가정이 사라진다.
- **Neo4j = 투영(projection).** SQLite 쓰기 후 best-effort로 Message 서브그래프에 투영.
  Neo4j 실패가 Telegram 응답을 막지 않는다. `reproject_all()`로 SQLite→Neo4j 재구축 가능.
- 기존 jsonl 대화는 폐기 (마이그레이션 없음, 클린 컷오버).

## SQLite 스키마 — `newsparser/store/conversations.py` (신규)

```sql
CREATE TABLE messages (
    id          TEXT PRIMARY KEY,               -- uuid4 hex
    chat_id     TEXT NOT NULL,
    role        TEXT NOT NULL,                  -- user | assistant | system
    content     TEXT NOT NULL,
    ts          TEXT NOT NULL,                  -- UTC ISO-8601
    reply_to_id TEXT REFERENCES messages(id),   -- 이 메시지가 답한 부모; NULL=스레드 루트
    kind        TEXT NOT NULL DEFAULT 'chat',   -- chat | admin | report
    meta        TEXT                            -- optional JSON
);
CREATE INDEX idx_messages_chat_ts ON messages(chat_id, ts);
CREATE INDEX idx_messages_reply   ON messages(reply_to_id);

-- 색인: trigram FTS5 (혼합 KR/EN 부분문자열 검색; 3자 이상)
CREATE VIRTUAL TABLE messages_fts USING fts5(
    content, content='messages', content_rowid='rowid', tokenize='trigram'
);
-- INSERT/DELETE/UPDATE 동기화 트리거 3종
```

API:
- `init_conv_db()` — 멱등 생성 (기존 store 패턴).
- `add_message(chat_id, role, content, *, reply_to_id=None, kind='chat', meta=None) -> id` — 단일 INSERT(원자적, 경쟁 없음).
- `get_recent_messages(chat_id, n, kinds=('chat',)) -> list[dict]` — 최근 n턴.
- `get_thread(message_id) -> list[dict]` — reply_to_id 체인 walk (recursive CTE).
- `search_messages(keyword, chat_id=None, since=None, limit=10) -> list[dict]` — FTS MATCH.
- `clear_chat(chat_id=None)` — 삭제(전체 또는 한 채팅).
- `iter_all_messages()` — Neo4j 재투영용.

## tracker.py 리팩터

- `load_history` → `get_recent_messages` 위임 (dict shape 유지: `_extract_pairs`/`_needed_history_depth` 호환).
- `save_history` 전체 재작성 제거 → run_tracker 말미에서 user 메시지 `add_message` 후,
  assistant 메시지를 `reply_to_id=<user id>`로 `add_message`. **Q&A 엣지를 명시적으로 기록.**
- admin-marker 답변: `kind='admin'`으로 저장하되 `get_recent_messages` 기본 `kinds=('chat',)`에서 제외
  (감사 로그 유지 + 기존 "히스토리에서 제외" 동작 보존).
- `_extract_pairs`를 reply_to_id 기반으로 재작성 (위치 인접 → 엣지). 순차성 페이오프.

## Neo4j 투영 — `newsparser/graph/conversation_projector.py` (신규)

- `project_message(msg)` — best-effort try/except:
  - `MERGE (m:Message {id})` 속성 세팅, `MERGE (c:Chat {chat_id})`, `(m)-[:IN_CHAT]->(c)`
  - `reply_to_id` 있으면 `MATCH` 부모 후 `(m)-[:REPLIES_TO]->(parent)`
  - MENTIONS: 엔티티 레지스트리(`resolver.fetch_registry`) 부분문자열 매칭으로 `(m)-[:MENTIONS]->(:Entity)` (경량, 옵션)
- `reproject_all()` — SQLite 전량 읽어 Message 서브그래프 재구축 (파생 데이터 복구).
- run_tracker에서 add_message 직후 호출 (best-effort, 실패는 로깅만).

## MCP 도구 (색인 MCP) — `newsparser/mcp_server.py`

- `read_conversation_history` → `get_recent_messages`로 갱신.
- `clear_conversation_history` → `conversations.clear_chat()` (jsonl glob 제거).
- **신규 `search_conversations(keyword, chat_id=None, since=None, n=10)`** — FTS 검색 (핵심 색인 도구).
- **신규 `get_conversation_thread(message_id)`** — DAG reply 체인.
- **신규 `conversations_about_entity(entity, n=10)`** — Neo4j `(:Message)-[:MENTIONS]->(:Entity)` 질의
  (대화↔뉴스그래프 교차 — 투영의 페이오프).

## 설정 / 배포

- `.gitignore`에 `workspace/conversations.db` 추가.
- dispatcher/poller 스타트업에 `init_conv_db()` 호출 (init_db 옆).
- CLAUDE.md에 대화 저장 = conversations.db + Neo4j Message 투영 짧게 명시.
- backup.sh/restore.sh 변경 불필요(신규 볼륨/외부 상태 없음) — snapshot→restore→row diff로 재검증만.

## 테스트

- 신규 `tests/test_conversations.py` — add/recent/thread/FTS/clear (CONV_DB_PATH tmp).
- `tests/test_tracker.py`·`tests/test_mcp_server.py` — 신규 store API로 갱신.
- 투영기: `NEWSPARSER_TEST_NEO4J=1` gate (기존 graph 테스트 관례), best-effort라 Neo4j 없이 유닛 통과.

## 작업 순서

1. `store/conversations.py` + 테스트
2. tracker.py 리팩터 + 테스트 갱신
3. `graph/conversation_projector.py` + 투영 배선
4. mcp_server.py 도구 갱신/추가 + 테스트 갱신
5. 스타트업 init 배선, .gitignore, CLAUDE.md
6. 전체 pytest + 백업 재검증

---

## 2단계 — 대화 신호를 소비하는 기능 마이그레이션 (커밋 2)

"대화로 하던 기능"을 추적한 결과: 유일한 대화 소비자는 tracker(완료)와, tracker가 남기는
`interest-events.jsonl`(대화-파생 관심 신호)뿐. reflect/weekly는 지금껏 **뉴스 공급(cycle)만** 보고
사용자 수요(대화)는 안 봤다. 이를 마이그레이션:

- **`interest-events.jsonl` → SQLite.** 마지막 남은 대화 JSONL을 `conversations.db`의
  `interest_events` 테이블로 이관. `store/conversations.py`에 `log_interest_event` /
  `interest_theme_counts` / `clear_interest_events` / `recent_user_queries` 추가.
  `mcp_server.py`의 `_log_interest_event` / `_interest_weights_one` / `clear_interest_events`가
  SQLite를 쓰도록 갱신. (tracker의 stale admin 마커 `interest-events.jsonl` → `interest events cleared`.)
- **reflect/weekly에 수요 신호 주입.** 두 잡은 `TAINTED_FILE_TOOLS`(MCP·bypass 없음)라 DB 직접
  조회 불가 → `scheduler/demand.py`가 Python으로 스토어를 읽어 `workspace/me/interest-demand.md`
  다이제스트(질의 빈도 상위 테마 + 최근 사용자 질문)를 쓰고, `run_reflect.py`(14일)/`run_weekly.py`(7일)가
  claude 실행 전에 생성. `reflect.md`/`weekly.md` 스펙이 그 파일을 Read(허용 도구)해서 반영.
  → 뉴스 taint를 늘리지 않고(사용자 입력은 신뢰됨) 기존 tool 정책 유지.
