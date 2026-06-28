# 사이클 텔레그램 메시지 축약 + 엔티티/서사 무시 목록

- 날짜: 2026-06-28
- 상태: 설계 승인됨, 구현 계획 대기
- 관련 파일: `.claude/commands/cycle.md`, `newsparser/scripts/run_cycle.py`, `newsparser/scripts/apply_graph.py`, `newsparser/bot/tracker.py`, `newsparser/scheduler/workspace.py`, 신규 `newsparser/ignore.py`, 신규 `workspace/me/ignore.md`

---

## 배경 / 문제

두 가지 운영상 불만에서 출발한다.

1. **텔레그램 메시지가 여전히 길다.** 직전 패치(#7 "fix: simplify msg")가 항목당 한 줄(`• (0.NN) 헤드라인`)로 줄였으나, `YYYY-MM-DD HH:00 KST` 타임스탬프 헤더 + 4개 섹션 헤더(새 소식 / 이어지는 흐름 / 조용한 영역 / 오픈 스레드)가 그대로 남아 매 사이클 구조적 군더더기가 누적된다. 사용자는 **중요도 + 기사 제목만** 보길 원한다. 단, 크론 사이클이 디스크에 저장하는 파일들(리포트·input·guids·logs·Neo4j)은 **형식·동작 모두 그대로** 유지해야 한다.

2. **잘못된 컨텍스트가 사이클마다 재등장한다.** 예: "Opus 4.8 API 미등장/미공개"라는 사실상 잘못된 서사가 한 번 사이클 기록에 들어간 뒤, 사이클이 직전 리포트를 다시 읽어 계속 재서술하며 길게 삽질했다. 사용자는 **외부에서 특정 엔티티/서사를 의도적으로 무시**시키고 싶다.

### 왜 재등장하는가 (핵심 진단)

`.claude/commands/cycle.md` step 2(line 20): "Read the most recent file in `workspace/cycles/{category}/` for prior context." 크론 사이클은 **그래프를 되읽지 않고**(그래프는 `/tracker`만 `graph_query`로 조회), **직전 사이클 리포트 .md**를 읽는다. 따라서 잘못된 서사는 직전 리포트의 `이어지는 흐름`/`오픈 스레드`에 남아 다음 리포트로 재서술되고, 그 리포트가 또 다음 사이클의 step 2 입력이 되어 자기영속한다. → **재등장을 끊는 진짜 지점은 "직전 리포트 재독 시 무시 목록 대상을 이어받지 않게 하는 것"이다.**

---

## 목표 / 비목표

**목표**
- G1. 크론 사이클 텔레그램 메시지를 `[CAT]` + 중요도 내림차순 `• 0.NN 헤드라인` 평평한 목록으로 축약. 타임스탬프·섹션 헤더 제거.
- G2. 외부에서 편집 가능한 무시 목록으로 특정 엔티티명/서사를 (a) 사이클 분석·리포트, (b) 그래프, (c) 텔레그램에서 제외.
- G3. 텔레그램 명령으로 무시 목록 추가/해제/조회. 조회 시 각 항목의 **등록 후 경과일** 표시.

**비목표**
- 저장 파일 형식/동작 변경 (리포트 .md, input, guids, logs는 불변).
- 기존 Neo4j 데이터 소급 정리 (이미 쌓인 노드는 보존; 무시는 이후 사이클부터 적용).
- 과거 리포트 .md 텍스트 소급 수정 (감사용 보존).
- 무시 항목 자동 만료 (영구; "무시 해제" 전까지 유지).
- **DB 스키마/마이그레이션 변경.** SQLite 테이블·Neo4j 라벨/속성/제약/인덱스 모두 불변. 무시 목록은 마크다운 파일(`ignore.md`)에 저장하며, `apply_graph`는 기존 그래프 스키마에 **필터링만** 추가한다(새 영속 데이터는 `ignore.md` 하나뿐).

---

## Goal 1 — 텔레그램 메시지 결정적 축약

### 설계 원칙: LLM stdout이 아니라 Python이 리포트 파일에서 결정적으로 렌더

현재 텔레그램 메시지는 `cycle.md` step 7이 stdout으로 출력하는 LLM 생성 텍스트다(`run_cycle.py:66` → `:89` 전송). 사용자의 불만 중 하나가 LLM의 형식 일관성이므로, 메시지를 **저장된 리포트 파일의 안정적 형식에서 Python이 직접 추출**해 매번 동일한 결과를 보장한다.

리포트 형식(`cycle.md:38-65`)은 각 항목을 다음으로 쓴다:

```
• (중요도 0.NN) 한 줄 헤드라인. 본문 2–4문장.
  엔티티: ... / 출처: ...
```

`조용한 영역`/`오픈 스레드` 항목은 **중요도 점수가 없다**(`• YYYY-MM-DD-HH 사이클에...`, `• ...`). 따라서 `• (중요도 0.NN)` 패턴만 파싱하면 점수 있는 항목(새 소식 + 이어지는 흐름)만 자연히 추출되고, 점수 없는 군더더기 섹션은 텔레그램에서 빠진다.

### 렌더링 로직 (`run_cycle.py`)

`_run_for_category`에서 `run_claude` 실행(리포트 작성·apply_graph·mark_processed는 그대로) 후:

1. `report_path = workspace/cycles/{category}/{slot}.md` 읽기 (없으면 전송 스킵 + 경고 로그 — 기존 동작 유지).
2. `## Graph updates` 앞부분(다이제스트)만 대상으로 정규식 추출:
   - 패턴: `^\s*[•\-\*]\s*\(중요도\s*([0-9]*\.?[0-9]+)\)\s*(.+)$`
   - `score = float(group1)`, `headline = group2`에서 **첫 `. `(마침표+공백) 앞부분**만 (본문 제거). 마침표가 없으면 전체.
3. 무시 필터 적용 (Goal 2의 `IgnoreList.matches(headline)` True면 드롭).
4. `(score, headline)` **중요도 내림차순** 정렬, 헤드라인 문자열 기준 중복 제거.
5. 렌더: `[{CAT}]\n` + `\n`.join(`• {score:.2f} {headline}`). 항목이 0건이면 `[{CAT}] 새 소식 없음`.
6. `send_long_message(msg)` (4000자 청크 전송은 기존 그대로).

기존 `summary = run_claude(...)`의 stdout 사용과 empty-stdout 폴백(`run_cycle.py:81-86`)은 **제거**한다(리포트 파일 렌더가 단일 경로). `run_claude` 호출 자체는 유지(리포트·그래프·mark_processed를 수행하는 본체).

### `cycle.md` 변경

- "텔레그램 전송용 요약 (stdout)" 섹션(72-91) 제거.
- step 7(line 30)을 "텔레그램 메시지는 이제 Python이 리포트 파일에서 렌더하므로 별도 stdout 요약을 출력할 필요 없음. 리포트 .md만 정확히 작성하면 된다."로 단순화.
- 리포트 형식(38-65), step 1–6, 문체 규칙은 **불변**.

### 예시 결과

```
[TECH]
• 0.85 엔비디아 신규 GPU 공개
• 0.72 오픈AI 기업가치 재산정
• 0.61 EU AI법 시행령 초안
• 0.55 TSMC 3nm 수율 개선
```

### 엣지 케이스

- 리포트에 점수 항목 0건 → `[CAT] 새 소식 없음`.
- 헤드라인에 마침표 없음 → 전체를 헤드라인으로.
- 점수 형식 변형(`0.NN`, `.7`, `1.0`) → 정규식이 흡수.
- 리포트 파일 부재(사이클 실패) → 전송 스킵 + 경고 로그.

---

## Goal 2 — 엔티티/서사 무시 목록

### 저장: `workspace/me/ignore.md`

`workspace.py`의 `ensure_workspace()`에서 `manifesto.md`처럼 "없으면 생성"(시드 템플릿). 관용 마크다운 표(파서는 `collector/sources.py` 스타일):

```markdown
# 무시 목록 (ignore list)

봇이 이 목록의 대상을 사이클 분석·다이제스트·그래프·텔레그램에서 제외한다.
"무시: <대상>" 추가 · "무시 해제: <대상>" 삭제 · "차단 리스트" 조회.

| 종류 | 대상 | 추가일 | 메모 |
|------|------|--------|------|
| storyline | Opus 4.8 API 미등장/미공개 추측 | 2026-06-28 | 잘못된 사이클 기록 |
| entity | Claude Opus 4.8 | 2026-06-28 |  |
```

- `종류`: `storyline`(자유문구 서사) 또는 `entity`(canonical_name/별칭).
- `대상`: 무시할 서사 문구 또는 엔티티명.
- `추가일`: `YYYY-MM-DD` (추가 시점). **만료일 컬럼 없음 — 영구.**
- `메모`: 선택.

### 로더: `newsparser/ignore.py` (신규)

`collector/sources.py`의 관용 표 파서(`_split_row`, `_is_separator`, 헤더 매핑)를 모델로:

```python
@dataclass
class IgnoreEntry:
    kind: str        # "storyline" | "entity"
    target: str
    added: date | None
    note: str = ""

class IgnoreList:
    entries: list[IgnoreEntry]
    def entity_names(self) -> set[str]      # kind=="entity" 대상, casefold
    def storylines(self) -> list[str]       # kind=="storyline" 대상
    def matches(self, text: str) -> bool     # 엔티티명/서사 문구가 text에 부분일치(casefold)
    def matches_entity(self, name, aliases) -> bool  # 그래프 필터용
```

- `load_ignore(workspace) -> IgnoreList`: 파일 없으면 빈 목록 반환(조용히).
- 알 수 없는 `종류`/빈 `대상` 행은 경고 후 스킵(관용).
- **`python -m newsparser.ignore` 실행 시** 오늘 기준 목록을 결정적으로 출력 (LLM 날짜 계산 배제):

```
무시 목록 (3건)
• [storyline] Opus 4.8 API 미등장/미공개 추측 — 30일 경과
• [entity]    Claude Opus 4.8 — 30일 경과
• [storyline] XX 인수설 — 5일 경과
```

경과일 = `today - 추가일`(일). 추가일이 비거나 파싱 불가면 `— 추가일 미상`.

### 적용 3지점

세 경로가 각기 다른 누수를 막는다. `종류`별 적용 범위:

| 지점 | 무엇을 막나 | storyline | entity |
|------|-------------|:---:|:---:|
| **1. SOFT — `cycle.md`** | 새 리포트에 재서술되는 것 (재등장 근본 차단) | ✓ | ✓ |
| **2. HARD — `apply_graph.py`** | 그래프에 신규로 들어가는 것 | — | ✓ |
| **3. HARD — `run_cycle.py` 렌더** | 텔레그램에 보이는 것 | ✓ | ✓ |

**1. SOFT (`cycle.md`)** — step 2(직전 리포트 재독) 근처에 지시 추가: "먼저 `workspace/me/ignore.md`를 읽는다. 목록의 대상은 (a) 직전 리포트에서 이어받지 말고, (b) 중요도 점수·다이제스트·그래프 블록에 포함하지 말 것." → 새 리포트 .md에 더는 안 실려 자기영속이 끊긴다. (LLM 힌트 — 결정적 보장 아님, 그래서 2·3과 병행.)

**2. HARD 그래프 (`apply_graph.py:56–58`)** — `parse_graph_updates`(50)와 `apply_graph_updates`(59) 사이, `_resolve_source_indices`(56) 직후에:
```python
ignore = load_ignore(workspace)
entities = [e for e in entities if not ignore.matches_entity(e.name, e.aliases)]
relations = [r for r in relations if not (ignore.matches_entity(r.subject, []) or ignore.matches_entity(r.object, []))]
```
(엔티티/관계 식별자가 canonical_name이므로 `entity` 종류만 의미 있음.) 기존 그래프 노드는 건드리지 않음 → "이후 사이클만". 리포트 .md는 그대로 → 감사 보존.

**3. HARD 텔레그램 (`run_cycle.py` 렌더)** — Goal 1 렌더링 3단계에서 `ignore.matches(headline)` True인 헤드라인 드롭. LLM이 SOFT 지시를 어겨도 텔레그램엔 안 보임.

### 조작: 텔레그램 명령 (새 MCP 도구 불필요)

`tracker.py`는 이미 `allowed_tools=["Bash","Read","Edit","Write",...]` + `permission_mode="bypassPermissions"`로 `workspace/me/*.md`를 **직접 Edit/Write**한다(manifesto·interests와 동일 패턴; `_ADMIN_MARKERS` 142-148). 같은 패턴을 그대로 사용:

- `tracker.py` 프롬프트에 힌트 블록 추가:
  - "무시: \<대상\>" / "\<대상\> 무시해" → `workspace/me/ignore.md` 표에 행 추가. `종류`는 단일 엔티티명이면 `entity`, 서사/문구면 `storyline`로 판단. `추가일`은 오늘(YYYY-MM-DD).
  - "무시 해제: \<대상\>" → 해당 행 삭제.
  - "차단 리스트" / "무시 목록 보여줘" → `.venv/bin/python -m newsparser.ignore`를 Bash로 실행하고 그 출력을 그대로 전달(경과일 포함).
- `_ADMIN_MARKERS`에 `"ignore.md updated"` 추가 → 무시 편집은 대화 히스토리에 저장 안 함(기존 admin 동작과 일관).

### 수명 / 범위

- 등록 즉시 이후 사이클부터 적용. Neo4j·과거 리포트는 보존.
- "무시 해제" 전까지 영구. 자동 만료 없음.
- 나중에 서사가 사실이 되면 → "무시 해제: \<대상\>" 한 번으로 복귀. (목록의 "N일 경과" 표시가 재검토 시점 판단을 돕는다.)

---

## 데이터 흐름 요약

```
크론 → run_cycle._run_for_category
  ├─ build_input_file + market snapshot       (불변)
  ├─ run_claude("/cycle ...")
  │     └─ cycle.md: ignore.md 읽음 → 무시 대상 제외하고 리포트 작성 (SOFT)
  │        └─ apply_graph.py: ignore로 entity/relation 필터 (HARD 그래프)
  │        └─ mark_processed.py                (불변)
  └─ [신규] 리포트 .md 파싱 → 중요도순 평평한 목록 → ignore.matches로 필터 (HARD 텔레그램)
        └─ send_long_message("[CAT]\n• 0.NN ...")

텔레그램 자유문구 → tracker.run_tracker
  └─ "무시:/무시 해제:/차단 리스트" → ignore.md Edit/Write 또는 `python -m newsparser.ignore`
```

---

## 테스트 (`.venv/bin/pytest`)

- `tests/test_ignore.py`: 표 파싱(정상·관용·빈 행), `entity_names`/`storylines`/`matches`/`matches_entity`, 경과일 계산, 파일 부재 시 빈 목록, `python -m newsparser.ignore` 출력 포맷.
- `run_cycle` 렌더 단위 테스트: 샘플 리포트 텍스트 → 중요도순 평평 목록, 점수 없는 섹션 제외, 본문 절단, 무시 필터 적용, 0건 시 "새 소식 없음".
- `apply_graph` 필터 단위 테스트: 무시 엔티티의 entity/relation 드롭, 비무시 항목 보존.

---

## 변경 파일 목록

| 파일 | 변경 |
|------|------|
| `newsparser/ignore.py` | **신규** — `IgnoreEntry`/`IgnoreList`/`load_ignore`, `__main__` 목록 출력(경과일) |
| `workspace/me/ignore.md` | **신규(시드)** — 무시 목록 표 템플릿 |
| `newsparser/scheduler/workspace.py` | `ensure_workspace`에 `ignore.md` "없으면 생성" 추가 |
| `newsparser/scripts/run_cycle.py` | stdout/폴백 제거 → 리포트 파일에서 결정적 렌더 + 무시 필터 |
| `newsparser/scripts/apply_graph.py` | `apply_graph_updates` 직전 무시 엔티티/관계 필터 |
| `.claude/commands/cycle.md` | stdout 요약 섹션 제거, step 7 단순화, step 2에 ignore.md 읽기/제외 지시(SOFT) |
| `newsparser/bot/tracker.py` | 프롬프트에 무시 명령 힌트, `_ADMIN_MARKERS`에 `"ignore.md updated"` |
| `tests/test_ignore.py` (+ 렌더/필터 테스트) | **신규** |

---

## 미해결 가정 (해소됨)

- "기사 제목" = 사이클이 dedup·병합해 만든 **한 줄 헤드라인**(리포트의 `• (중요도 …) 헤드라인`)으로 해석. 원본 RSS 제목과 1:1 매핑이 없으므로 storyline 단위 헤드라인을 사용.
- 기간 표시는 **만료일이 아니라 등록 후 경과일**("N일 경과")로 확정.
- 무시 단위는 **자유문구 서사 + 엔티티명 둘 다**.
- 조작은 **텔레그램 명령**(기존 직접-편집 패턴 재사용, 새 MCP 도구 없음).
