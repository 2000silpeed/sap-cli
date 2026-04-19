# SAP CLI — 레시피 축적·검증 실전 메뉴얼 (Phase 18 중심)

> 본 문서는 SAP CLI 사내 검증 단계에서 **AI 내비게이션 레시피를 실제로 축적·검증·관리**하기 위한 실무 메뉴얼이다.
> 환경 설정은 `SETUP.md`, 기본 기능(Phase 0~17) 검증 시나리오는 `VERIFICATION.md` 참조.
> 이 문서는 Phase 18 (자동 제안 / 품질 관리 / 버전닝 / 크로스 트랜잭션) 을 **실제 사내 SAP에서** 돌려보며 데이터를 쌓는 루틴에 집중한다.

---

## 목차

1. 사전 체크리스트
2. 일일 운영 루틴 (Daily Routine)
3. Phase 18-1 검증 — 자동 레시피 제안
4. Phase 18-2 검증 — 레시피 품질 관리 (실패/비활성화)
5. Phase 18-3 검증 — 레시피 버전닝
6. Phase 18-4 검증 — 크로스 트랜잭션 체인
7. 주간·월간 품질 점검 루틴
8. 실무 축적 시나리오 예시 (VA01 / MM03 / SPRO→MM03)
9. 트러블슈팅
10. 축적 KPI & 롤백 규칙

---

## 1. 사전 체크리스트

아래 항목이 모두 충족돼야 Phase 18 검증이 의미 있다.

- [ ] `SETUP.md` 전 과정 완료 (SAP GUI 스크립팅 TRUE, `pip install -e .[mcp]`)
- [ ] `VERIFICATION.md` 시나리오 12 (레시피 저장/검색/재활용) 최소 1회 성공
- [ ] `VERIFICATION.md` 시나리오 17 (MCP 세션 로깅 + 승인) 최소 1회 성공
- [ ] Claude Code MCP에 `sap-cli` 서버가 붙어 있고 `sap_recipe_pending`, `sap_recipe_chain_search` 툴이 목록에 보임
- [ ] `recipes/` 디렉토리 쓰기 권한 OK (`ls -la recipes/` 로 확인)
- [ ] 사내 QAS/DEV 시스템 접속 계정 (PRD 직접 검증 금지)

확인 명령:

```bash
sap --help | head -30
python -c "from sap_cli.recipe import RecipeStore, AUTO_DISABLE_MIN_TOTAL, AUTO_DISABLE_FAILURE_RATE; print(AUTO_DISABLE_MIN_TOTAL, AUTO_DISABLE_FAILURE_RATE)"
# 예상 출력: 3 0.5
```

---

## 2. 일일 운영 루틴 (Daily Routine)

매일 SAP 작업 시작~종료까지 이 순서를 그대로 따른다.
**중요:** Phase 18-1 자동 제안은 **session_end 시점**에 발생하므로 세션을 반드시 열고 닫아야 한다.

### 2.1 작업 시작 (Claude Code + MCP 기준)

```
1) sap_connect           # 또는 데몬 모드로 이미 연결돼 있으면 생략
2) sap_session_start     { "label": "YYYY-MM-DD MM03 조회 업무" }
   → 응답의 session_id 를 기억/기록
3) sap_run { "tcode": "MM03" }   # 본 작업 시작
```

### 2.2 작업 진행 중

- 평소처럼 `sap_set / sap_fill / sap_press / sap_send_vkey` 조합으로 업무 수행
- 중요 화면마다 `sap_screen` 또는 `sap_capture` 로 상태 확인
- 한 세션에서 **여러 tcode**를 거쳐도 무방 (Phase 18-4 크로스 체인 후보가 됨)

### 2.3 작업 종료 (레시피 축적 포인트)

```
4) sap_session_end { "session_id": "<2단계에서 받은 id>" }
   → 응답에 recipe_suggestion 이 있으면 자동 후보 발생
5) sap_recipe_pending        # 승인 대기 목록 확인
6) sap_session_approve { "session_id": "<id>", "name": "MM03_재무뷰_조회" }
   → 승인 즉시 레시피 파일 생성 (recipes/<id>.json)
```

### 2.4 실행(재활용) 루틴

```
sap_recipe_search { "query": "MM03 재무" }
sap_recipe_show   { "recipe_id": "<id>" }
# 성공 시
sap_recipe_success { "recipe_id": "<id>" }
# 실패 시
sap_recipe_failure { "recipe_id": "<id>", "reason": "스크린 ID 변경" }
```

**원칙:** 성공/실패는 **매 실행마다** 기록한다. Phase 18-2 자동 비활성화는 이 데이터에 전적으로 의존한다.

---

## 3. Phase 18-1 검증 — 자동 레시피 제안

