# Newsparser를 다른 VM으로 마이그레이션

전체 시스템을 새 호스트로 옮기기 = **상태**(`backup.sh` 아카이브) 전송 + **코드**는 직접
가져오기. 백업은 설계상 상태만 담으므로, "restore 후 `docker compose up`만" 으로는
**부족**하다 — 아래 추가 단계 참고.

`docker compose`의 poller/dispatcher는 이제 레포 전체가 아니라 `workspace/`, `mcp.json`,
`.claude/`, `CLAUDE.md`만 바인드 마운트한다 (PR #12 "ghcr로 경로 변경"). `.venv/bin/python`은
**이미지에 구워진 venv**(Dockerfile의 `uv sync --frozen`, 빌드/푸시 시점)를 가리키므로 새
호스트에 `.venv`가 없어도 컨테이너는 정상 기동한다. **`uv sync`는 마이그레이션 절차에 필요
없다** — 호스트 `.venv`를 쓰는 곳(`restore.sh`의 workspace 검증, `backup.sh`/`scripts/`)은
전부 없으면 알아서 skip되거나 system `python3`로 폴백하는 선택 사항이다.

---

## 한눈에 보기

```bash
# --- 옛 VM ---
./backup.sh
# manifest 확인, 특히 그래프 줄 ("Neo4j는 best-effort" 절 참고):
#   neo4j : graph dump included = yes
# 그 다음 두 파일(아카이브 + 체크섬)을 박스 밖으로 복사:
scp backups/newsparser-backup-*.tar.gz* new-vm:/tmp/

# --- 새 VM ---
git clone <repo-url> newsbot && cd newsbot     # 코드는 백업에 없음
mkdir -p backups && mv /tmp/newsparser-backup-*.tar.gz* backups/
./restore.sh backups/newsparser-backup-XXXXXXXX-XXXXXX.tar.gz
docker compose up -d                           # neo4j + poller + dispatcher (이미지 자체 venv 사용)
```

이게 마이그레이션 전부다. 나머지는 각 단계가 왜 필요한지와 무엇을 확인할지 설명한다.

---

## 백업에 담기는 것 / 안 담기는 것

`backup.sh`는 git으로 복구 **불가능**한 것만 담는다:

| 아카이브에 포함 | 아카이브에 없음 (직접 제공) |
|---|---|
| `workspace/` 아래 모든 SQLite DB — `newsparser.db`, `market.db`, `state/claude_runs.db` — 일관성 있는 온라인 스냅샷 | 레포 코드 (`newsparser/`, `Dockerfile`, `docker-compose.yml`, 스크립트) |
| 런타임 문서: `cycles/`, `briefs/`, `me/`, `input/`, `logs/`, `sessions/` | `.claude/`, `mcp.json`, `CLAUDE.md` (git 추적 설정) |
| `.env` (시크릿) — `--no-secrets` 아니면 | `.venv/` (호스트별 — 마이그레이션엔 불필요, 이미지에 자체 venv 있음) |
| Neo4j 그래프 dump — **best effort**, 아래 참고 | `neo4j:5` / `python:3.12-slim` 이미지 (첫 `up`에 pull/build) |

따라서 새 VM에는 아카이브 단독이 아니라 **레포 + 아카이브**가 필요하다.

---

## 놓치기 쉬운 것: 코드가 새 VM에 먼저 있어야 한다

`backup.sh`는 git에 있는 건 일부러 건너뛴다 ("git으로 복구 가능한 건 전부 제외").
`restore.sh`는 **fresh checkout 안에서** 실행되는 걸 전제한다 — 레포를 먼저 clone(또는
복사)한 뒤 그 안으로 restore.

(예전엔 dispatcher가 `.:/app`으로 레포 전체를 바인드 마운트해서 호스트 `.venv`를 직접
실행했고, `uv sync`를 안 돌리면 dispatcher가 안 뜨는 사고가 있었다. PR #12에서 마운트를
`workspace/`, `mcp.json`, `.claude/`, `CLAUDE.md`로 줄이면서 `.venv/bin/python`이 이미지에
구워진 venv를 가리키게 됐고, 그 문제는 해소됨 — 지금은 호스트에 `uv sync`를 돌릴 필요가
없다.)

---

## 수동 복사 대신 `restore.sh`를 써야 하는 이유

`restore.sh`는 수동 `cp`로는 틀리기 쉬운 것들을 처리한다:

- **오래된 SQLite WAL 사이드카 정리** (`*.db-wal/-shm/-journal`) — 추출 전에 타깃에서 지운다.
  아카이브는 사이드카 없는 깔끔한 `.db` 스냅샷을 담는데, 남아 있던 사이드카가 복구된 DB의
  첫 오픈 때 체크포인트되면서 옛 데이터로 조용히 롤백시킨다.
- **`.sha256` 체크섬 검증** — 손대기 전에 먼저.
- **Neo4j dump 로드** (neo4j stop → `neo4j-admin database load` → 다시 기동).
- **`.env` 복원** — 기존 `.env`가 없을 때만 (`--restore-env`로 덮어쓰기).
- 타깃 workspace가 비어 있지 않으면 **사전 안전 백업**을 떠서 재실행을 되돌릴 수 있게 한다.
  진짜 새 VM이면 workspace가 비어 있어 이 단계는 생략된다.

---

## 마이그레이션 때의 `.env`

`.env`는 기본적으로 **백업 안에 들어 있고** `restore.sh`가 그대로 되돌려놓으므로, 동작하던
설정이 그대로 넘어온다. 즉 신규 배포에서의 `.env` 함정들(`IS_SANDBOX=1` 설정 필요,
`.env.example`에 `TELEGRAM_CHAT_ID` 누락)은 **마이그레이션엔 안 물린다** — 소스 `.env`에 이미
올바른 값이 있기 때문. 뭔가 바꿀 의도가 아니면 새 `.env`를 손대지 마라.

- 옛 설정을 **유지**: `restore.sh`가 `.env`를 복원하게 둔다 (기본).
- 뭔가 **변경** (예: 새 `NEO4J_PASSWORD`): `.env`를 먼저 만들고 restore하거나(기존 `.env`는
  덮어쓰지 않음), `--restore-env`로 받은 뒤 나중에 수정.

### Neo4j 비밀번호는 자유롭게 바꿀 수 있다

그래프 dump는 인증이 아니라 `neo4j` 데이터베이스만 담는다. 새 VM에서는 `neo4j_data` 볼륨이
새로 생성되며 `.env`의 `NEO4J_PASSWORD`(`NEO4J_AUTH` 경유)로 초기화된 뒤, 그 위로 dump가
로드된다. 따라서 새 비번 = 새 `.env`에 넣은 값이며, 옛 호스트와 **일치할 필요가 없다**.

---

## ⚠️ Neo4j는 best-effort — manifest를 확인하라

이게 진짜 함정 하나다. SQLite 스냅샷은 **all-or-nothing**이다 — DB 하나라도 못 담으면
`backup.sh`가 중단하고 아카이브를 아예 안 쓴다. **Neo4j dump는 그렇지 않다** — `backup.sh`
실행 시 docker나 neo4j 서비스가 없으면 `⚠ Neo4j dump failed — continuing without it`을
로그하고 **지식 그래프 없이** 아카이브를 만든다. 그래프가 빠진 건 마이그레이션 후에야 알게 된다.

마이그레이션 백업을 믿기 전에 항상 소스 manifest를 확인하라:

```
neo4j : graph dump included = yes
```

(`backup.sh`는 일관된 dump를 위해 neo4j를 잠깐 멈췄다 재시작하므로, 스택이 떠 있는 상태에서도
캡처가 정상 동작한다 — 백업 시점에 docker만 올라와 있으면 된다.)

---

## 마이그레이션 후 점검

1. `docker compose ps` — `neo4j`, `poller`, `dispatcher` 셋 다 up.
2. `docker compose logs dispatcher` — "Loaded bots: [...]", "Dispatcher polling" 보이고 인증
   (401)이나 이미지 pull/build 에러 없음.
3. 봇에 메시지 보내기 — 복원된 그래프 + cycle 리포트로 답해야 함.
4. 소스 `MANIFEST.txt`의 행 수와 대조 (테이블별 row count가 적혀 있음).

호스트 재부팅 후 dispatcher가 `bolt://neo4j:7687` 접속 실패로 crash-loop 돌면, `neo4j`
서비스에 아직 `restart:` 정책이 없어서다 — 수동으로 띄우거나(`docker compose up -d neo4j`)
`restart: unless-stopped`를 추가해라.
