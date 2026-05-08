---
name: sap
description: SAP GUI 자동화 - MCP 도구로 SAP 트랜잭션 실행, 매크로 녹화/재생, 온톨로지 학습
triggers:
  - sap
  - SAP
  - 트랜잭션
  - tcode
  - 자재
  - 수주
  - 구매오더
  - 판매오더
  - MM03
  - MM01
  - MM02
  - VA01
  - VA02
  - VA03
  - ME21N
  - ME22N
  - MMBE
  - MD04
  - VL01N
  - VF01
  - FB01
  - 매크로
  - 온톨로지
---

# SAP GUI 자동화 Skill

SAP GUI MCP 도구를 사용하여 SAP 트랜잭션을 실행하고, 화면을 탐색하며, 반복 작업을 매크로로 자동화하는 가이드.

## 핵심 원칙

1. **화면 먼저 확인**: 모든 액션 전에 `sap_screen`으로 현재 화면 상태를 파악한다
2. **결과 확인 필수**: 버튼 클릭/Enter/저장 후 반드시 `sap_get_status`로 결과를 확인한다
3. **에러 즉시 중단**: 상태바 type이 "E"면 사용자에게 보고하고 진행을 멈춘다
4. **필드 ID 동적 확인**: 필드 ID를 모르면 `sap_screen(fields_only=true)`로 확인한다
5. **전용 기능 없으면 Scripting API 그래프 사용**: 전용 도구가 없거나 조작법이 애매하면 `sap_scripting_query`로 공식 API 그래프 subgraph를 조회하고, `sap_inspect_element`로 live Type/SubType/capability를 확인한 뒤 `sap_ui_action`으로 실행한다
6. **온톨로지 선행 조회**: 트랜잭션 구조를 모르면 `sap_ontology_query`로 먼저 조회한다
7. **화면 전환 후 재확인**: 버튼/VKey 실행 후 모든 UI 요소 참조가 무효화되므로, 반드시 `sap_screen`으로 새 화면을 다시 읽는다
8. **재시도 전 transcript 검토**: 직전 실패/경고/승인요청이 있으면 `sap_transcript_query`와 `sap_transcript_plan`으로 먼저 검토한다
9. **온톨로지와 transcript를 같이 본다**: 온톨로지는 일반 구조를, transcript는 직전 실행 사실을 보여주므로 둘 중 하나만 믿지 않는다

## Daemon 기본 실행

CLI/MCP는 기본적으로 daemon 경유를 우선 사용한다. daemon이 없으면 자동 시작하고,
실패 시 direct dispatch로 fallback한다. 직접 실행을 강제해야 할 때만
`SAP_CLI_USE_DAEMON=0`을 설정한다. Claude/Codex MCP에서는 별도 설정 없이 daemon
기본값이 켜지며, 명시하려면 `SAP_CLI_USE_DAEMON=1`,
`SAP_CLI_DAEMON_AUTOSTART=1`을 MCP env에 넣는다.

## CLI/bash 실행 기록 원칙

Codex/Claude가 터미널을 사용할 수 있으면 `sap` CLI 예시는 실제 bash/PowerShell
명령으로 실행한다. 레시피 후보로 남길 작업은 다음 순서를 지킨다.

```powershell
sap session-start "작업 목표"
sap connect --system PR1
sap run MM03
sap screen --fields-only
sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100"
sap vkey 0
sap status
sap session-end --outcome success --summary "성공"
```

`sap session-start` 이후의 CLI step은 active session에 자동 기록되고, recipe step에는
`cli_invocation`/`raw_argv`가 보존된다. 현재 active session은 `sap session-active`로
확인하고, 외부 세션 ID를 직접 지정하려면 `SAP_CLI_SESSION_ID=<id>`를 사용한다.

---

## MCP 도구 레퍼런스

### 기본 조작

