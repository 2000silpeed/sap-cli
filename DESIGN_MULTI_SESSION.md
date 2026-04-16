# Phase 17: 병렬 멀티 세션 — 여러 에이전트가 각각의 SAP 세션 운영

> 작성일: 2026-04-16

## 1. 배경과 목적

### 현재 상태

```
MCP Agent A ──┐
MCP Agent B ──┤──→ daemon(TCP 18471) ──→ SapGuiWrapper (싱글턴) ──→ ses[0]
CLI          ──┘
```

- `service.py`의 `_sap` 싱글턴 → 프로세스당 SapGuiWrapper 1개
- `connect()`가 `_last_connection_index`/`_last_session_index` 1벌만 추적
- 데몬이 모든 요청을 직렬 처리 (`socketserver.TCPServer`)
- **결과:** 에이전트 A가 MM03 화면에서 작업 중에 에이전트 B가 VA01을 실행하면 같은 세션에서 충돌

### 목표 상태

```
MCP Agent A (slot="ses0") ──→ daemon ──→ SessionPool["ses0"] ──→ con[0]/ses[0] (MM03)
MCP Agent B (slot="ses1") ──→ daemon ──→ SessionPool["ses1"] ──→ con[0]/ses[1] (VA01)
CLI         (slot="ses0") ──→ daemon ──→ SessionPool["ses0"] ──→ con[0]/ses[0]
```

- SAP GUI는 연결당 최대 6세션까지 지원 (`/o` 명령으로 세션 추가)
- 각 에이전트가 고유 slot에 바인딩되어 독립적으로 작업
- slot을 지정하지 않으면 기본 `ses0`으로 동작 (하위 호환)

## 2. SAP GUI 세션 구조 이해

```
SAP GUI Application (ROT)
├── Connection[0] (PR1 시스템)
│   ├── Session[0] ← 에이전트 A (MM03 작업)
│   ├── Session[1] ← 에이전트 B (VA01 작업)
│   └── Session[2] ← 에이전트 C (VL03N 조회)
└── Connection[1] (ERP 시스템)
    ├── Session[0] ← 에이전트 D
    └── Session[1] ← (미사용)
```

- **Connection** = 시스템(SID) 1개에 대한 로그인
- **Session** = 같은 시스템 안의 독립 작업 창 (각각 다른 트랜잭션 실행 가능)
- SAP 기본 설정: 연결당 최대 6세션 (sm04에서 확인 가능)
- 세션 추가: `/o` 명령 또는 `connection.Sessions.CreateSession()` COM API

## 3. 핵심 설계

### 3.1 SessionSlot — 세션 식별 단위

```python
@dataclass
class SessionSlot:
    """에이전트에 할당된 SAP 세션 슬롯."""
    slot_id: str              # "ses0", "ses1", ...
    connection_index: int     # Connection 인덱스
    session_index: int        # Session 인덱스
    system_id: str | None     # SID (PR1, ERP 등)
    wrapper: SapGuiWrapper    # 이 슬롯 전용 wrapper 인스턴스
    assigned_agent: str | None  # 할당된 에이전트 이름
    created_at: str           # ISO 타임스탬프
```

### 3.2 SessionPool — 세션 풀 관리자

```python
class SessionPool:
    """여러 SapGuiWrapper 인스턴스를 slot_id로 관리."""

    def __init__(self, max_sessions: int = 6):
        self._slots: dict[str, SessionSlot] = {}
        self._max_sessions = max_sessions
        self._lock = threading.Lock()

    def get_or_create(self, slot_id: str = "ses0", ...) -> SapGuiWrapper:
        """slot_id에 해당하는 wrapper를 반환. 없으면 새 세션 생성."""

    def release(self, slot_id: str) -> None:
        """슬롯 해제 (세션은 유지, 재사용 가능)."""

    def list_slots(self) -> list[dict]:
        """활성 슬롯 목록."""

    def close_slot(self, slot_id: str) -> None:
        """세션 종료 + 슬롯 제거."""
```

