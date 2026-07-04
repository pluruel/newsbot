# 엔티티 흩어짐 백필 — Neo4j 그래프 재구축

기존 그래프는 엔티티 정규화(리졸버) 없이 쌓여서 같은 실체가 여러 노드로 흩어져 있을 수 있다
("Tesla" vs "테슬라" vs "TSLA"). `apply_graph.py`/`restore_graph_from_cycles.py`에 리졸버가
붙었으니, **그래프를 비우고 사이클 리포트 전체를 처음부터 다시 재생**하면 이후부터는 흩어지지
않는다 — 사후 병합이 아니라 애초에 안 흩어지게 다시 만드는 것.

리포트 `.md` 파일이 유일한 진실의 원천이라 이 방식이 가능하다. **리포트 파일 자체는 절대
건드리지 않는다** — Neo4j만 지웠다가 다시 채운다.

---

## 한눈에 보기

```bash
# 0. dispatcher 잠깐 멈춤 (재구축 도중 새 사이클이 그래프에 쓰는 걸 막음)
docker compose stop dispatcher

# 1. 백업 (필수 — 되돌리기 어려운 작업)
./backup.sh
#   MANIFEST에서 확인: neo4j : graph dump included = yes
#   (no면 docker/neo4j 상태 확인 후 재시도. 이거 없이 진행하지 말 것.)

# 2. 그래프 비우기 전 노드/관계 수 기록 (비교용)
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "MATCH (n) RETURN count(n) AS nodes;"
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "MATCH ()-[r]->() RETURN count(r) AS rels;"

# 3. 그래프 비우기
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "MATCH (n) DETACH DELETE n;"

# 4. 재생 — 사이클 리포트 전체를 리졸버 경유로 다시 적용
.venv/bin/python scripts/restore_graph_from_cycles.py

# 5. dispatcher 재개
docker compose start dispatcher
```

`$NEO4J_PASSWORD`는 `.env`에 있음 — 셸에 미리 export 해두거나(`set -a; source .env; set +a`),
`-p` 뒤에 실제 값을 직접 넣어도 된다.

---

## 왜 dispatcher를 먼저 멈추나

`dispatcher`가 크론 사이클마다 `apply_graph.py`를 돌려 그래프에 쓴다. 2~4단계 사이에 새 사이클이
끼어들면 비운 직후의 빈 그래프에 쓰거나, 재생 도중에 동시에 쓰면서 레이스가 생길 수 있다.
`poller`(기사 수집, SQLite만 건드림)는 안 멈춰도 된다.

---

## 왜 백업이 필수인가

3단계(`DETACH DELETE n`)는 그래프 전체 삭제다. 4단계 재생이 정상적으로 끝나면 문제없지만,
중간에 실패하거나(Haiku 호출 실패, 파싱 에러 등) 재생 결과가 기대와 다르면 되돌릴 방법이
백업뿐이다. `backup.sh`의 Neo4j dump는 **best-effort**라 실패해도 조용히 넘어갈 수 있으니,
MANIFEST에서 `neo4j : graph dump included = yes`를 반드시 눈으로 확인하고 진행할 것.

---

## 재생이 하는 일 / 안 하는 일

`scripts/restore_graph_from_cycles.py`는:

- `workspace/cycles/{tech,markets}/*.md`를 파일명(=시간) 순으로 정렬해 순서대로 재생
- 각 리포트를 `prepare_graph_updates()`(리졸버 리네임 + ignore 필터)에 통과시킨 뒤 `apply_graph_updates()` 호출
- 시간순이라 리졸버가 그래프가 채워지는 과정을 그대로 지켜봄 — 이미 있는 엔티티는 다음부터 자동으로 정규화된 이름에 붙음

**안 하는 것** (알아둘 것, 재생 후 놀라지 않게):

- `source_article_guids` 재구성 안 함 — 원본 사이클 슬롯의 `-guids.txt` 인덱스를 다시 안 풂
- 가격 반응 주석(`maybe_annotate_impacts`) 재실행 안 함 — impact 주석이 필요하면 별도로 처리해야 함
- `first_seen`/`last_seen`은 재생 시점(지금)으로 찍힘, 원래 시간이 아님

이 셋이 필요하면 지금 이 백필 범위 밖이니 별도로 얘기하자.

---

## 검증

```bash
# 재생 후 노드/관계 수 (2단계 기록값과 비교)
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "MATCH (n) RETURN count(n) AS nodes;"
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "MATCH ()-[r]->() RETURN count(r) AS rels;"
```

노드 수는 이전보다 **같거나 줄어야** 한다 (흩어졌던 게 합쳐졌으면 줄어듦). 관계 수는 리포트
개수에 비례해 비슷한 범위여야 함 — 크게 차이 나면 재생 로그(`[ok] ... N entities, M relations`)를
훑어서 파싱 실패나 리졸버 에러가 없었는지 확인.

```bash
# 눈으로 훑어보기 — 특정 라벨 엔티티가 잘 정규화됐는지
docker compose exec neo4j cypher-shell -u neo4j -p "$NEO4J_PASSWORD" \
  "MATCH (e:Company) RETURN e.canonical_name, e.aliases, e.mention_count ORDER BY e.mention_count DESC LIMIT 50;"
```

비슷한 이름(예: "Tesla"와 "테슬라")이 여전히 별개 행으로 남아 있으면 리졸버가 놓친 것 —
로그에서 해당 사이클의 재생 결과 확인.

마지막으로 텔레그램으로 트래커 봇에 그래프 관련 질문을 던져서 정상 응답하는지 확인.

---

## 문제 생기면

1단계 백업으로 롤백:

```bash
docker compose stop dispatcher
./restore.sh -y --no-safety backups/newsparser-backup-<타임스탬프>.tar.gz
docker compose start dispatcher
```

- `-y`: 기존 workspace 덮어쓰기 확인 프롬프트 생략 (빼면 대화형으로 y/N 물어봄).
- `--no-safety`: `restore.sh`는 기본적으로 덮어쓰기 전 현재 상태를 또 백업해두는데, 지금은
  실패한 재생 결과를 백업하는 거라 의미 없어서 생략.
- `--no-neo4j`는 **쓰지 말 것** — 그래프 롤백이 이 복구의 핵심이니 워크스페이스와 함께
  그래프도 복원해야 두 상태가 일치한다.
