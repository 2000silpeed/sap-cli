# Dispatcher 분리 설계

작성일: 2026-04-26

이 문서는 Phase D-1의 설계 산출물이다. 목표는 `sap_cli/dispatcher.py`를 한 번에 크게 갈아엎지 않고, 외부 API와 테스트 표면을 유지한 상태에서 기능별 dispatcher 모듈로 점진 분리하는 것이다.

## 현재 역할 경계

`sap_cli/dispatcher.py`는 현재 다음 책임을 동시에 가진다.

- command registry / MCP tool mapping
- authorization / approval / audit
- transcript / session step logging
- post-action capture
- SAP 기본 action dispatch
- macro dispatch
- ontology / process dispatch
- recipe dispatch
- session lifecycle dispatch
- slot dispatch

따라서 기능 추가 시 registry, policy, MCP, CLI drift를 조심해야 하고, recipe처럼 빠르게 커지는 영역도 중앙 파일을 계속 수정해야 한다.

## 유지해야 하는 외부 API

아래 import surface는 리팩토링 중에도 유지한다.

```python
from sap_cli.dispatcher import (
    ApprovalRequiredError,
    COMMAND_REGISTRY,
    COMMAND_TOOL_MAP,
    DispatchContext,
    MCP_TOOL_TO_COMMAND,
    PermissionDeniedError,
    dispatch_command,
    resolve_mcp_call,
    _resolve_agent_name,
)
```

`sap_cli/cli.py`, `sap_cli/mcp_server.py`, `sap_cli/daemon.py`, 기존 테스트는 위 API가 깨지지 않는다는 전제로 유지한다.

## 목표 구조

```text
sap_cli/
  dispatcher.py                 # 하위 호환 facade
  dispatchers/
    __init__.py                 # public re-export
    registry.py                 # CommandDef, COMMAND_REGISTRY, MCP mapping
    core.py                     # dispatch_command, resolve_mcp_call
    auth.py                     # authorization/audit helper
    transcript.py               # transcript/session logging helper
    post_action.py              # post-action capture helper
    sap_actions.py              # connect/screen/run/set/get/table/tree/fill 등
    macro.py                    # macro record/play/list/show/delete
    ontology.py                 # ontology query/ask/relationships/dependencies/document flow
    process.py                  # process list/show/create/delete/start/step/status/path
    recipe.py                   # recipe save/search/show/list/delete/quality/version/query
    session.py                  # session start/end/list/show/candidates/approve
    slots.py                    # slots/allocate/release
```

## 단계별 분리 순서

1. **registry 분리**
   - `CommandDef`, `COMMAND_REGISTRY`, `COMMAND_TOOL_MAP`, `MCP_TOOL_TO_COMMAND`를 `dispatchers/registry.py`로 이동한다.
   - `dispatcher.py`는 같은 이름을 re-export한다.
   - drift 테스트(`test_command_surface_consistency.py`)가 그대로 통과해야 한다.

2. **가장 독립적인 feature부터 분리**
   - recipe → session → slots → macro → ontology/process → SAP actions 순서로 진행한다.
   - 각 단계는 “함수 이동 + import 조정 + 전체 테스트” 단위로만 커밋한다.

3. **core는 마지막에 축소**
   - `dispatch_command()`의 authorization/transcript/post-action 흐름은 가장 결합도가 높으므로 초반에 쪼개지 않는다.
   - feature dispatcher가 안정화된 뒤 `auth.py`, `transcript.py`, `post_action.py`로 helper를 분리한다.

## command routing 규칙

- command key는 계속 `COMMAND_REGISTRY`의 key를 기준으로 한다.
- MCP tool name은 `CommandDef.tool_name`에서만 파생한다.
- `CommandDef.permission`과 `auth.policy.TOOL_PERMISSION_MAP`은 계속 일치해야 한다.
- CLI 노출 여부는 `CommandDef.expose_cli`가 단일 기준이다.
- MCP 노출 여부는 `CommandDef.expose_mcp`가 단일 기준이다.
- 새 command 추가 시 아래 항목을 반드시 같이 갱신한다.
  - `COMMAND_REGISTRY`
  - `TOOL_PERMISSION_MAP`
  - MCP tool definition 또는 의도적 비노출 allowlist
  - CLI command 또는 MCP-only allowlist
  - README/작업표

## feature dispatcher 함수 시그니처 규칙

분리된 feature dispatcher는 기존 테스트와 call site를 최소 변경하기 위해 당분간 기존 시그니처를 유지한다.