**목적:** `session_end` 시 자동으로 `recipe_suggestion` 이 붙고, 승인 전까지 `sap_recipe_pending` 에서 조회되는지 확인.

### 3.1 후보 판정 조건 (사전 지식)

`sap_cli/session_analyzer.py` 기준:
- `core_steps ≥ 3` (tcode 실행, set/fill, press/vkey 등 의미 있는 스텝)
- `has_tcode == True` (세션 내 최소 1회 tcode 전환)
- `session.candidate == True` 이고 `session.approved == False` 여야 제안됨

### 3.2 시나리오 A — 후보 O (제안 O)

작업:

```
sap_session_start { "label": "P18-1 후보 테스트" }
sap_run     { "tcode": "MM03" }
sap_set     { "field": "RMMG1-MATNR", "value": "<유효자재>" }
sap_press   { "id": "btn[0]" }   # 엔터
sap_send_vkey { "vkey": 0 }
sap_session_end { "session_id": "<id>" }
```

기대 응답 (`session_end` payload):

```jsonc
{
  "status": "ok",
  "session_id": "...",
  "candidate": true,
  "approved": false,
  "recipe_suggestion": {
     "name_hint": "...",
     "tcode_sequence": ["MM03"],
     "core_steps": 4,
     ...
  }
}
```

검증 후속:

```
sap_recipe_pending
# → 위 session_id 가 목록에 포함돼야 함
sap_session_approve { "session_id": "<id>", "name": "P18-1_MM03_후보_A" }
sap_recipe_pending
# → 목록에서 사라져야 함 (approved 처리됨)
```

### 3.3 시나리오 B — 후보 X (제안 없음)

작업 (스텝 2개만):

```
sap_session_start { "label": "P18-1 비후보" }
sap_screen
sap_session_end { "session_id": "<id>" }
```

기대: `candidate: false`, `recipe_suggestion` 필드 **없음** 또는 `null`.

### 3.4 체크포인트

- [ ] A 시나리오에서 `recipe_suggestion` 자동 생성
- [ ] `sap_recipe_pending` 에 A 의 session_id 노출
- [ ] approve 후 `sap_recipe_pending` 에서 사라짐
- [ ] B 시나리오는 제안 없음 (오탐지 없음)

---

## 4. Phase 18-2 검증 — 레시피 품질 관리

**목적:** 실패 카운트 누적 → 임계 돌파 시 자동 `disabled` → 수동 `enable` 복구 경로 확인.

### 4.1 임계치 (모듈 상수)

- `AUTO_DISABLE_MIN_TOTAL = 3` — 최소 3회 이상 실행 이력 있어야 판단
- `AUTO_DISABLE_FAILURE_RATE = 0.5` — 실패율 50% 이상이면 자동 비활성화
- 계산: `failure_count / (success_count + failure_count) ≥ 0.5`

### 4.2 시나리오 — 실패 누적으로 자동 disabled

준비: 검증용 레시피 1개 저장 (`P18-2_disabled_test` 라는 이름으로)

```
sap_recipe_list
# → 대상 recipe_id 확인
```

실행 로그를 다음 순서로 기록:

| 회차 | 명령 | 누적 S/F | disabled? |
|------|------|----------|-----------|
| 1 | `sap_recipe_success` | 1 / 0 | false |
| 2 | `sap_recipe_failure` | 1 / 1 | false (total=2, 미달) |
| 3 | `sap_recipe_failure` | 1 / 2 | **true** (total=3, 실패율 66%) |

3회차 직후 확인:

```
sap_recipe_show { "recipe_id": "<id>" }
# → disabled: true, disabled_reason: "auto: failure_rate=0.67 ..."
```

### 4.3 수동 복구

SAP 화면 이슈(예: 스크린 ID 변경) 해결 후:

```
sap_recipe_enable { "recipe_id": "<id>" }
sap_recipe_show   { "recipe_id": "<id>" }
# → disabled: false, failure_count 초기화 확인
```

### 4.4 체크포인트

- [ ] 2회차까지는 disabled=false 유지
- [ ] 3회차 시점에 자동 disabled 전환 + `disabled_reason` 문자열 존재
- [ ] `sap_recipe_search` 결과에서 disabled 레시피가 **제외**되는지
- [ ] `sap_recipe_enable` 후 실패 카운트 리셋

---

## 5. Phase 18-3 검증 — 레시피 버전닝

**목적:** SAP 패치/레이아웃 변경 후 **기존 레시피를 아카이브**하고 새 버전을 만드는 흐름 검증.

### 5.1 사전 준비

검증용 레시피 저장: `P18-3_version_test`
- 내부적으로 `version: 1` 으로 기록됨
- `recipes/<id>.json` 파일 존재 확인

### 5.2 버전 업

현재 레시피를 v2 로 승격:

```
sap_recipe_version_up { "recipe_id": "<id>", "note": "SAP 2023 SP3 대응" }
```

기대 결과:
- `recipes/_archive/<id>__v1__<timestamp>.json` 생성 (원본 보존)
- `recipes/<id>.json` 의 `version: 2`, `previous_version_archive: "_archive/...v1...json"`
- `failure_count`, `disabled` 등 품질 필드는 초기화됨

확인:

```bash
ls -la recipes/_archive/
```

### 5.3 이력 조회 / 아카이브 로드

```
sap_recipe_history { "recipe_id": "<id>" }
# → [{version:2, created_at:...}, {version:1, archived_at:..., path:"_archive/..."}]
```

특정 구 버전 내용 확인:

```
# Python 직접 호출 (MCP tool이 노출 안 돼 있으면)
python -c "from sap_cli.recipe import RecipeStore; s=RecipeStore(); print(s.load_archive('<id>', version=1).model_dump_json(indent=2))"
```

### 5.4 체크포인트

- [ ] `_archive/` 디렉토리는 **version_up 최초 시점에** 생성 (미리 생성 X)
- [ ] 아카이브 파일명에 `__v1__` + ISO 타임스탬프
- [ ] 본체 레시피의 `version` 값 증가 (1→2→3...)
- [ ] `history` 응답에 최소 2건 (현재 + 직전 아카이브)

---

## 6. Phase 18-4 검증 — 크로스 트랜잭션 체인

**목적:** 한 세션에서 여러 tcode 를 거친 레시피가 `tcode_sequence` 로 저장되고, `sap_recipe_chain_search` 로 검색되는지 확인.

### 6.1 크로스 세션 만들기

```
sap_session_start { "label": "P18-4 SPRO→MM03 체인" }
sap_run { "tcode": "SPRO" }
# ... 최소 스텝 진행
sap_run { "tcode": "MM03" }
sap_set { "field": "RMMG1-MATNR", "value": "<자재>" }
sap_press { "id": "btn[0]" }
sap_session_end { "session_id": "<id>" }
sap_session_approve { "session_id": "<id>", "name": "P18-4_SPRO_MM03_chain" }
```

### 6.2 저장된 레시피 구조 확인

```
sap_recipe_show { "recipe_id": "<id>" }
# → tcode_sequence: ["SPRO", "MM03"]
```

### 6.3 체인 검색

```
sap_recipe_chain_search { "tcodes": ["SPRO", "MM03"] }
# → 위 레시피가 결과에 포함돼야 함

sap_recipe_chain_search { "tcodes": ["MM03"] }
# → 단일 tcode 서브시퀀스 매칭 동작 확인
```

### 6.4 체크포인트

- [ ] `tcode_sequence` 가 실제 방문 순서대로 저장됨 (중복 연속 제거)
- [ ] 2-tcode 체인 검색 OK
- [ ] 부분 서브시퀀스 (`["MM03"]`) 로도 조회 가능
- [ ] 일반 `sap_recipe_search` 결과에는 기존대로 키워드 매칭 유지 (회귀 없음)

---

## 7. 주간·월간 품질 점검 루틴

### 7.1 주간 (매주 금요일 15분)

```
sap_recipe_list
```

다음 지표를 기록한다 (별도 시트 권장):

| 지표 | 측정 방법 | 기준 |
|------|-----------|------|
| 총 레시피 수 | `list` 전체 카운트 | 증가 추세 |
| disabled 비율 | `disabled:true` / 전체 | 20% 이하 유지 |
| 실패율 상위 3개 | failure / total 내림차순 | 원인 분석 대상 |
| 승인 대기 | `sap_recipe_pending` | 7일 이상 방치 금지 |

### 7.2 월간 (매월 말)

- `recipes/_archive/` 디스크 사용량 점검
- v3 이상 레시피 별도 리스트업 → 자주 바뀌는 트랜잭션 징후 (SAP 측 변경 잦음)
- 실패율 50% 근접 (예: 40~49%) 레시피 선별 → 수동 점검

확인 명령:

```bash
du -sh recipes/ recipes/_archive/
ls recipes/_archive/ | wc -l
```

---

## 8. 실무 축적 시나리오 예시

### 8.1 VA01 판매 오더 생성 (반복 업무)

목표: 매일 10건 내외의 VA01 입력을 레시피 1개로 표준화.

루틴:
1. 첫 입력 1건을 `session_start ~ session_end` 로 감싸 레시피 승인 (`VA01_표준입력`)
2. 이후 9건은 **동일 레시피 재실행 + 가변 필드만 `sap_set` 로 주입**
3. 각 실행 후 `sap_recipe_success` 또는 `sap_recipe_failure` 기록

기대 효과: 1주일 내 총 실행 50회 이상 누적 → 실패율 산출이 유의미해짐.