| 도구 | 용도 | 핵심 파라미터 |
|------|------|--------------|
| `sap_connect` | SAP GUI 연결 (이미 로그인된 상태 필요) | `connection_index`, `session_index` |
| `sap_session` | 세션 정보 조회 (사용자, tcode, 화면번호) | - |
| `sap_screen` | 현재 화면 UI 구조 (JSON) | `fields_only=true`, `actions_only=true` |
| `sap_inspect_element` | 단일 요소의 SAP GUI Scripting capability 검사 | `element_id` |
| `sap_scripting_query` | SAP GUI Scripting API 그래프 subgraph 조회 | `component_type`, `intent`, `node_id` |
| `sap_combo_entries` | GuiComboBox entries(key/value/pos) 직접 조회 | `element_id` |
| `sap_ui_action` | Type/SubType 기반 범용 UI 액션 fallback | `element_id`, `action`, `value` |
| `sap_run` | 트랜잭션 실행 | `tcode` (필수) |
| `sap_set_field` | 필드 값 설정 (1개) | `element_id`, `value` (필수) |
| `sap_fill_fields` | 여러 필드 한번에 입력 (필드명 자동 매핑) | `fields` (필수), `enter` (기본 true) |
| `sap_get_field` | 필드 값 읽기 | `element_id` (필수) |
| `sap_press` | 버튼 클릭 / 탭 선택 (GuiTab 자동 감지) | `element_id` (필수) |
| `sap_send_vkey` | 키보드 키 전송 | `vkey` (필수) |
| `sap_get_status` | 상태바 메시지 조회 | - |
| `sap_table_read` | 테이블/그리드 데이터 읽기 | `element_id` (필수) |
| `sap_modal` | 모달 팝업 존재 여부와 텍스트 | - |
| `sap_transcript_query` | 최근 실행 transcript 조회 | `limit`, `outcome`, `command`, `tool`, `agent`, `text`, `full` |
| `sap_transcript_plan` | 선택한 transcript의 재실행 검토 계획 생성 | `limit`, `index`, `outcome`, `command`, `tool`, `agent`, `text` |

### 온톨로지

| 도구 | 용도 | 핵심 파라미터 |
|------|------|--------------|
| `sap_ontology_query` | 트랜잭션 도메인 지식 조회 | `tcode`, `field_search`, `list_all` |
| `sap_learn` | 현재 화면을 온톨로지로 학습/저장 | - |

### 스크린샷

| 도구 | 용도 | 핵심 파라미터 |
|------|------|--------------|
| `sap_capture` | SAP GUI 화면 스크린샷 캡처 | `label` (선택), `output` (선택) |
| `sap_capture_list` | 저장된 스크린샷 목록 조회 | `tcode` (필터) |

### 매크로

| 도구 | 용도 | 핵심 파라미터 |
|------|------|--------------|
| `sap_macro_control` | 녹화 제어 | `command`: start/finish/cancel/status/undo |
| `sap_macro_record_step` | 녹화 중 단계 기록+실행 | `action`, `target`, `value`, `description` |
| `sap_macro_play` | 매크로 재생 | `macro_id`, `variables`, `dry_run` |
| `sap_macro_list` | 매크로 목록 조회 | `tcode` (필터) |
| `sap_macro_show` | 매크로 상세 조회 | `macro_id` |
| `sap_macro_delete` | 매크로 삭제 | `macro_id` |

---

## VKey 레퍼런스

| VKey | 키 | 용도 |
|------|----|------|
| 0 | Enter | 확인/다음 화면 |
| 3 | F3 (Back) | 이전 화면 |
| 8 | F8 (Execute) | 실행/조회 |
| 11 | Ctrl+S | 저장 |
| 12 | F12 (Cancel) | 취소 |
| 16 | F4 | 검색 도움말 (Search Help) |
| 2 | F2 | 선택/더블클릭 |
| 5 | F5 | 기타 기능 |

---

## 작업 패턴

### 패턴 1: 트랜잭션 실행 기본 흐름

모든 SAP 작업의 기본 순서:

```
1. sap_connect                          # SAP GUI 연결
2. sap_ontology_query(tcode="XX01")     # 도메인 지식 확인 (선택)
3. sap_run(tcode="XX01")               # 트랜잭션 실행
4. sap_screen(fields_only=true)        # 입력 가능한 필드 확인
5. sap_fill_fields(fields={"MATNR": "100-100", "WERKS": "1000"})
   # 여러 필드 한번에 입력 + 자동 Enter (필드명 자동 매핑)
   # 또는 1개씩: sap_set_field(element_id, value) → sap_send_vkey(vkey=0)
6. sap_get_status()                    # 결과 확인
```