**핵심 원칙:**
- 각 slot이 독립적인 `SapGuiWrapper` 인스턴스를 소유
- 같은 `SapGuiWrapper` 내부는 `_session` 1개만 추적 (현재 구조 유지)
- pool이 `_application` 객체는 공유, `_connection`/`_session`은 slot별 분리

### 3.3 SapGuiWrapper 변경 (최소)

```python
class SapGuiWrapper:
    def connect(
        self,
        connection_index: int = 0,
        session_index: int = 0,
        system: str | None = None,
    ) -> SapSession:
        # 기존과 동일 — 인스턴스가 여러 개 생기는 것만으로 병렬화 달성
        ...

    def create_session(self) -> int:
        """현재 연결에 새 세션을 생성하고 세션 인덱스를 반환."""
        if self._connection is None:
            raise SapGuiError("연결되지 않았습니다")
        before = self._connection.Sessions.Count
        self._connection.Sessions(0).CreateSession()
        # SAP가 세션 생성에 시간이 걸릴 수 있음
        import time
        for _ in range(20):
            time.sleep(0.3)
            if self._connection.Sessions.Count > before:
                return self._connection.Sessions.Count - 1
        raise SapGuiError("새 세션 생성 실패 (최대 세션 수 초과 가능)")
```

### 3.4 서비스 레이어 변경

```python
# service.py — 싱글턴 → 풀 전환

_pool: SessionPool | None = None

def get_pool() -> SessionPool:
    global _pool
    if _pool is None:
        _pool = SessionPool()
    return _pool

def get_sap(slot_id: str = "ses0") -> SapGuiWrapper:
    """slot_id에 해당하는 wrapper 반환. 하위 호환: 기본 ses0."""
    return get_pool().get_or_create(slot_id)
```

### 3.5 DispatchContext 확장

```python
@dataclass(slots=True)
class DispatchContext:
    source: str = "cli"
    tool_name: str = ""
    agent_name: str = ""
    include_screen_summary: bool = False
    slot_id: str = "ses0"        # ← 추가
```

### 3.6 Daemon 변경

```python
class _DaemonServer(socketserver.ThreadingTCPServer):  # TCPServer → ThreadingTCPServer
    """멀티스레드 데몬 — 여러 에이전트 요청을 병렬 처리."""

    def __init__(self):
        super().__init__(("127.0.0.1", PORT), _Handler)
        self._pool = SessionPool()

    def dispatch(self, cmd, args, ctx_dict=None):
        slot_id = (ctx_dict or {}).get("slot_id", "ses0")
        ctx = DispatchContext(source="daemon", slot_id=slot_id)
        ...
```

**ThreadingTCPServer 주의사항:**
- COM 객체는 생성한 스레드에서만 호출 가능 (STA 모델)
- 해결: `pythoncom.CoInitialize()` 를 각 스레드에서 호출
- 또는 요청을 메인 스레드 큐에 위임 (안전하지만 직렬화)
- **결정:** 스레드별 `CoInitialize` + slot당 독립 COM 접근이 현실적

## 4. 프로토콜 변경

### 4.1 MCP 도구 — slot_id 파라미터 추가

모든 SAP 도구에 선택적 `slot_id` 파라미터 추가:

```json
{
  "name": "sap_run",
  "inputSchema": {
    "properties": {
      "tcode": {"type": "string"},
      "slot_id": {"type": "string", "default": "ses0",
                  "description": "SAP 세션 슬롯 ID. 병렬 작업 시 각 에이전트별 고유 슬롯 사용."}
    }
  }
}
```

### 4.2 새 MCP 도구

```
sap_session_pool      — 활성 슬롯 목록 조회
sap_session_allocate  — 새 세션 슬롯 할당 (에이전트용)
sap_session_release   — 슬롯 해제
```

### 4.3 CLI 명령

```bash
sap slots                    # 활성 슬롯 목록
sap connect --slot ses1      # ses1 슬롯에 연결
sap run MM03 --slot ses1     # ses1 슬롯에서 트랜잭션 실행
```

### 4.4 daemon TCP 프로토콜