```python
def dispatch_recipe_query(args: dict[str, Any]) -> dict[str, Any]: ...
def dispatch_session_start(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]: ...
def dispatch_slot_allocate(args: dict[str, Any], ctx: DispatchContext) -> dict[str, Any]: ...
```

초기 이동 단계에서는 기존 private 이름(`_dispatch_recipe_query`)을 facade에서 alias로 유지할 수 있다. 내부 모듈에서는 public 이름을 우선 사용하고, 호환이 필요한 동안만 `_dispatch_*` alias를 둔다.

## 금지 사항

- MCP `TOOL_DEFINITIONS` 자동 생성은 Phase D에서 진행하지 않는다.
- registry/policy/MCP/CLI drift 테스트를 약화하지 않는다.
- `read_table` 기본 `max_rows=50`은 건드리지 않는다.
- recipe version-up CLI UX는 이 단계에서 확장하지 않는다.
- 여러 기능군을 한 커밋에서 동시에 이동하지 않는다.

## 검증 기준

각 분리 커밋은 최소 아래를 만족해야 한다.

```bash
uv run pytest
```

추가로 registry 관련 분리에서는 다음 테스트가 핵심 방어선이다.

```bash
uv run pytest tests/test_command_surface_consistency.py
```

## D-2 착수 전 체크리스트

- [x] `dispatchers/` 패키지 생성
- [x] registry 분리 또는 recipe 분리 중 하나만 선택
- [x] `sap_cli.dispatcher` import 호환성 유지
- [x] recipe 관련 테스트 범위 먼저 실행
- [x] 전체 테스트 실행

## D-3 registry 분리 진행 메모

- `CommandDef`, `COMMAND_REGISTRY`, `COMMAND_TOOL_MAP`, `MCP_TOOL_TO_COMMAND`를 `sap_cli/dispatchers/registry.py`로 이동했다.
- `sap_cli.dispatcher`는 같은 이름을 re-export해 기존 외부 import surface를 유지한다.
- `sap_cli/dispatchers/__init__.py`에서도 registry public surface를 re-export한다.
- MCP `TOOL_DEFINITIONS` 자동 생성은 설계대로 진행하지 않았다.
- registry drift 핵심 테스트 통과 확인: `tests/test_command_surface_consistency.py`.

## D-4 session 분리 진행 메모

- session lifecycle dispatch 및 session step logging 구현을 `sap_cli/dispatchers/session.py`로 이동했다.
- `sap_cli.dispatcher`에는 `_dispatch_session_*`, `_log_session_step` wrapper를 유지해 기존 private import surface를 보존한다.
- session dispatcher는 session store, recipe store, ontology link, SAP wrapper 의존성을 facade에서 주입받으므로 기존 monkeypatch 기반 테스트 호환성을 유지한다.
- session → recipe suggestion 회귀 테스트 포함 확인: `tests/test_recipe_autosuggest.py`, `tests/test_session*.py`.

## D-5 process 분리 진행 메모

- D-5의 첫 작은 단계로 process dispatch를 ontology dispatch보다 먼저 분리했다.
- `process_list/show/create/delete/start/step/status/path` 구현을 `sap_cli/dispatchers/process.py`로 이동했다.
- `sap_cli.dispatcher`에는 `_dispatch_process_*` wrapper를 유지해 기존 private import surface를 보존한다.
- process dispatcher는 `get_store` 의존성을 facade에서 주입받으므로 기존 `sap_cli.dispatcher._get_store` monkeypatch 호환성을 유지한다.
- `sap_cli/dispatchers/__init__.py`에서도 process command public surface를 re-export한다.
- 이어서 `ontology_query/ask/relationships/field_dependencies/document_flow/rebuild/annotate` 구현을 `sap_cli/dispatchers/ontology.py`로 이동했다.
- `sap_cli.dispatcher`에는 `_dispatch_ontology_*`, `_dispatch_field_dependencies`, `_dispatch_document_flow`, `_dispatch_annotate*`, `_build_chain` wrapper를 유지해 기존 private import surface를 보존한다.
- ontology dispatcher는 `get_store`, `get_recipe_store` 의존성을 facade에서 주입받으므로 기존 `sap_cli.dispatcher._get_store`/`_get_recipe_store` monkeypatch 호환성을 유지한다.
- `sap_cli/dispatchers/__init__.py`에서도 ontology command public surface를 re-export한다.

## D-6 macro 분리 진행 메모