### 패턴 2: 화면 탐색 (필드 ID를 모를 때)

```
1. sap_screen()                        # 전체 UI 구조 확인
   → fields: 입력 필드 목록 (id, label, changeable, required)
   → buttons: 버튼 목록 (id, text, tooltip)
   → tabs: 탭 목록
   → tables: 테이블 목록

2. sap_screen(fields_only=true)        # 입력 가능 필드만 필터링
   → changeable=true인 필드만 반환
   → required=true인 필드는 반드시 입력해야 함

3. sap_screen(actions_only=true)       # 가능한 액션만 필터링

4. 전용 도구가 없거나 컴포넌트 조작법이 애매하면:
   sap_scripting_query(component_type="GuiComboBox", intent="select", depth=2)
   sap_inspect_element(element_id="...")  # Type/SubType/capability/entries 확인
   sap_combo_entries(element_id="...")    # 콤보 entries 직접 조회
   sap_ui_action(element_id="...", action="set", value="...")  # live type 기반 실행
   sap_ui_action(element_id="...", action="read_table", max_rows=20)
   sap_ui_action(element_id="...", action="read_cell", row=0, column="MATNR")
   sap_ui_action(element_id="...", action="set_cell", value="100-100", row=0, column="MATNR")
   sap_ui_action(element_id="...", action="click_cell", row=0, column="MATNR")
   sap_ui_action(element_id="...", action="doubleclick_cell", row=0, column="MATNR")
   sap_ui_action(element_id="...", action="context_menu_item", value="SORT_ASC", row=0, column="MATNR")
   sap_ui_action(element_id="...", action="select_column", column="MATNR")
   sap_ui_action(element_id="...", action="clear_selection")
   sap_ui_action(element_id="...", action="press_toolbar_button", value="&SORT_ASC")
   sap_ui_action(element_id="...", action="press_toolbar_context_button", value="&MB_EXPORT")
   sap_ui_action(element_id="...", action="select_toolbar_menu_item", value="&XXL")
   sap_ui_action(element_id="...", action="page_down")
   sap_ui_action(element_id="...", action="scroll", value="50")
   sap_ui_action(element_id="...", action="append_text", value="추가 텍스트")
   sap_ui_action(element_id="wnd[0]", action="maximize")
   sap_ui_action(element_id="...", action="call_method", value="GetCellValue", method_args=[0, "MATNR"])
   sap_ui_action(element_id="...", action="get_property", property_name="RowCount")
   sap_ui_action(element_id="...", action="set_property", value=10, property_name="FirstVisibleRow")
```

`sap_set_field`, `sap_press`, `sap_ui_action` 응답에는 `ontology_preflight`가
자동 포함된다. 여기서 live Type/SubType, capability, API graph 요약,
`runtime.generated_overlay_loaded`를 확인한 뒤 다음 행동을 결정한다.

콤보박스는 표시값을 직접 Text에 쓰지 말고, `sap_inspect_element` 또는
`sap_get_field`로 entries를 확인한 뒤 `sap_ui_action(action="set")` 또는
`sap_set_field`에 key/표시값을 넘긴다. 내부에서 entries의 표시값을 key로 매핑한다.

공식 SAP GUI Scripting API 전체 지도는 수동 입력하지 않는다. 공식 PDF/HTML을
빌더에 넣어 `sap_cli/ontology/knowledge/scripting_api/graph.generated.json`을
생성하면 런타임이 기존 curated `graph.json`과 자동 병합한다.

```powershell
python scripts/build_sap_scripting_graph.py `
  "https://help.sap.com/doc/9215986e54174174854b0af6bb14305a/800.08/en-US/sap_gui_scripting_api.pdf" `
  --out sap_cli/ontology/knowledge/scripting_api/graph.generated.json
```

