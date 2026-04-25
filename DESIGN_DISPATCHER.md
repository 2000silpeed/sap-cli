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

## D-2 진행 메모

- `recipe_failure`, `recipe_enable`, `recipe_history`, `recipe_chain` 구현을 `sap_cli/dispatchers/recipe.py`로 이동했다.
- `sap_cli.dispatcher`에는 기존 private import/monkeypatch 호환을 위한 얇은 wrapper를 남겼다.
- wrapper는 dispatcher module의 `_get_recipe_store`를 implementation에 주입하므로 기존 `sap_cli.dispatcher._get_recipe_store` monkeypatch가 계속 동작한다.
- 추가로 `recipe_search`, `recipe_show`, `recipe_list`, `recipe_delete`, `recipe_success` 구현을 `sap_cli/dispatchers/recipe.py`로 이동했다.
- `recipe_delete` wrapper는 dispatcher module의 `_delete_recipe_with_links`를 implementation에 주입하므로 helper monkeypatch 호환성을 유지한다.
- 현재 남은 큰 recipe 구현은 `recipe_save`, `recipe_version_up`, `recipe_query`, `recipe_pending` 및 ontology-link helper 계열이다. helper 이동은 monkeypatch 영향이 커서 별도 작은 단계로 진행한다.
