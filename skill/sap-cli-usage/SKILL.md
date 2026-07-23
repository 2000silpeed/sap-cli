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

### Windows 운영 체크리스트

SAP GUI 자동화는 SAP GUI for Windows가 실행 중인 동일한 Windows 사용자 세션에서
실행해야 한다. PowerShell에서 새 환경을 준비할 때는 다음 순서를 따른다.

```powershell
cd <sap_cli 프로젝트 폴더>
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[mcp,dev]"
sap daemon doctor
sap --help
```

PowerShell이 스크립트 실행을 차단하면 `Set-ExecutionPolicy -Scope CurrentUser
RemoteSigned`를 적용한다. 환경변수는 `$env:SAP_CLI_USE_DAEMON = "1"`처럼 설정하며,
기본 daemon 로그는 `$env:USERPROFILE\.sap_cli\daemon.log`에 기록된다.

**프로젝트 폴더 찾는 법** (경로는 사용자마다 다르므로 박아두지 않는다):

1. **사용자에게 경로를 물어본다** — 가장 확실하다.
2. 이미 설치돼 있으면 위치를 역추적한다.
   ```powershell
   pip show sap-cli        # "Editable project location: ..." 이 프로젝트 폴더
   python -c "import sap_cli, pathlib; print(pathlib.Path(sap_cli.__file__).resolve().parent.parent)"
   ```
3. 그래도 모르면 `name = \"sap-cli\"`를 담은 `pyproject.toml`이 있는 폴더를 찾는다.

가상환경은 보통 그 폴더의 `.venv`다. 이름이 다르면(`venv` 등) 사용자에게 확인한다.

## 메뉴 / "More" 네비게이션 (VL02N 등)

신형 SAP GUI에서 메뉴바가 접혀 **"More"** 버튼으로 뜨는 화면(VL02N 등)의 메뉴
항목은 전부 `GuiMenu`이고 `wnd[0]/mbar/...` 아래에 그대로 있다. "More"가 접혀
있어도 스크립팅에서는 메뉴 트리가 노출된다.

- **누르는 법**: `sap press <menu_id>` 또는 `sap act <menu_id> select`. GuiMenu는
  `Press()`가 아니라 `Select()`로 실행된다(도구가 자동 처리). "More" 버튼 자체를
  누를 필요 없이, 목표 메뉴 **항목(leaf)** 을 바로 Select하면 동작이 실행된다.
  예: `sap press "wnd[0]/mbar/menu[3]/menu[1]"`.
- **항목 id 찾기**: `sap screen`은 최상위 메뉴(`wnd[0]/mbar/menu[i]` + text)를
  보여준다. 하위 항목은 `wnd[0]/mbar/menu[i]/menu[j]` 경로 규칙을 따른다. 최상위
  메뉴를 먼저 Select하면 열리며, 필요한 leaf 경로를 지정해 Select한다.
- 저장·전기 같은 변경 메뉴는 그 결과(상태바/모달)를 반드시 확인한다.

## 기본 원칙

1. `sap connect` 또는 `sap_connect`로 연결한다.
2. 액션 전 `sap screen` / `sap_screen`으로 현재 화면을 읽는다.
3. 필드 ID를 모르면 `sap screen --search` 또는 `sap_screen(fields_only=true)`를 사용한다.
4. 전용 명령이 없으면 `sap scripting-query` / `sap_scripting_query`로 SAP GUI Scripting API 그래프를 조회하고, `sap inspect` / `sap_inspect_element`로 live capability를 본 뒤 `sap act` / `sap_ui_action`으로 실행한다.
5. 조작 후 `sap status` / `sap_get_status`를 확인한다.
6. 상태 type이 `E`면 즉시 중단하고 에러를 보고한다.

## 테이블 행 선택과 매크로 기록

사용자가 “표에서 n번째 행을 선택해”, “이 행을 클릭해”, “선택한 행으로 진행해”라고
말하면 `select_row` 의도로 해석한다. SAP의 행 번호는 **0-based**다. 먼저 `sap screen`
또는 `sap inspect <table_id>`로 실제 테이블/ALV element ID를 확인한 뒤, 실행과 매크로
기록을 한 번에 수행한다.

```powershell
# 세 번째 행(0-based row=2)을 선택하고 현재 녹화 단계로 기록
sap record-step select-row "<table element ID>" --row 2 --desc "대상 행 선택"
```

`select-row`, `table-select`, `table-row`는 같은 의미다. `--row`는 필수이며, 행 순서가
동적으로 바뀌는 업무라면 고정 행 번호 대신 먼저 검색·필터링하거나 선택 후 화면/셀
값을 검증한다. MCP에서는 `sap_macro_record_step(action="select-row",
target="<table ID>", row=2, description="대상 행 선택")`을 사용한다. 일반 UI fallback은
`sap_ui_action(action="select_row", element_id="<table ID>", row=2)`다.

## Claude/Codex/MCP daemon 기본 실행

CLI와 MCP는 기본적으로 daemon 경유를 우선 사용한다. daemon이 없으면 자동 시작을
시도하고, 실패하면 direct dispatch로 fallback한다.

```powershell
sap daemon status
sap daemon stop
sap daemon start
sap daemon restart
sap daemon doctor
```

명령이 계속 실패하거나 다른 프로젝트에서 실행이 안 될 때는 먼저 `sap daemon doctor`
(MCP에서는 `sap_daemon_status` 도구)를 호출한다. 어떤 인터프리터/가상환경(venv)으로
도는지, `sap_cli`·`pywin32`·`mcp`를 import 할 수 있는지, 데몬이 살아있는지, 기동 실패
원인(`~/.sap_cli/daemon.log` tail)을 한 번에 보여준다. 기동 실패의 흔한 원인은 데몬이
`sap_cli`가 설치되지 않은 다른 venv로 스폰되는 경우이며, doctor의 `python_executable`과
`sap_cli_path`로 즉시 확인할 수 있다. `SAP_CLI_DAEMON_START_TIMEOUT`(초)으로 콜드
스타트 대기 예산을 조정한다.

### 첫 `sap_connect`가 60초 매달릴 때 (daemon 한정)

MCP(daemon 경유) 첫 connect만 ~60초 걸리고 두 번째부터 즉시라면, daemon 프로세스의
첫 SAP COM 호출 cold-start다(CLI direct 연결은 즉시라 COM 코드 문제 아님).

- **간단한 우회**: daemon을 한 번 띄우고 `sap connect`를 한 번 해서 warm 시켜 둔다.
  이 비용은 daemon 수명당 한 번뿐이고 이후 MCP는 즉시다.
- **실험적 수정(opt-in)**: MCP env에 `SAP_CLI_COM_WORKER=1`을 넣으면 daemon이 SAP
  COM을 전용 STA 스레드에서 처리해 첫 connect도 빨라진다. Windows에서 검증 후 켠다.
  문제가 생기면 `SAP_CLI_COM_WORKER=0`(또는 제거)으로 즉시 원복한다. daemon 재시작
  필요.

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