생성 후 `sap_scripting_query(component_type="GuiGridView", depth=1)`처럼
공식 API 객체/속성/메서드를 조회한다. PDF URL 입력에는 로컬
`pdftotext`/poppler가 필요하다. 조회 결과에서
`runtime.generated_overlay_loaded=true`, `runtime.nodes_total>=1000`이면 전체
overlay가 로드된 것이다. `false`면 `graph.generated.json`이 없거나 현재 실행 중인
`sap` CLI가 다른 설치 경로를 보고 있으므로 프로젝트 루트에서
`python -m pip install -e .`로 editable 재설치 후 확인한다.

### 패턴 3: 저장 흐름

문서 생성/변경 트랜잭션에서 저장할 때:

```
1. sap_send_vkey(vkey=11)              # Ctrl+S (저장)
2. sap_get_status()                    # 상태바 확인
   → type="S": 성공. 메시지에서 문서번호 추출
   → type="E": 에러. 메시지 읽고 사용자에게 보고
   → type="W": 경고. Enter로 확인 가능한 경우 많음
3. sap_screen()                        # 모달 팝업 확인
   → 모달이 있으면 내용 읽고 적절히 응답 (Yes/No 등)
```

저장 성공 시 상태바 메시지 예시: `"Standard Order 12345678 has been saved"`
→ 문서번호를 추출하여 사용자에게 전달

### 패턴 4: 매크로 녹화

반복 작업을 매크로로 저장하는 흐름:

```
1. sap_macro_control(command="start", name="자재조회")
2. sap_macro_record_step(action="run", target="MM03", description="자재조회 실행")
3. sap_macro_record_step(action="set", target="wnd[0]/usr/ctxtRMMG1-MATNR", value="100-100", description="자재번호 입력")
4. sap_macro_record_step(action="vkey", target="0", description="Enter")
5. ...반복...
6. sap_macro_control(command="finish", name="자재조회",
     variables={"MATNR": "조회할 자재번호"})
```

`sap_macro_record_step`의 action 종류:
- `set`: 필드 값 설정 (target=element_id, value=값)
- `press`: 버튼 클릭 또는 탭 선택 (target=element_id, GuiTab이면 자동으로 Select() 호출)
- `vkey`: VKey 전송 (target=vkey번호 문자열)
- `run`: 트랜잭션 실행 (target=tcode)
- `checkbox`: 체크박스 (target=element_id, value="true"/"false")
- `radio`: 라디오 버튼 (target=element_id)
- `combo`: 콤보박스 (target=element_id, value=값)
- `tab`: 탭 선택 (target=element_id)
- `menu`: 메뉴 선택 (target=메뉴 경로)

### 패턴 5: 매크로 재생

```
1. sap_macro_list(tcode="MM03")        # 해당 트랜잭션 매크로 검색
2. sap_macro_play(
     macro_id="...",
     variables={"MATNR": "200-200"}    # 변수 값 변경
   )
```

`dry_run=true`로 실행하면 실제 SAP 실행 없이 단계만 확인 가능.

### 패턴 6: transcript 기반 사후 검토

실행 직후 결과가 애매하거나, 같은 단계를 다시 실행하려면:

```
1. sap_transcript_query(limit=10)                  # 최근 실행 흐름 확인
2. sap_transcript_query(outcome="approval_required")
3. sap_transcript_plan(command="set", index=0)     # 재실행 검토 계획 생성
   → risk_level
   → before/after transaction, screen
   → ontology_context
   → preconditions
   → proposed_sequence
4. 필요한 경우 sap_session / sap_screen / sap_get_status로 실제 화면 재검증
```

권장 사용처:
- verifier agent가 operator의 작업을 사후 검토할 때
- 에러/경고 이후 같은 단계를 반복 실행하기 전에

### 패턴 7: 스크린샷 기반 설명서 제작

단계별 스크린샷을 캡처하여 사용자 설명서 소재를 생성:

```
1. sap_run(tcode="VL03N")
2. sap_capture(label="01_초기화면")               # 초기 화면 캡처
3. sap_fill_fields(fields={"VBELN": "80000123"})  # 필드 입력 + Enter
4. sap_capture(label="02_상세화면")               # 결과 화면 캡처
5. sap_capture_list(tcode="VL03N")                # 캡처 목록 확인
```