```json
{
  "cmd": "run",
  "args": {"tcode": "MM03"},
  "context": {
    "source": "mcp",
    "slot_id": "ses1",
    "agent_name": "operator"
  }
}
```

## 5. COM 스레딩 모델 대응

SAP GUI COM은 STA(Single-Threaded Apartment) 모델:

### 방안 A: 스레드별 CoInitialize (채택)

```python
import pythoncom

class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        pythoncom.CoInitialize()
        try:
            # ... 요청 처리 ...
        finally:
            pythoncom.CoUninitialize()
```

- 장점: 진정한 병렬 실행
- 단점: COM 마샬링 오버헤드, STA 위반 시 크래시 가능
- **검증 필요:** SAP GUI COM이 MTA에서도 안전한지 실제 테스트

### 방안 B: 메인 스레드 큐 (폴백)

```python
import queue

class _DaemonServer:
    def __init__(self):
        self._request_queue = queue.Queue()

    def dispatch(self, cmd, args, ctx):
        future = Future()
        self._request_queue.put((cmd, args, ctx, future))
        return future.result(timeout=60)

    def _main_loop(self):
        while True:
            cmd, args, ctx, future = self._request_queue.get()
            result = self._execute(cmd, args, ctx)
            future.set_result(result)
```

- 장점: COM 스레딩 문제 없음
- 단점: 요청이 직렬화됨 (그래도 slot별 세션 분리는 유지)

### 결정

**Phase 17-1에서 방안 B(메인 스레드 큐)로 시작**, 안정성 확인 후 Phase 17-2에서 방안 A로 전환 고려.
- 직렬 실행이더라도 slot별 세션 분리가 핵심 가치
- 에이전트 A가 MM03에서 대기 중일 때 에이전트 B가 VA01 실행 가능 (요청 자체는 순차지만 세션 상태는 독립)

## 6. 사용 시나리오

### 시나리오 1: 두 에이전트가 동시에 다른 트랜잭션 작업

```
Agent A (operator):                      Agent B (verifier):
  sap_connect(slot_id="ses0")              sap_session_allocate(slot_id="ses1")
  sap_run("MM03", slot_id="ses0")          sap_connect(slot_id="ses1", session_index=1)
  sap_fill(fields=..., slot_id="ses0")     sap_run("VL03N", slot_id="ses1")
  sap_screen(slot_id="ses0")               sap_screen(slot_id="ses1")
  ...                                      ...
```

### 시나리오 2: 같은 시스템 다른 세션

```bash
# 터미널 1 — 자재 마스터 조회
sap connect --system PR1 --slot ses0
sap run MM03 --slot ses0

# 터미널 2 — 구매오더 조회 (같은 PR1, 다른 세션)
sap connect --system PR1 --slot ses1 --session 1
sap run ME23N --slot ses1
```

### 시나리오 3: 다른 시스템 병렬

```bash
sap connect --system PR1 --slot ses0    # PR1 시스템
sap connect --system ERP --slot ses1    # ERP 시스템 (다른 Connection)
```

## 7. 하위 호환성

| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| `get_sap()` | 싱글턴 반환 | `get_sap("ses0")` — 기본 슬롯 반환 |
| `sap connect` | 그대로 | 그대로 (slot 미지정 시 ses0) |
| MCP 도구 | slot_id 없음 | slot_id 선택적 (기본 "ses0") |
| daemon TCP | 그대로 | context.slot_id 추가 (없으면 "ses0") |
| 테스트 | 싱글턴 mock | slot mock — 기존 테스트는 ses0으로 동작 |

**기존 사용자 영향 없음** — slot_id를 지정하지 않으면 현재와 동일하게 동작.

## 8. 구현 계획

### Phase 17-1: SessionPool 코어 + service 레이어

| # | 작업 | 내용 |
|---|------|------|
| 1 | SessionSlot 모델 | dataclass 정의 |
| 2 | SessionPool 클래스 | slot CRUD + threading.Lock |
| 3 | SapGuiWrapper.create_session | 새 SAP 세션 생성 COM API |
| 4 | service.py 전환 | 싱글턴 → get_sap(slot_id) |
| 5 | 테스트 | pool 생성/해제/중복/최대 세션 |

