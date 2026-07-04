# Newsparser를 다른 VM으로 마이그레이션

전체 시스템을 새 호스트로 옮기기 = **상태**(`backup.sh` 아카이브) 전송 + **코드**는 git으로
직접 가져오기 + **호스트 프로비저닝**(`deploy/install.sh`). 백업은 설계상 상태만 담는다.

구조 (plan-host-migration.md 참고): poller/dispatcher는 **호스트 systemd 유닛**으로 돌고
(`newsbot-poller.service`, `newsbot-dispatcher.service`), 컨테이너는 **neo4j 하나뿐**이다.
dispatcher가 claude CLI를 서브프로세스로 실행하므로 호스트에 claude 설치·로그인이 필요하다.
`.venv`는 호스트 것을 직접 쓰므로 **`uv sync`가 필수 단계다** (컨테이너 시절과 반대).

---

## 한눈에 보기

```bash
# --- 옛 VM ---
./backup.sh
# manifest 확인, 특히 그래프 줄 ("Neo4j는 best-effort" 절 참고):
#   neo4j : graph dump included = yes
scp backups/newsparser-backup-*.tar.gz* new-vm:/tmp/

# --- 새 VM (사전 준비: docker + uv + claude CLI 설치, claude 로그인) ---
git clone <repo-url> newsbot && cd newsbot     # 코드는 백업에 없음
uv sync                                        # 호스트 .venv — 필수
mkdir -p backups && mv /tmp/newsparser-backup-*.tar.gz* backups/
./restore.sh backups/newsparser-backup-XXXXXXXX-XXXXXX.tar.gz
docker compose up -d                           # neo4j만 (127.0.0.1 바인딩)
sudo ./deploy/install.sh                       # systemd 유닛 + newsbot-ops + sudoers
sudo systemctl start newsbot-poller newsbot-dispatcher
```

---

## 백업에 담기는 것 / 안 담기는 것

`backup.sh`는 git으로 복구 **불가능**한 것만 담는다:

| 아카이브에 포함 | 아카이브에 없음 (직접 제공) |
|---|---|
| `workspace/` 아래 모든 SQLite DB — 일관성 있는 온라인 스냅샷 | 레포 코드, `docker-compose.yml`, `deploy/` |
| 런타임 문서: `cycles/`, `briefs/`, `me/`, `input/`, `logs/`, `sessions/` | `.claude/`, `mcp.json`, `CLAUDE.md` (git 추적 설정) |
| `.env` (시크릿) — `--no-secrets` 아니면 | `.venv/` (호스트별 — `uv sync`로 생성, **필수**) |
| Neo4j 그래프 dump — **best effort**, 아래 참고 | claude CLI + `~/.claude` 자격증명 (호스트별 로그인) |

---

## 호스트 사전 요구사항 (새로 생긴 것)

1. **claude CLI**: `curl -fsSL https://claude.ai/install.sh | bash` 후 로그인
   (`~/.claude` 자격증명). `deploy/install.sh`가 경로를 찾아 유닛의 `CLAUDE_BIN`에 박는다.
2. **uv** + `uv sync` — dispatcher/poller가 호스트 `.venv/bin/python`으로 돈다.
3. **docker** — neo4j 전용. 서비스 유저를 docker 그룹에 넣을 필요는 없다
   (neo4j 제어는 root 소유 `newsbot-ops`가 담당).
4. `.env`의 `NEO4J_URI`: compose 시절 값 `bolt://neo4j:7687`이 남아 있으면
   `bolt://localhost:7687`로 바꾼다 (install.sh가 경고해준다).

---

## `restore.sh`를 써야 하는 이유 (수동 cp 금지)

- **오래된 SQLite WAL 사이드카 정리** (`*.db-wal/-shm/-journal`) — 남아 있으면 복구된 DB가
  첫 오픈 때 옛 데이터로 조용히 롤백된다.
- **`.sha256` 체크섬 검증** — 손대기 전에 먼저.
- **Neo4j dump 로드** (neo4j stop → `neo4j-admin database load` → 재기동).
- **`.env` 복원** — 기존 `.env`가 없을 때만 (`--restore-env`로 덮어쓰기).
- 타깃 workspace가 비어 있지 않으면 **사전 안전 백업**.

`.env`는 백업에 들어 있고 그대로 복원되므로 동작하던 설정이 그대로 넘어온다.
Neo4j 비밀번호는 새 VM에서 자유롭게 바꿀 수 있다 — dump는 인증이 아니라 데이터만 담고,
새 볼륨이 `.env`의 `NEO4J_PASSWORD`로 초기화된 뒤 dump가 로드된다.

---

## ⚠️ Neo4j는 best-effort — manifest를 확인하라

SQLite 스냅샷은 all-or-nothing이지만 **Neo4j dump는 아니다** — 백업 시 docker/neo4j가 없으면
`⚠ Neo4j dump failed — continuing without it`을 로그하고 그래프 없이 아카이브를 만든다.
마이그레이션 백업을 믿기 전에 소스 manifest에서 `neo4j : graph dump included = yes`를 확인하라.

---

## 마이그레이션 후 점검

1. `docker compose ps` — neo4j up (포트가 `127.0.0.1`에만 바인딩됐는지 확인).
2. `systemctl status newsbot-poller newsbot-dispatcher` — 둘 다 active.
   `journalctl -u newsbot-dispatcher -n 50` — "Loaded bots: [...]", "Dispatcher polling".
3. 텔레그램에서 봇에 메시지 — tracker가 복원된 그래프 + cycle 리포트로 답해야 함.
   `service_status` 도구로 3개 서비스 상태 확인 (sudoers 배선 검증).
4. cycle 1회 수동 실행(start_job) — 리포트 생성 + 거부(denial) 로그 없음 확인.
5. 소스 `MANIFEST.txt`의 행 수와 대조.