저장 위치: `~/.sap_cli/screenshots/{tcode}/`
파일명: `{tcode}_{step}_screen{num}_{label}.png`
- 왜 특정 승인 요청이 발생했는지 설명해야 할 때

이 패턴은 온톨로지를 대체하지 않는다.
- 온톨로지: 원래 이 트랜잭션이 어떤 구조인지 설명
- transcript: 방금 실제로 어떤 화면에서 실패/성공했는지 설명
- planner: 두 정보를 바탕으로 다음 액션을 보수적으로 정리하고, 다시 볼 ontology 힌트까지 준다

### 패턴 7: F4 검색 도움말

필드에서 가능한 값을 검색할 때:

```
1. sap_set_field(element_id, "")       # 대상 필드에 포커스
2. sap_send_vkey(vkey=16)              # F4 (검색 도움말)
3. sap_screen()                        # 팝업 내용 확인
   → 값 목록이 바로 표시되거나
   → 검색 조건 입력 화면이 나타남
4. (검색 조건 입력 → F8 실행)
5. 결과에서 값 선택
```

---

## SAP 트랜잭션 도메인 지식

### MM03 - 자재 조회 (Display Material)

**모듈**: MM (Material Management)

**화면 흐름**:
1. 초기화면 (4000): 자재번호 입력 → Enter
2. 뷰 선택 (4004): 조회할 뷰 체크 → Enter
3. 데이터 화면 (4200~): 기본데이터, 구매, MRP, 회계 등 탭별 조회

**핵심 필드**:
| 필드 ID | SAP 필드 | 설명 |
|---------|---------|------|
| `wnd[0]/usr/ctxtRMMG1-MATNR` | MATNR | 자재번호 (18자리, 선행0 자동추가) |

**데이터 화면 주요 필드**:
| 필드 ID | SAP 필드 | 설명 |
|---------|---------|------|
| `wnd[0]/usr/txtMAKT-MAKTX` | MAKTX | 자재내역 (품명) |
| `wnd[0]/usr/cmbMARA-MEINS` | MEINS | 기본단위 (EA, KG 등) |
| `wnd[0]/usr/txtMARA-MTART` | MTART | 자재유형 (FERT=완제품, ROH=원자재, HALB=반제품) |
| `wnd[0]/usr/txtMARA-MATKL` | MATKL | 자재그룹 |
| `wnd[0]/usr/txtMARA-BRGEW` | BRGEW | 총중량 |
| `wnd[0]/usr/txtMARA-NTGEW` | NTGEW | 순중량 |

**메타엣지**: 자재유형(MTART)에 따라 사용 가능한 탭/뷰가 결정됨

**관련 트랜잭션**: MM01(생성), MM02(변경), MD04(MRP), MMBE(재고), MB52(창고재고)

---

### VA01 - 수주 생성 (Create Sales Order)

**모듈**: SD (Sales & Distribution)

**화면 흐름**:
1. 초기화면 (4001): 조직레벨 입력 → Enter
2. Overview (4011): 헤더 + 품목 입력 → 저장

**초기화면 필드** (모두 필수):
| 필드 ID | SAP 필드 | 설명 | 예시 |
|---------|---------|------|------|
| `wnd[0]/usr/ctxtVBAK-AUART` | AUART | 오더유형 (4자리) | OR=표준, SO=러시, RE=반품 |
| `wnd[0]/usr/ctxtVBAK-VKORG` | VKORG | 판매조직 (4자리) | |
| `wnd[0]/usr/ctxtVBAK-VTWEG` | VTWEG | 유통채널 (2자리) | 10=직판, 20=대리점 |
| `wnd[0]/usr/ctxtVBAK-SPART` | SPART | 제품군 (2자리) | |