- `macro_control/play/list/show/delete` 구현과 `_utc_to_local` helper를 `sap_cli/dispatchers/macro.py`로 이동했다.
- `sap_cli.dispatcher`에는 `_dispatch_macro_*`, `_utc_to_local` wrapper를 유지해 기존 private import surface를 보존한다.
- 중앙 `_execute_command`는 `is_macro_command()`와 `dispatch_macro_command()`를 통해 macro 명령군을 라우팅한다.
- macro dispatcher는 recorder, macro store, SAP wrapper, post-action hook 의존성을 facade에서 주입받으므로 기존 monkeypatch 기반 테스트 호환성을 유지한다.
- `sap_cli/dispatchers/__init__.py`에서도 macro command public surface를 re-export한다.

## D-7 slot 분리 진행 메모

- `slots/slot_allocate/slot_release` 구현을 `sap_cli/dispatchers/slot.py`로 이동했다.
- `sap_cli.dispatcher`에는 `_dispatch_slots`, `_dispatch_slot_allocate`, `_dispatch_slot_release` wrapper를 유지해 기존 private import surface를 보존한다.
- 중앙 `_execute_command`는 `is_slot_command()`와 `dispatch_slot_command()`를 통해 slot 명령군을 라우팅한다.
- slot dispatcher는 pool factory와 SAP wrapper factory를 facade에서 주입받으므로 기존 monkeypatch 기반 테스트 호환성을 유지한다.
- `sap_cli/dispatchers/__init__.py`에서도 slot command public surface를 re-export한다.
- `dispatcher.py`는 1552줄에서 1504줄로 감소했다.

## D-8 transcript 분리 진행 메모

- `transcript_query/transcript_plan` 구현과 `_make_ontology_store` helper를 `sap_cli/dispatchers/transcript.py`로 이동했다.
- `sap_cli.dispatcher`에는 `_dispatch_transcript_query`, `_dispatch_transcript_plan`, `_make_ontology_store` wrapper를 유지해 기존 private import surface를 보존한다.
- 중앙 `_execute_command`는 `is_transcript_command()`와 `dispatch_transcript_command()`를 통해 transcript 명령군을 라우팅한다.
- `sap_cli/dispatchers/__init__.py`에서도 transcript command public surface를 re-export한다.
- `dispatcher.py`는 1504줄에서 1483줄로 감소했다.

## D-2 진행 메모

- `recipe_failure`, `recipe_enable`, `recipe_history`, `recipe_chain` 구현을 `sap_cli/dispatchers/recipe.py`로 이동했다.
- `sap_cli.dispatcher`에는 기존 private import/monkeypatch 호환을 위한 얇은 wrapper를 남겼다.
- wrapper는 dispatcher module의 `_get_recipe_store`를 implementation에 주입하므로 기존 `sap_cli.dispatcher._get_recipe_store` monkeypatch가 계속 동작한다.
- 추가로 `recipe_search`, `recipe_show`, `recipe_list`, `recipe_delete`, `recipe_success` 구현을 `sap_cli/dispatchers/recipe.py`로 이동했다.
- `recipe_delete` wrapper는 dispatcher module의 `_delete_recipe_with_links`를 implementation에 주입하므로 helper monkeypatch 호환성을 유지한다.
- 추가로 `recipe_query`, `recipe_pending` 구현을 `sap_cli/dispatchers/recipe.py`로 이동했다.
- `recipe_query` wrapper는 dispatcher module의 `_get_recipe_store`를, `recipe_pending` wrapper는 `_get_session_store`를 implementation에 주입하므로 기존 monkeypatch 테스트 호환성을 유지한다.
- 추가로 `recipe_version_up` 구현을 `sap_cli/dispatchers/recipe.py`로 이동했다.
- `recipe_version_up` wrapper는 dispatcher module의 `_get_recipe_store`, `_unlink_recipe_from_ontology`, `_link_recipe_to_ontology`를 implementation에 주입하므로 기존 helper monkeypatch 호환성을 유지한다.
- 추가로 `recipe_save` 구현과 recipe helper 계열(`_get_recipe_store`, `_collect_recipe_hints`, `_extract_recipe_tcodes`, `_unlink_recipe_from_ontology`, `_delete_recipe_with_links`, `_link_recipe_to_ontology`)의 실제 구현을 `sap_cli/dispatchers/recipe.py`로 이동했다.
- `sap_cli.dispatcher`에는 기존 private import/monkeypatch 호환을 위한 facade wrapper만 남겼다. wrapper가 dispatcher module의 helper 이름을 implementation에 주입하므로 기존 monkeypatch 테스트 호환성을 유지한다.
- 이 시점에서 recipe 기능 수정 시 우선 `sap_cli/dispatchers/recipe.py`를 보면 되는 상태가 되었고, `dispatcher.py`는 recipe facade/호환 wrapper와 중앙 라우팅만 담당한다.
