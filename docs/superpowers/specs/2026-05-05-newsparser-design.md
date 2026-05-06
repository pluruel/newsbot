# Newsparser System Design
Date: 2026-05-05

## Overview

개인 마켓 인텔리전스 시스템. 6시간 주기로 국내외 경제 기사를 수집·분석하고, 매일 오전 7시 텔레그램으로 브리핑을 발송한다. 유저의 팔로업 쿼리에는 Neo4j 지식 그래프를 타고 컨텍스트를 구성해서 답변한다.

**핵심 원칙:** LLM은 판단이 필요한 곳에만. 기계적인 작업(fetch, 파일 I/O, DB CRUD, 스케줄링)은 Python이 처리한다.

---

## 환경

- VPS, 24/7 상시 실행
- Claude Code 구독 플랜 (API 아님) → `claude` CLI headless 호출
- Python 오케스트레이터 + Telegram 봇

---

## 아키텍처

```
┌─────────────────────────────────────────────────┐
│                    VPS (24/7)                   │
│                                                 │
│  ┌─────────────────────────────────────────┐    │
│  │  RSS Poller  (5~10분 간격, 상시 실행)    │    │
│  │  - 새 기사 → SQLite pending_articles    │    │
│  │  - 크로스소스 수렴 감지                  │    │
│  │  - 볼륨 스파이크 감지                    │    │
│  │       ↓ breaking 감지 시                │    │
│  │  Telegram 즉시 알림                     │    │
│  └─────────────────────────────────────────┘    │
│                                                 │
│  ┌──────────────┐    ┌──────────────────────┐   │
│  │ Telegram Bot │    │  APScheduler         │   │
│  │ (inbound)    │    │  00/06/12/18 KST     │   │
│  └──────┬───────┘    └──────────┬───────────┘   │
│         └─────────────┬─────────┘               │
│                       ▼                         │
│           Python Orchestrator                   │
│           - pending_articles → input 파일 구성  │
│           - 파일 I/O, 락파일, 로그              │
│           - Neo4j CRUD                          │
│           - 대화 히스토리 관리                  │
│                       │                         │
│            claude -p (headless)                 │
│                       ▼                         │
│           Claude CLI                            │
│           - 기사 중요도 판단                    │
│           - 중복 제거, 인과 분석                │
│           - 엔티티/관계 추출                    │
│           - 브리핑 작성                         │
│           - 팔로업 쿼리 답변                    │
│                                                 │
│  Neo4j          SQLite            /workspace/   │
│  (그래프 DB)    pending_articles  cycles/       │
│                 seen_articles     briefs/ me/   │
└─────────────────────────────────────────────────┘
```

---

## 코드베이스 구조

```
newsparser/
├── bot/
│   ├── telegram_bot.py      # 항상 실행, 인바운드 수신
│   └── dispatcher.py        # 슬래시커맨드 vs 자유쿼리 라우팅
├── scheduler/
│   └── cron.py              # APScheduler로 cycle/morning/weekly/reflect 등록
├── collector/
│   ├── poller.py            # RSS 상시 폴링 (5~10분), pending_articles 적재
│   ├── alert.py             # 크로스소스 수렴 + 볼륨 스파이크 감지
│   ├── scraper.py           # trafilatura fallback
│   └── sources.py           # sources.md 로드
├── store/
│   └── sqlite.py            # pending_articles, seen_articles 관리
├── graph/
│   ├── neo4j_client.py      # 드라이버, 커넥션
│   ├── writer.py            # Claude 출력 파싱 → entity/relation INSERT
│   └── traversal.py         # Tracker용 graph traversal 쿼리
├── claude/
│   └── runner.py            # claude CLI subprocess 호출 + 결과 대기
├── workspace/               # Claude가 읽고 쓰는 파일들
│   ├── cycles/
│   ├── briefs/
│   ├── input/
│   └── me/
└── CLAUDE.md                # Claude 행동 지침 (plan.md 기반)
```

---

## 소스

**국내:** 매일경제, 한국경제, 연합인포맥스  
**해외:** Reuters, Financial Times, federalreserve.gov  
수집 전략: RSS 우선 → HTML 필요 시 trafilatura → 페이월 사이트는 RSS 요약으로만 분석

---

## SQLite 테이블 (경량 운영 저장소)

