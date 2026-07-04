# Plan B — 디스패처/폴러 호스트 이전 (컨테이너는 neo4j만)

배경: dispatcher 컨테이너의 격리는 docker.sock 마운트 순간 이미 없었고(호스트 root 동급),
남은 건 불편뿐 — 코드가 이미지에 구워져 claude가 자기 코드를 고칠 수 없고, 자기 재시작이 자살 호출.
컨테이너의 순가치는 서드파티 서비스 패키징 = neo4j 하나.

## 목표 구조

```
[호스트]
  newsbot-poller.service    (systemd, .venv 직접 실행)
  newsbot-dispatcher.service (systemd; telegram + cron + JobManager + claude 서브프로세스)
  /usr/local/sbin/newsbot-ops (root:root 755 — 유일한 무인 root 경로, sudoers NOPASSWD)
[docker compose]
  neo4j (127.0.0.1 바인딩)
```

- 배포 = `git pull && uv sync && sudo -n newsbot-ops restart dispatcher`
- claude는 호스트 repo에서 직접 작업 — 코드 수정이 곧 라이브 반영 대상, git이 격리 담당
- Dockerfile / .github/workflows/build.yml / ghcr 파이프라인 삭제

## 리포 내 산출물

| 파일 | 역할 |
|---|---|
| `docker-compose.yml` | neo4j 단독, `127.0.0.1:7474/7687` 바인딩 (docker는 ufw 우회하므로 필수) |
| `deploy/newsbot-poller.service` | 템플릿 (`@USER@`, `@ROOT@` 치환) |
| `deploy/newsbot-dispatcher.service` | 템플릿 (+ `CLAUDE_BIN=@CLAUDE_BIN@`) |
| `deploy/newsbot-ops` | root 소유로 설치될 ops 스크립트 (아래) |
| `deploy/install.sh` | sudo로 수동 실행하는 프로비저닝 (아래) |
| `newsparser/mcp_server.py` | service_status/restart_service/tail_logs를 `sudo -n /usr/local/sbin/newsbot-ops` 경유로 |

## newsbot-ops (보안 경계의 전부)

- **repo 밖 root:root 755로 설치** — repo 안 사본에 sudo를 열면 claude의 코드수정 권한이 곧 root
- 인자 화이트리스트: action ∈ {status, start, stop, restart, logs}, service ∈ {poller, dispatcher, neo4j}
- poller/dispatcher → systemctl (unit: newsbot-*), neo4j → `docker compose --project-directory @ROOT@`
- `restart dispatcher`: import 가드(`.venv/bin/python -B -c "import newsparser.dispatcher"`) 통과 시에만
  `systemd-run --on-active=5s systemctl restart` (detached — claude가 답장 보내고 죽는 것 허용)
- logs: poller/dispatcher는 journalctl -u, neo4j는 docker logs (root이므로 그룹 불필요)
- PATH 고정, `set -euo pipefail`, 인자 이어붙이기 금지

## install.sh (사람이 sudo로 실행 — 이게 승인 게이트)

1. `@USER@`(SUDO_USER)/`@ROOT@`/`@CLAUDE_BIN@` 치환해 유닛 2개 → /etc/systemd/system/
2. newsbot-ops → /usr/local/sbin/ (root:root 755)
3. /etc/sudoers.d/newsbot 생성 (`visudo -cf` 검증 후 설치, 0440)
4. daemon-reload + enable
5. **실행 전 `git diff deploy/`를 확인하는 습관이 이 게이트의 실체** — claude가 install.sh/ops를
   변조해도 root가 되려면 사람이 승인(재설치)해야 한다

## 프로드 컷오버 체크리스트 (운영 머신에서 — 이 박스는 dev)

1. [ ] 사전 재검증: claude 설치 경로(`which claude`), `~/.claude` 자격증명, `.venv` 존재, `uv sync`
2. [ ] `.env`에 `NEO4J_URI`가 있으면 `bolt://localhost:7687`로 (compose 내부 DNS `bolt://neo4j:7687` 제거)
3. [ ] `./backup.sh` 전체 백업
4. [ ] `docker compose down` (기존 3-서비스)
5. [ ] `git pull` (새 compose/deploy) → `docker compose up -d` (neo4j만, 127.0.0.1 바인딩 확인)
6. [ ] `sudo chown -R $USER:$USER workspace/` (컨테이너 root 소유 파일 정리)
7. [ ] `sudo ./deploy/install.sh`
8. [ ] `systemctl start newsbot-poller newsbot-dispatcher` → journalctl로 기동 확인
9. [ ] 텔레그램에서 tracker로 `service_status` / `tail_logs` / (확인 후) `restart_service` 동작 확인
10. [ ] cycle 1회 수동 실행(`start_job`) → 리포트 생성 + 거부 로그 없음 확인
11. [ ] backup.sh 재실행 (neo4j 덤프 경로 그대로인지)
12. [ ] ghcr 이미지/워크플로 정리, migration.md 갱신

## 남는 제약 (구조로 못 없애는 것)

- `restart dispatcher`는 실행 중인 장기 job(claude 자식 프로세스)을 죽인다 — 어떤 구조로도 불가피.
  detached 재시작으로 "답장 후 재시작"까지는 보장. 장기 job 중에는 재시작을 피할 것 (job_status로 확인).
- ops 스크립트 수정은 repo 편집 + `sudo ./deploy/install.sh` 재실행이 필요 (의도된 마찰).

## 완료 기준 (dev)

- [ ] compose/deploy 산출물 작성, Dockerfile·build.yml 삭제
- [ ] mcp_server.py 서비스 도구 재작성 + 테스트 갱신
- [ ] bash -n / import / pytest 통과
- [ ] migration.md에 새 구조 반영
