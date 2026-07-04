# Plan A — 봇별 도구 정책 (오염 입력 격리)

원칙: **도구는 입력의 오염도를 따라간다.** 모델 티어(하이쿠/소넷)는 비용·품질 선택일 뿐 보안 경계가 아니다.
인터넷에서 수집한 뉴스 본문(및 그 파생물: 사이클 리포트, 그래프 엔티티명)을 읽는 claude 호출은
도구를 최소화하고, 신뢰 입력(ALLOWED_CHAT_ID로 게이트된 텔레그램 대화)만 넓은 권한을 가진다.

## 호출 지점별 정책

| 호출 지점 | 입력 오염도 | 모델 | 정책 |
|---|---|---|---|
| `classifier.classify_article` | 기사 원문 (최고 오염) | haiku | 무도구: `permission_mode="default"`, allowedTools 없음 |
| `classifier.classify_query` | 유저 쿼리 + 히스토리 | haiku | 무도구 (도구 필요 없음) |
| `graph/resolver.resolve_entities` | 사이클 파생 엔티티명 | haiku | 무도구 |
| `bot/tracker._needed_history_depth` | 대화 히스토리(파생 오염 포함) | haiku | 무도구 |
| `scripts/run_cycle` (`/cycle`) | 기사 원문 입력파일 | sonnet | `permission_mode="default"` + 화이트리스트 (아래) |
| `scripts/run_reflect` (`/reflect`) | 사이클 리포트 (파생 오염) | sonnet | `permission_mode="default"` + 파일 도구만 |
| `scripts/run_weekly` (`/weekly`) | 사이클 리포트 (파생 오염) | sonnet | `permission_mode="default"` + 파일 도구만 |
| `bot/tracker.run_tracker` | 유저 대화 (신뢰) | sonnet | 현행 유지: bypassPermissions + Bash + MCP |

## 화이트리스트 (cycle)

cycle.md가 지시하는 셸 명령은 정확히 2개뿐 — 화이트리스트 완성 가능:

```
Read, Write, Edit, Grep, Glob, TodoWrite,
Bash(.venv/bin/python newsparser/scripts/apply_graph.py:*),
Bash(.venv/bin/python newsparser/scripts/mark_processed.py:*)
```

reflect/weekly는 셸 명령 지시가 없음 → `Read, Write, Edit, Grep, Glob, TodoWrite`만.

## 공통 deny (왕관 보석 — allow가 아무리 넓어져도 우선)

`.claude/settings.json` `permissions.deny`:
- `Read(./.env)`, `Read(./.env.*)` — 토큰 전부
- `Read(~/.claude/**)` — claude 자격증명 (최대 리스크 자산)
- `Read(./backups/**)`, `Read(./*.tar.gz)` — 백업에 .env 포함됨

주의: bypassPermissions 모드(tracker)에서 deny가 강제되는지는 버전에 따라 다를 수 있음 —
설계는 이에 의존하지 않는다(오염 봇은 전부 default 모드, tracker는 신뢰 입력).

## 거부 가시화 (화이트리스트 갭이 조용히 썩지 않게)

`claude/runner.py`가 stream-json 이벤트를 이미 파싱하므로:
- `tool_result` + `is_error` + 권한거부 패턴 매칭 → `run.denials`에 기록
- 거부 발생 시 `logger.warning` + jobs.json activity에 `denied` 카운트/마지막 내역 노출
- 갭 발견 → cycle 화이트리스트에 규칙 한 줄 추가로 해결 (태스크가 고정이라 빠르게 수렴)

## 전역 설정

`.claude/settings.json`의 `defaultMode: bypassPermissions`는 이제 **인터랙티브 세션에만** 영향
(모든 헤드리스 호출이 명시적 `--permission-mode`를 넘기므로). 유지 여부는 사용자 취향.
deny 목록은 추가한다.

## 하이쿠/소넷 역할 (사용자 제안 반영)

- 하이쿠: 분류·정수 응답 등 무도구 단문 작업 전용. 컨텍스트 200K 한계도 있어 원문 대량 입력 부적합.
- 소넷: cycle/reflect/weekly/tracker. effort는 sonnet-5에서만 유효 (haiku 4.5는 effort 미지원, 400).

## 완료 기준

- [ ] 4개 무도구 호출 지점 전환
- [ ] cycle/reflect/weekly 화이트리스트 적용
- [ ] settings.json deny 규칙
- [ ] runner 거부 감지 + jobs.json 노출
- [ ] pytest 통과 + 헤드리스 거부 스모크 테스트 (permission-mode default에서 Bash 실측 거부 확인)
- [ ] (운영) 첫 실사이클 후 거부 로그 확인, 필요 시 규칙 추가