### Phase 17-2: Dispatcher + Daemon 연동

| # | 작업 | 내용 |
|---|------|------|
| 1 | DispatchContext.slot_id | slot_id 필드 추가 |
| 2 | dispatcher slot 라우팅 | _get_sap() → _get_sap(ctx.slot_id) |
| 3 | daemon ThreadingTCPServer | 또는 메인스레드 큐 방식 |
| 4 | daemon context.slot_id | TCP 프로토콜에 slot_id 전달 |
| 5 | 테스트 | 다중 슬롯 dispatcher 테스트 |

### Phase 17-3: MCP + CLI 표면

| # | 작업 | 내용 |
|---|------|------|
| 1 | MCP 도구 slot_id | 모든 SAP 도구에 선택적 slot_id 추가 |
| 2 | sap_session_pool 도구 | 활성 슬롯 목록 |
| 3 | sap_session_allocate 도구 | 새 슬롯 할당 |
| 4 | sap_session_release 도구 | 슬롯 해제 |
| 5 | CLI --slot 옵션 | sap slots, sap connect --slot, sap run --slot |
| 6 | 테스트 | MCP + CLI 슬롯 라우팅 |

### Phase 17-4: COM 스레딩 안정화 + 문서

| # | 작업 | 내용 |
|---|------|------|
| 1 | CoInitialize 검증 | Windows 실기 테스트 (COM STA/MTA) |
| 2 | 동시성 안전 | 데드락 방지, 타임아웃 처리 |
| 3 | 문서 갱신 | README, 인터랙티브 가이드, TASKS.md |

## 9. 리스크와 대응

| 리스크 | 영향 | 대응 |
|--------|------|------|
| COM STA 스레딩 충돌 | 크래시 | 메인스레드 큐 폴백 |
| SAP 최대 세션 제한 (보통 6) | 7번째 세션 생성 실패 | pool에서 max_sessions 체크 + 에러 메시지 |
| 에이전트가 슬롯 해제 안 함 | 세션 누수 | TTL 기반 자동 해제 (30분 미사용 시) |
| 기존 테스트 깨짐 | CI 실패 | get_sap()의 기본값을 "ses0"으로 유지 |
| SAP 라이선스 비용 | 세션 수 × 라이선스 | 사용자에게 경고 + max_sessions 설정 |

## 10. 구현 완료 사항 (2026-04-16)

### 10.1 구현된 기능

- **SessionPool** (`session_pool.py`): slot_id 기반 SapGuiWrapper 풀, 최대 세션 제한, 에이전트 할당
- **TTL 자동 해제**: `ttl_seconds`(기본 1시간) 초과 슬롯 자동 정리. max_sessions 도달 시 자동 sweep
- **service 레이어**: 싱글턴 `_sap` → `SessionPool` 전환, `get_sap(slot_id)` 하위 호환 유지
- **Dispatcher**: 24개 `_get_sap()` 호출을 `slot_id` 인자로 변환, 3개 신규 명령 추가
- **MCP 도구**: `sap_session_pool`, `sap_session_allocate`, `sap_session_release`
- **CLI**: `sap slots`, `sap connect --slot`
- **COM STA 가드**: `_ensure_com_initialized()` — 비메인 스레드에서 COM 호출 시 자동 `CoInitialize`
- **테스트**: 42개 (풀 CRUD, TTL, 디스패처 통합, 스레드 안전성)

### 10.2 아직 미검증 (Windows 실기 필요)

- COM STA/MTA 모델에서 실제 SAP GUI 다중 세션 동시 조작
- `CreateSession()` COM 호출의 실제 동작 (타이밍, 세션 인덱스 안정성)
- 네트워크 끊김 시 세션 풀 복구 시나리오

## 11. 참고: SAP GUI 세션 API

```python
# 세션 생성
connection.Sessions(0).CreateSession()

# 세션 수 확인
connection.Sessions.Count

# 특정 세션 접근
session = connection.Sessions(session_index)

# 세션 종료
session.EndTransaction()  # 또는 /nex 명령
```