### 8.2 MM03 자재 조회 (단순 조회)

목표: 재무뷰 / 회계뷰 / MRP뷰 각각 레시피 3개 분리.

포인트:
- 뷰 선택 팝업까지 포함하도록 `session_end` 전에 화면 이동 끝내기
- 승인 시 이름에 뷰명 명시 (`MM03_재무뷰`, `MM03_MRP뷰1` 등)

### 8.3 SPRO → MM03 체인 (크로스)

목표: 업무 프로세스 "재무원장 확인 후 자재 조회" 를 하나의 체인 레시피로.

단계:
1. `session_start` → `SPRO` 로 조회 → `MM03` 전환 → `session_end`
2. 승인 후 `sap_recipe_chain_search { "tcodes": ["SPRO", "MM03"] }` 로 검증
3. 실사용 시 체인 검색으로 찾아서 재실행

---

## 9. 트러블슈팅

### 9.1 `recipe_suggestion` 이 안 붙는다

원인 후보:
- core_steps < 3 (세션이 너무 짧음) → `sap_screen` 같은 읽기 스텝만으로는 core 로 카운트되지 않음
- tcode 전환 없이 종료 → `has_tcode=false`
- 이미 `approved=true` 인 세션을 다시 end 호출

확인:

```
sap_session_show { "session_id": "<id>" }
# → candidate 필드와 core_steps 를 직접 확인
```

### 9.2 예기치 않게 disabled 됐다

대개는 의도된 결과지만, 일시적 네트워크 오류로 실패가 튄 경우:

```
sap_recipe_enable { "recipe_id": "<id>" }
# 실패 카운트 리셋됨. 이후 동일 실패가 재발하면 진짜 원인이 있음.
```

### 9.3 `sap_recipe_chain_search` 에서 기대한 레시피가 안 나온다

- `sap_recipe_show` 로 `tcode_sequence` 가 실제로 들어 있는지 확인
- 과거 Phase 18 이전에 저장된 레시피는 `tcode_sequence`가 비어 있을 수 있음 → `sap_recipe_version_up` 으로 재저장 시 채워짐

### 9.4 `_archive/` 가 너무 커진다

월 1회 버전 2 이상 레시피만 남기고 v1 아카이브는 압축/외부 보관:

```bash
cd recipes/_archive
tar czf ../archive_backup_$(date +%Y%m).tar.gz *__v1__*.json
# 검증 후
# rm *__v1__*.json
```

⚠ 삭제 전 반드시 압축 아카이브가 제대로 만들어졌는지 확인 (`tar tzf ...` 로 목록 검증).

---

## 10. 축적 KPI & 롤백 규칙

### 10.1 1개월 축적 KPI (권장)

| 항목 | 목표 |
|------|------|
| 승인된 레시피 수 | 30개 이상 |
| 레시피당 평균 실행 | 5회 이상 |
| 전체 실패율 | 20% 이하 |
| 크로스 체인 레시피 비율 | 전체의 10% 이상 |
| disabled → enable 복구 수 | 2회 이하 (많으면 임계 재조정) |

### 10.2 롤백 규칙

운영 중 레시피가 **업무 장애를 유발**했을 때:

1. 즉시 `sap_recipe_failure` 로 실패 기록 (증거 확보)
2. `sap_recipe_show` 로 버전 확인
3. 직전 버전으로 복구 필요 시:
   ```
   python -c "from sap_cli.recipe import RecipeStore; r=RecipeStore().load_archive('<id>', version=<N-1>); print(r)"
   # 내용 확인 후 수동으로 recipes/<id>.json 덮어쓰기 (백업 먼저)
   ```
4. 발생 원인과 조치를 `recipes/INCIDENT_LOG.md` 에 기록 (필요 시 직접 생성)

### 10.3 사내 검증 완료 판단 기준

본 메뉴얼의 3~6 섹션 체크포인트가 **모두 완료 + KPI 1개월 달성** 시 사내 파일럿 종료 조건 충족.
이후 TASKS.md 의 Phase 15 장기 항목(RAG 검색, 온톨로지 심화) 재개 가능.

---

**부록:** 명령 빠른 참조

| 작업 | MCP 툴 / CLI |
|------|---------------|
| 세션 시작/종료 | `sap_session_start` / `sap_session_end` |
| 자동 제안 후보 조회 | `sap_recipe_pending` |
| 승인 | `sap_session_approve` |
| 성공/실패 기록 | `sap_recipe_success` / `sap_recipe_failure` |
| 비활성 복구 | `sap_recipe_enable` |
| 버전 업 / 이력 | `sap_recipe_version_up` / `sap_recipe_history` |
| 체인 검색 | `sap_recipe_chain_search` |
| CLI 전체 명령 | `sap --help` |
