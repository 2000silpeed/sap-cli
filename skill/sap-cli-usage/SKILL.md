---
name: sap-cli-usage
description: SAP CLI 실제 사용법 스킬. sap connect/run/screen/set/fill/vkey/table/tree/macro와 MCP sap_* 도구를 이용해 SAP GUI를 조작한다. 전용 기능이 없을 때 sap_inspect_element와 sap_ui_action으로 SAP GUI Scripting capability를 live 검사/실행한다. 콤보박스, 트리, 매크로, 슬롯/서브에이전트식 운영 질문에 사용.
---

# SAP CLI Usage

이 스킬은 `sap` CLI와 MCP `sap_*` 도구로 SAP GUI를 실제 조작하는 방법을 제공한다.
이슈 해결/재설치/커밋이 아니라 **운영 사용법**에 집중한다.

## 기본 원칙

1. `sap connect` 또는 `sap_connect`로 연결한다.
2. 액션 전 `sap screen` / `sap_screen`으로 현재 화면을 읽는다.
3. 필드 ID를 모르면 `sap screen --search` 또는 `sap_screen(fields_only=true)`를 사용한다.
4. 전용 명령이 없으면 `sap scripting-query` / `sap_scripting_query`로 SAP GUI Scripting API 그래프를 조회하고, `sap inspect` / `sap_inspect_element`로 live capability를 본 뒤 `sap act` / `sap_ui_action`으로 실행한다.
5. 조작 후 `sap status` / `sap_get_status`를 확인한다.
6. 상태 type이 `E`면 즉시 중단하고 에러를 보고한다.

## CLI 핵심 명령

```powershell
sap connect
sap connect --system PR1
sap session
sap run MM03
sap screen
sap screen --fields-only
sap screen --search "MATNR"
sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100"
sap fill MATNR=100-100 WERKS=1000
sap vkey 0
sap status
```

## 범용 Scripting capability fallback

전용 명령이 없거나 read-only처럼 보이는 컴포넌트는 먼저 검사한다.
`sap set`, `sap press`, `sap act` 응답에는 `ontology_preflight`가 자동 포함된다.
이 값에서 live Type/SubType, capability, Scripting API graph 요약, generated
overlay 로드 여부를 확인한다.

```powershell
sap inspect "wnd[0]/usr/cmbLFART"
sap combo-entries "wnd[0]/usr/cmbLFART"
sap scripting-query --type GuiComboBox --intent select --depth 2
sap act "wnd[0]/usr/cmbLFART" set "Standard Delivery"
sap act "wnd[0]/usr/chkSELECT" set true
sap act "wnd[0]/usr/btnSAVE" press
sap act "wnd[0]/usr/ctxtRMMG1-MATNR" search_help
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" read_table --max-rows 20
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" read_cell --row 0 --column MATNR
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" set_cell "100-100" --row 0 --column MATNR
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" click_cell --row 0 --column MATNR
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" doubleclick_cell --row 0 --column MATNR
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" context_menu_item "SORT_ASC" --row 0 --column MATNR
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" select_column --column MATNR
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" clear_selection
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" press_toolbar_button "&SORT_ASC"
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" press_toolbar_context_button "&MB_EXPORT"
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" select_toolbar_menu_item "&XXL"
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" page_down
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" scroll 50
sap act "wnd[0]/usr/cntlEDITOR/shell" append_text "추가 텍스트"
sap act "wnd[0]" maximize
```

MCP:

```python
sap_inspect_element(element_id="wnd[0]/usr/cmbLFART")
sap_combo_entries(element_id="wnd[0]/usr/cmbLFART")
sap_scripting_query(component_type="GuiComboBox", intent="select", depth=2)
sap_ui_action(element_id="wnd[0]/usr/cmbLFART", action="set", value="Standard Delivery")
```

## 공식 SAP GUI Scripting API 그래프 갱신

직접 데이터를 손으로 넣지 않는다. SAP 공식 PDF/HTML을 그래프 빌더에 넣어
`graph.generated.json`을 생성하면 런타임이 seed 그래프와 자동 병합한다.

```powershell
python scripts/build_sap_scripting_graph.py `
  "https://help.sap.com/doc/9215986e54174174854b0af6bb14305a/800.08/en-US/sap_gui_scripting_api.pdf" `
  --out sap_cli/ontology/knowledge/scripting_api/graph.generated.json
```

생성 후 조회:

```powershell
sap scripting-query --type GuiGridView --depth 1
sap scripting-query --type GuiTree --depth 1
sap scripting-query --type GuiComboBox --intent select --depth 2
```

조회 결과의 `runtime.generated_overlay_loaded`가 `true`이고
`runtime.nodes_total`이 1000개 이상이면 전체 overlay가 로드된 것이다. `false`면
`graph.generated.json`이 없거나, 현재 실행 중인 `sap` CLI가 다른 설치 경로를 보고
있는 것이다. 이 경우 프로젝트 루트에서 editable 재설치 후 다시 확인한다.

```powershell
python -m pip install -e .
sap scripting-query --type GuiGridView --depth 1
```

PDF URL 입력에는 로컬 `pdftotext`/poppler가 필요하다. `graph.generated.json`은
공식 문서에서 자동 추출된 overlay이고, 기존 `graph.json`의 curated
intent/action 지도는 그대로 유지된다.

## 콤보박스

콤보박스는 `Text`에 쓰지 말고 SAP GUI Scripting의 `Entries`를 이용해 표시값을 `Key`로 매핑한다.

```powershell
sap get "wnd[0]/usr/cmbLFART"
sap combo-entries "wnd[0]/usr/cmbLFART"
sap set "wnd[0]/usr/cmbLFART" "LF"
sap set "wnd[0]/usr/cmbLFART" "Standard Delivery"
sap act "wnd[0]/usr/cmbLFART" set "Standard Delivery"
```

`sap get` / `sap_inspect_element` 결과의 `entries`에서 `key`/`value`를 확인한다.

## 트리/SPRO

```powershell
sap tree "wnd[0]/shellcont/shell" --depth 1
sap tree-expand "wnd[0]/shellcont/shell" "00001"
sap tree-select "wnd[0]/shellcont/shell" "00005"
sap tree-doubleclick "wnd[0]/shellcont/shell" "00005"
sap tree-press "wnd[0]/shellcont/shell" "00005" "2"
```

전용 명령이 막히면:

```powershell
sap act "wnd[0]/shellcont/shell" select_node --node-key "00005"
sap act "wnd[0]/shellcont/shell" expand_node --node-key "00001"
sap act "wnd[0]/shellcont/shell" press_node --node-key "00005" --column "2"
```

## 매크로

녹화 중에는 실제 값을 입력하고, finish에서 변수화한다.

```powershell
sap record-start "자재조회"
sap record-step run MM03
sap record-step set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100" --desc "자재번호"
sap record-step vkey 0
sap record-finish --name "자재조회" --vars "{\"100-100\": \"조회할 자재번호\"}"
sap macro-list
sap macro-play <macro_id> --vars "{\"matnr\": \"200-200\"}"
```

placeholder를 SAP 필드에 직접 입력하지 않는다: `{{MATNR}}`, `${matnr}` 금지.

## 슬롯/서브에이전트식 운영

```powershell
sap --slot ses0 connect --system PR1
sap --slot ses1 connect --system PR1
sap --slot ses0 run MM03
sap --slot ses1 run VA01
sap slots
```

서브에이전트를 실제로 사용할 때는 사용자 명시 요청이 있을 때만 나눈다.

- operator: `ses0`에서 실행
- verifier: 상태/화면/transcript 검증
- recorder: 반복 작업 매크로화