```sql
-- 수집된 기사 큐 (6시간 cycle 전까지 보관)
CREATE TABLE pending_articles (
    guid        TEXT PRIMARY KEY,
    source      TEXT,
    title       TEXT,
    url         TEXT,
    published   TIMESTAMP,
    body        TEXT,
    fetched_at  TIMESTAMP,
    alerted     BOOLEAN DEFAULT FALSE,
    processed   BOOLEAN DEFAULT FALSE
);

-- 중복 방지용 (영구 보관)
CREATE TABLE seen_articles (
    guid        TEXT PRIMARY KEY,
    seen_at     TIMESTAMP
);
```

Neo4j에는 기사가 들어가지 않는다. 기사에서 Claude가 추출한 엔티티와 관계만 들어간다.

---

## 실시간 Breaking 감지 (LLM 없음)

RSS Poller가 새 기사를 받을 때마다 Python이 두 가지 신호를 계산한다.

**크로스소스 수렴:** 15분 윈도우 안에 N개 이상의 소스에서 제목 단어 겹침(Jaccard)이 임계값 이상인 기사 클러스터가 형성되면 breaking으로 판단.

**볼륨 스파이크:** 소스별 시간당 평균 게시량 대비 현재 게시량이 3배 이상이면 신호 발생.

| 신호 조합 | 판단 |
|-----------|------|
| 단일 소스 볼륨 스파이크 | 해당 소스 breaking 후보 |
| 멀티 소스 수렴 | 시장 전체 이벤트 |
| 둘 다 | 즉시 Telegram 알림 |

키워드 목록 관리 없음. 자기유지형.

---

## 데이터 플로우

### RSS Poller (상시, 5~10분 간격)

```
RSS fetch (전 소스)
→ GUID 확인 → seen_articles에 없으면 신규
→ 본문 fetch (trafilatura fallback)
→ pending_articles INSERT, seen_articles INSERT
→ 크로스소스 수렴 + 볼륨 스파이크 계산
→ breaking 감지 시 → Telegram 즉시 알림
                      alerted = TRUE 마킹
```

### `/cycle` (00:00 / 06:00 / 12:00 / 18:00 KST)

```
APScheduler 트리거
→ Python: 락파일 체크
→ Python: pending_articles WHERE processed=FALSE 조회
          → workspace/input/YYYY-MM-DD-HH-input.md 작성
→ claude -p "/cycle"
  - input 파일 읽고 분석
  - workspace/cycles/YYYY-MM-DD-HH.md 작성
→ Python: Graph updates 섹션 파싱 → Neo4j INSERT/UPDATE
→ Python: processed=TRUE 마킹, 락파일 제거, 로그 기록
```

### `/morning` (07:00 KST)

```
APScheduler 트리거
→ claude -p "/morning"
  - 최근 4개 cycle 리포트 읽기
  - interests.md 참조
  - 브리핑 작성 (7슬롯)
→ Python: stdout 캡처
→ Telegram 발송
→ workspace/briefs/YYYY-MM-DD.md 저장
```

### Tracker (Telegram 인바운드 자유 쿼리)

```
유저 메시지 수신
→ Python: 키워드/엔티티 추출
→ Neo4j: 2-hop traversal → 관련 entity/relation 수집
→ workspace/sessions/{chat_id}.jsonl 에서 대화 히스토리 로드
→ claude -p "/tracker [query + graph context + history]"
→ Python: 응답 캡처 → Telegram 발송
→ 대화 히스토리 + interest-events.jsonl 업데이트
```

---

## Neo4j 스키마

### 노드 레이블

| 레이블 | 예시 |
|--------|------|
| `Company` | 삼성전자, Apple, TSMC |
| `Person` | 파월, 이창용, 트럼프 |
| `Institution` | Fed, BOK, IMF, 기재부 |
| `Event` | FOMC 5월 회의, 삼성 1Q 실적 |
| `Indicator` | CPI, GDP, 기준금리 |
| `Market` | KOSPI, S&P500, KRW/USD |
| `Sector` | 반도체, 에너지, 금융 |
| `Policy` | QT, 관세, 밸류업 |

**공통 property:** `canonical_name`, `aliases[]`, `first_seen`, `last_seen`, `mention_count`