**Overview 화면 헤더 필드**:
| 필드 ID | SAP 필드 | 설명 | 필수 |
|---------|---------|------|------|
| `wnd[0]/usr/subSUBSCREEN_HEADER/ctxtKUAGV-KUNNR` | KUNNR | 주문자 (Sold-to) | O |
| `wnd[0]/usr/subSUBSCREEN_HEADER/ctxtKUWEV-KUNNR` | KUNNR_SH | 납품처 (Ship-to) | 자동결정 |
| `wnd[0]/usr/subSUBSCREEN_HEADER/ctxtRV45A-KETDAT` | KETDAT | 요청 납품일 | O |
| `wnd[0]/usr/subSUBSCREEN_HEADER/ctxtVBKD-BSTNK` | BSTNK | 고객 PO번호 | X |

**품목 테이블**: `CTRL_U_ERF_AUFTRAG`
- 컬럼: Material(MATNR), Order Quantity(KWMENG), SU(VRKME), Net Value(NETWR), Currency(WAERK), Plant(WERKS), Storage Location(LGORT), Item Category(PSTYV)

**탭**: All Items, Item Overview, Item Detail, Ordering Party

**메타엣지**:
- 오더유형(AUART) → 화면 필드 구성, 가격결정 방식 결정
- 판매조직(VKORG) → 가격결정 절차 결정
- 주문자(KUNNR) 입력 시 → 납품처(Ship-to) 자동 결정

**관련 트랜잭션**: VA02(변경), VA03(조회), VL01N(납품생성), VL02N(납품변경), VF01(청구생성), VF02(청구변경)

---

### ME21N - 구매오더 생성 (Create Purchase Order)

**모듈**: MM (Material Management)

ME21N은 Enjoy 트랜잭션으로 화면 구조가 다른 트랜잭션과 다름. 온톨로지가 아직 학습되지 않았을 수 있으므로 반드시 `sap_screen`으로 실제 화면을 확인할 것.

**일반적인 흐름**:
1. `sap_run(tcode="ME21N")`
2. 헤더: 공급업체, 구매조직, 구매그룹, 회사코드
3. 품목: 자재, 수량, 납품일, 플랜트
4. 저장: VKey 11

**관련 트랜잭션**: ME22N(변경), ME23N(조회), ME2M(구매오더 목록)

---

## 에러 처리

### 상태바 메시지 타입

| 타입 | 의미 | 대응 |
|------|------|------|
| S (Success) | 성공 | 문서번호 등 정보 추출 후 사용자에게 전달 |
| W (Warning) | 경고 | 메시지 내용 확인. 대부분 Enter(VKey 0)로 진행 가능 |
| E (Error) | 에러 | **즉시 중단**. 메시지 읽고 사용자에게 보고. 수정 방법 제안 |
| I (Information) | 정보 | 내용 확인 후 Enter로 진행 |
| A (Abort) | 중단 | 심각한 오류. 트랜잭션 재시작 필요할 수 있음 |

### 일반적인 에러 상황

- **필수 필드 미입력**: "Fill in all required entry fields" → `sap_screen(fields_only=true)`로 required=true 필드 확인
- **권한 없음**: "No authorization" → 사용자에게 권한 요청 안내
- **자재/고객 없음**: "Material XXX does not exist" → 번호 확인 요청
- **잠금 충돌**: "is locked by user" → 다른 사용자가 편집 중, 나중에 재시도

### 모달 팝업 처리

화면 전환 후 모달이 나타나면:
1. `sap_screen()`으로 모달 내용 확인 (wnd[1] 레벨)
2. 모달 텍스트를 읽고 판단
3. 확인(Yes): 해당 버튼 press 또는 Enter
4. 취소(No): Cancel 버튼 또는 VKey 12
5. "불완전 데이터" 경고: 에러 필드를 확인하고 수정

---

## 주의사항

1. **화면 전환 시 참조 무효화**: 버튼 Press, VKey 전송, 트랜잭션 실행 후에는 이전에 파악한 element_id가 유효하지 않을 수 있다. 반드시 `sap_screen`으로 다시 확인한다.

2. **SAP 서버 응답 대기**: SAP 서버와 통신 중 수초간 블로킹될 수 있다. 이는 정상적인 동작이다.

3. **트랜잭션별 화면 차이**: 같은 트랜잭션이라도 시스템 설정, 사용자 설정, 입력 데이터에 따라 화면 구조가 달라질 수 있다. 항상 `sap_screen`으로 실제 화면을 확인한다.

