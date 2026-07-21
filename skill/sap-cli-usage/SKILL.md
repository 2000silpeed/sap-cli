---
name: sap-cli-usage
description: SAP CLI 실제 사용법 스킬. sap connect/run/screen/set/fill/vkey/table/tree/macro와 MCP sap_* 도구를 이용해 SAP GUI를 조작한다. 전용 기능이 없을 때 sap_inspect_element와 sap_ui_action으로 SAP GUI Scripting capability를 live 검사/실행한다. 콤보박스, 트리, 매크로, 슬롯/서브에이전트식 운영 질문에 사용.
---

# SAP CLI Usage

이 스킬은 `sap` CLI와 MCP `sap_*` 도구로 SAP GUI를 실제 조작하는 방법을 제공한다.
이슈 해결/재설치/커밋이 아니라 **운영 사용법**에 집중한다.

## 환경 준비 (프로젝트 폴더 + 가상환경)

`sap` CLI는 프로젝트의 가상환경에 editable로 설치돼 있다. **어떤 `sap` 명령이나
테스트든 먼저 sap_cli 프로젝트 폴더로 이동해 그 안의 `.venv`를 활성화한 뒤
실행한다.** 활성화하지 않으면 `sap`이 PATH에 없거나 다른 설치본을 쓸 수 있다.

```powershell
cd <sap_cli 프로젝트 폴더>          # pyproject.toml과 sap_cli/ 가 있는 곳
.venv\Scripts\Activate.ps1          # Windows PowerShell (macOS/Linux: source .venv/bin/activate)
sap --help                          # 활성화 확인
```

활성화가 어려우면 venv 실행파일을 직접 부른다: `.venv\Scripts\sap ...`(Windows) /
`.venv/bin/sap ...`(macOS/Linux). 테스트도 반드시 같은 venv에서 돈다:
`.venv\Scripts\pytest tests\ -q`.

**프로젝트 폴더 찾는 법** (경로는 사용자마다 다르므로 박아두지 않는다):

1. **사용자에게 경로를 물어본다** — 가장 확실하다.
2. 이미 설치돼 있으면 위치를 역추적한다.
   ```powershell
   pip show sap-cli        # "Editable project location: ..." 이 프로젝트 폴더
   python -c "import sap_cli, pathlib; print(pathlib.Path(sap_cli.__file__).resolve().parent.parent)"
   ```
3. 그래도 모르면 `name = \"sap-cli\"`를 담은 `pyproject.toml`이 있는 폴더를 찾는다.

가상환경은 보통 그 폴더의 `.venv`다. 이름이 다르면(`venv` 등) 사용자에게 확인한다.

## 기본 원칙

1. `sap connect` 또는 `sap_connect`로 연결한다.
2. 액션 전 `sap screen` / `sap_screen`으로 현재 화면을 읽는다.
3. 필드 ID를 모르면 `sap screen --search` 또는 `sap_screen(fields_only=true)`를 사용한다.
4. 전용 명령이 없으면 `sap scripting-query` / `sap_scripting_query`로 SAP GUI Scripting API 그래프를 조회하고, `sap inspect` / `sap_inspect_element`로 live capability를 본 뒤 `sap act` / `sap_ui_action`으로 실행한다.
5. 조작 후 `sap status` / `sap_get_status`를 확인한다.
6. 상태 type이 `E`면 즉시 중단하고 에러를 보고한다.

## Claude/Codex/MCP daemon 기본 실행

CLI와 MCP는 기본적으로 daemon 경유를 우선 사용한다. daemon이 없으면 자동 시작을
시도하고, 실패하면 direct dispatch로 fallback한다.

```powershell
sap daemon status
sap daemon stop
sap daemon start
```

직접 실행으로 강제하려면 환경변수로 끈다.

```powershell
$env:SAP_CLI_USE_DAEMON = "0"
```

Claude/Codex MCP 설정에서 별도 env를 안 넣어도 daemon 기본값은 켜져 있다. 그래도
명시하고 싶으면 `SAP_CLI_USE_DAEMON=1`, `SAP_CLI_DAEMON_AUTOSTART=1`을 넣는다.

## CLI 핵심 명령

Codex/Claude가 터미널을 사용할 수 있으면 CLI 예시는 **반드시 실제 bash/PowerShell
명령으로 실행**한다. `sap_run(...)` 같은 의사코드로 바꾸지 않는다.

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