### 관계 타입

| 관계 | 설명 |
|------|------|
| `INFLUENCES` | Fed금리 → KRW/USD |
| `MEMBER_OF` | 이창용 → BOK |
| `COMPETES_WITH` | 삼성전자 → TSMC |
| `ANNOUNCED` | 삼성전자 → 1Q실적 |
| `IMPACTS` | FOMC회의 → KOSPI |
| `CONTRADICTS` | 신호 간 충돌 기록 |
| `FOLLOWS_UP` | 스토리 연속성 (이전 cycle 연결) |

**관계 property:**

| property | 설명 |
|----------|------|
| `confidence` | 관계 존재 확신도 (0~1) |
| `impact_score` | 실제 파급력 (0~1, 실험적) |
| `first_seen` | 최초 관측 |
| `last_seen` | 최근 관측 |
| `source_cycles[]` | 이 관계를 언급한 cycle 목록 |
| `predicate_text` | 자연어 설명 |

### impact_score 업데이트 규칙 (실험적)

Claude가 관계 최초 등록 시 초기 추정값 설정. 이후 같은 관계를 언급하는 후속 기사가 등장할 때마다 EMA로 업데이트:

```
new_impact = 0.85 × old_impact + 0.15 × new_signal
```

`source_cycles[]` 길이가 `reinforcement_count`를 자동으로 나타낸다.

### Traversal 쿼리 패턴 (Tracker용)

```cypher
-- 2-hop 컨텍스트 수집
MATCH (e:Entity {canonical_name: $name})-[r*1..2]-(related)
WHERE related.last_seen >= date() - duration({days: 7})
RETURN related, r
ORDER BY related.mention_count DESC
LIMIT 20

-- 영향 체인 추적
MATCH path = (e {canonical_name: $name})-[:IMPACTS|INFLUENCES*1..3]->(target)
RETURN path

-- 최근 고파급력 스토리
MATCH ()-[r]->()
WHERE r.last_seen >= date() - duration({days: 1})
  AND r.impact_score > 0.7
RETURN r
ORDER BY r.impact_score DESC
```

---

## Claude ↔ Python 인터페이스

### Input 파일 포맷 (Python → Claude)

```markdown
# Input YYYY-MM-DD-HH KST

## Collected Articles (N total)

### [매일경제] 기사 제목
- URL: ...
- Published: ...
- Body:
  기사 본문 전체

### [Reuters] Article Title
...
```

Claude는 이 파일을 읽고 분석. 이전 cycle 리포트는 경로를 CLAUDE.md에 지시해서 Claude가 직접 읽음.

### Graph Updates 섹션 포맷 (Claude → Python)

```markdown
## Graph updates
### Entities
- NEW | Company | 삼성전자 | aliases: [Samsung, 삼성] | metadata: {sector: "반도체"}
- UPDATE | Institution | Fed

### Relations
- NEW | FOMC 5월 회의 --IMPACTS[conf:0.85, impact:0.80]--> KOSPI | 금리동결 서프라이즈
- NEW | FOMC 5월 회의 --INFLUENCES[conf:0.80, impact:0.70]--> KRW/USD
- UPDATE | 미국금리 --IMPACTS[conf:0.90, impact:0.85]--> 반도체섹터
```

Python이 정규식으로 파싱 → Cypher 변환 → Neo4j INSERT.

---

## 멀티턴 쿼리 처리 (Tracker)

각 Telegram 채팅별 `workspace/sessions/{chat_id}.jsonl` 유지:

```jsonl
{"role": "user", "content": "...", "ts": "..."}
{"role": "assistant", "content": "...", "ts": "..."}
```

새 쿼리마다 Python이:
1. 히스토리 로드 (최근 N턴)
2. Neo4j traversal로 관련 그래프 컨텍스트 구성
3. 둘 다 Claude에 전달 (one-shot)
4. 응답 후 히스토리 append

---

## 미결 사항

- `impact_score` 실험 검증 방법 (충분한 데이터 쌓인 후 결정)
- 페이월 사이트 (FT, WSJ) 처리 정책 확정 — RSS 요약만 사용할지
- `/weekly` 그래프 정리 절차 상세 설계 (plan.md 섹션 7.2 기반)
- Telegram 봇 토큰 및 chat_id 설정