4. **Enjoy 트랜잭션**: ME21N, MIRO 등 Enjoy 트랜잭션은 화면 구조가 복잡하고 일반 트랜잭션과 다르다. 온톨로지에 없으면 `sap_learn`으로 먼저 학습하는 것이 좋다.

5. **테이블 입력**: 품목 테이블에 데이터를 입력할 때는 셀 단위로 `sap_set_field`를 사용한다. 테이블 element_id는 `sap_screen`에서 확인한다.

6. **날짜 형식**: SAP 시스템 설정에 따라 DD.MM.YYYY, MM/DD/YYYY, YYYY-MM-DD 등 형식이 다르다. `sap_session`에서 date format을 확인하거나, YYYYMMDD 형식을 사용하면 대부분 호환된다.

7. **연결 상태**: MCP 서버는 한 번 연결하면 세션을 유지한다. 연결이 끊어지면 `sap_connect`로 재연결한다.

8. **역할 분리**: operator는 실행하고 verifier는 `sap_transcript_query`, `sap_transcript_plan`, `sap_screen`, `sap_get_status` 같은 read-only surface로 검토하는 편이 안전하다.

---

## 실전 예시

### 예시 1: 자재 기본 데이터 조회

```
사용자: "자재 100-100의 기본 데이터를 조회해줘"

1. sap_connect()
2. sap_run(tcode="MM03")
3. sap_set_field("wnd[0]/usr/ctxtRMMG1-MATNR", "100-100")
4. sap_send_vkey(vkey=0)                    # Enter → 뷰 선택
5. sap_screen()                             # 뷰 선택 화면 확인
6. sap_send_vkey(vkey=0)                    # 기본 뷰 선택 후 Enter
7. sap_screen()                             # 데이터 화면
8. sap_get_field("wnd[0]/usr/txtMAKT-MAKTX")  # 자재내역 읽기
9. sap_get_field("wnd[0]/usr/cmbMARA-MEINS")  # 기본단위 읽기
   ... 필요한 필드 값 읽기 ...
```

### 예시 2: 수주 생성

```
사용자: "표준수주를 생성해줘. 판매조직 1000, 유통채널 10, 제품군 00"

1. sap_connect()
2. sap_run(tcode="VA01")
3. sap_set_field("wnd[0]/usr/ctxtVBAK-AUART", "OR")
4. sap_set_field("wnd[0]/usr/ctxtVBAK-VKORG", "1000")
5. sap_set_field("wnd[0]/usr/ctxtVBAK-VTWEG", "10")
6. sap_set_field("wnd[0]/usr/ctxtVBAK-SPART", "00")
7. sap_send_vkey(vkey=0)                    # Enter → Overview
8. sap_get_status()                         # 에러 확인
9. sap_screen(fields_only=true)             # Overview 필드 확인
10. sap_set_field("wnd[0]/usr/subSUBSCREEN_HEADER/ctxtKUAGV-KUNNR", "1000")  # 주문자
11. sap_set_field("wnd[0]/usr/subSUBSCREEN_HEADER/ctxtRV45A-KETDAT", "20260401")  # 납품일
12. ... 품목 입력 ...
13. sap_send_vkey(vkey=11)                  # 저장
14. sap_get_status()                        # "Standard Order XXXXXXXX has been saved"
```

### 예시 3: 매크로 녹화 후 재생

```
# 녹화
1. sap_macro_control(command="start", name="자재조회_매크로")
2. sap_macro_record_step(action="run", target="MM03", description="MM03 실행")
3. sap_macro_record_step(action="set", target="wnd[0]/usr/ctxtRMMG1-MATNR", value="{{MATNR}}", description="자재번호 입력")
4. sap_macro_record_step(action="vkey", target="0", description="Enter")
5. sap_macro_record_step(action="vkey", target="0", description="뷰 선택 확인")
6. sap_macro_control(command="finish", name="자재조회_매크로", variables={"MATNR": "조회할 자재번호"})

# 재생
7. sap_macro_list(tcode="MM03")
8. sap_macro_play(macro_id="...", variables={"MATNR": "200-200"})
```