## 레시피 RAG 검색

직접 화면을 탐색하기 전에 기존 recipe를 먼저 찾는다. `sap recipe-query`는
BM25 lexical search, lightweight semantic overlap, tcode/step_kind boost,
recipe quality score를 섞어 hybrid reranking한다.

```powershell
sap recipe-query --tcode MM03 --keywords "엑셀 업로드 자재 조회"
sap recipe-query --keywords "shipping point 설정"
```

응답의 `retrieval.match_reasons`, `retrieval.matched_snippets`,
`relevance_score`, `quality_score`를 같이 보고 가장 맞는 recipe를 선택한다.

## CLI/bash 실행을 레시피로 기록

bash에서 직접 실행한 `sap ...` 명령을 재사용 가능한 recipe 후보로 남기려면
작업 시작 전에 CLI 세션을 연다. 이후 실행되는 `sap` CLI 명령은 active session에
자동 기록되며, 각 step에는 재실행용 원문 `cli_invocation`과 `raw_argv`가 함께 저장된다.

```powershell
sap session-start "MM03 자재 조회 성공 경로"
sap connect --system PR1
sap run MM03
sap screen --fields-only
sap set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100"
sap vkey 0
sap status
sap session-end --outcome success --summary "MM03 조회 성공"
```

현재 active session 확인/변경:

```powershell
sap session-active
sap session-use <session_id>
sap session-clear
```

SAP 조작은 아니지만 recipe가 따라야 하는 준비/검증/외부 CLI 단계는
`session-mark`로 명시한다.

```powershell
sap session-mark "pdftotext 설치 여부 확인" --kind external --cli "pdftotext -v" --no-requires-sap
sap session-mark "입력 CSV 생성" --kind bash --cli "python scripts/prepare_input.py" --no-requires-sap
sap session-mark "사용자 승인 확인 후 저장 진행" --kind manual --not-followable --no-requires-sap
```

Excel 조작/검증은 shorthand로 더 간단히 남긴다.

```powershell
sap session-excel "업로드 대상 범위 확인" --action check --workbook input.xlsx --sheet Sheet1 --range A1:D20
sap session-excel "MATNR 목록 붙여넣기" --action paste --workbook input.xlsx --sheet Upload --range A2:A100
```

운영 규칙:
- recipe로 남길 작업은 `sap session-start` → 실제 `sap ...` bash 명령들 → `sap session-end` 순서로 수행한다.
- SAP 외부 단계는 `sap session-mark`로 남겨 `step_kind`, `followable`, `requires_sap`를 명확히 표시한다.
- Excel 단계는 `sap session-excel`로 남기면 `step_kind=excel`, `requires_sap=false`가 자동 지정된다.
- `sap session-end` 응답의 `recipe_suggestion`이 나오면 `sap session-approve <session_id>`로 recipe 저장을 승인한다.
- 이미 외부에서 세션 ID를 관리한다면 `SAP_CLI_SESSION_ID=<id>` 환경변수로도 CLI step 기록이 가능하다.

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
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" call_method "GetCellValue" --args-json "[0,\"MATNR\"]"
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" get_property --property-name RowCount
sap act "wnd[0]/usr/cntlGRID/shellcont/shell" set_property 10 --property-name FirstVisibleRow
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

**녹화 규칙: `record-step`의 값은 반드시 실제 값이어야 한다.**
`${var}`, `{{MATNR}}`, `{var}` 같은 플레이스홀더를 값으로 넘기면 SAP에 그대로 입력되어 에러가 발생한다.
시스템이 이를 감지해 에러를 반환한다. 변수화는 `record-finish` 시 자동으로 처리된다.

```powershell
sap record-start "자재조회"
sap record-step run MM03
sap record-step set "wnd[0]/usr/ctxtRMMG1-MATNR" "100-100" --desc "자재번호"
# ↑ 실제 자재번호 "100-100" 사용. "${matnr}" 같은 플레이스홀더 절대 금지.
sap record-step vkey 0
sap record-finish --name "자재조회"
# ↑ finish 후 "100-100"이 자동으로 ${matnr}로 변수화됨
sap record-finish --name "자재조회" --vars "{\"100-100\": \"조회할 자재번호\"}"
# ↑ --vars는 변수명/설명을 직접 지정하고 싶을 때만 사용
sap macro-list
sap macro-play <macro_id> --vars "{\"matnr\": \"200-200\"}"
```

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
