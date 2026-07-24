"""SAP CLI 데몬 - COM 연결을 유지하는 상주 프로세스.

sap daemon start  → 백그라운드 서버 시작
sap daemon stop   → 서버 종료
sap daemon status → 상태 확인

데몬이 실행 중이면 sap 명령이 소켓으로 포워딩되어
Python 시작 + import + COM 연결 오버헤드를 제거한다.
예상 효과: 명령당 5초 → 0.2초 이하.
"""

from __future__ import annotations

import json
import logging
import os
import platform
import socket
import socketserver
import subprocess
import sys
import tempfile
import time
from typing import Any

from .com_wrapper import SapGuiError
from .ipc_auth import IpcAuthError, IpcAuthenticator, load_or_create_secret
from .dispatcher import (
    ApprovalRequiredError,
    DispatchContext,
    PermissionDeniedError,
    dispatch_command,
)

logger = logging.getLogger(__name__)

PORT = 18471
PID_FILE = os.path.join(tempfile.gettempdir(), "sap_cli_daemon.pid")
DEFAULT_COMMAND_TIMEOUT = 60.0
DEFAULT_PLAN_EXECUTE_TIMEOUT = 3600.0
# Windows 콜드 스타트는 무거운 import(pywin32 등) 때문에 기동이 수 초 걸릴 수 있어
# 여유 있는 벽시계 예산을 둔다. 자식이 죽으면 예산과 무관하게 즉시 실패로 판정한다.
DEFAULT_START_TIMEOUT = 20.0
# start_daemon 실패 원인(자식 stderr 마지막 줄, import 오류 등)을 기록해 두어
# doctor / CLI status 가 사용자에게 그대로 보여줄 수 있게 한다.
_last_start_error: str | None = None
# Side-effect-free liveness probe. A healthy local daemon answers a ping in
# milliseconds, so this only bites when the port is held by a process that is
# not responding (dead-but-bound, or wedged on a blocking COM call). Keeping it
# short bounds how long a first contact waits before giving up.
DAEMON_PROBE_TIMEOUT = 2.0


def _daemon_log_path() -> str:
    """데몬 자식 프로세스의 stderr를 남기는 로그 경로."""
    override = os.getenv("SAP_CLI_DAEMON_LOG")
    if override:
        return override
    root = os.path.expanduser("~/.sap_cli")
    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        return os.path.join(tempfile.gettempdir(), "sap_cli_daemon.log")
    return os.path.join(root, "daemon.log")


def _read_log_tail(path: str, max_bytes: int = 4000) -> str:
    """로그 파일의 마지막 부분만 읽어 반환 (없으면 빈 문자열)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(-max_bytes, os.SEEK_END)
            data = f.read()
        return data.decode("utf-8", errors="replace").strip()
    except OSError:
        return ""


def command_timeout(cmd: str) -> float:
    """Return a bounded client wait suitable for one-shot plan execution."""

    if str(cmd).strip() != "plan_execute":
        return DEFAULT_COMMAND_TIMEOUT
    raw = os.getenv("SAP_CLI_PLAN_EXECUTE_TIMEOUT", str(DEFAULT_PLAN_EXECUTE_TIMEOUT))
    try:
        return min(86_400.0, max(DEFAULT_COMMAND_TIMEOUT, float(raw)))
    except (TypeError, ValueError):
        return DEFAULT_PLAN_EXECUTE_TIMEOUT


# ---------------------------------------------------------------------------
# Client (경량 - stdlib만 사용, cli.py에서 import해도 무거운 의존성 없음)
# ---------------------------------------------------------------------------

def send_command(
    cmd: str,
    args: dict | None = None,
    timeout: float = DEFAULT_COMMAND_TIMEOUT,
    context: dict | None = None,
    authenticator: IpcAuthenticator | None = None,
) -> dict | None:
    """데몬에 명령 전송. 데몬 미실행 시 None 반환.

    context가 주어지면 DispatchContext 필드를 daemon에 전달하여
    MCP 등 외부 소스의 요청 문맥을 보존한다.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", PORT))
        signer = authenticator or IpcAuthenticator(load_or_create_secret())
        request = signer.sign_request(cmd, args or {}, context)
        payload = json.dumps(request).encode("utf-8") + b"\n"
        sock.sendall(payload)
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        sock.close()
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (ConnectionRefusedError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def probe_daemon(timeout: float | None = None) -> str:
    """Classify the daemon port without running any real command.

    Returns one of:
      "refused"      – nothing is listening (instant; safe to launch one).
      "alive"        – a ping was answered.
      "unresponsive" – the port is held but no reply arrived within `timeout`
                       (a foreign process, or a wedged daemon). A second daemon
                       cannot bind the port, so callers must not keep waiting.
    """
    if timeout is None:
        timeout = DAEMON_PROBE_TIMEOUT
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(("127.0.0.1", PORT))
    except (ConnectionRefusedError, OSError):
        sock.close()
        return "refused"
    try:
        signer = IpcAuthenticator(load_or_create_secret())
        request = signer.sign_request("ping", {}, None)
        sock.sendall(json.dumps(request).encode("utf-8") + b"\n")
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)
        result = json.loads(b"".join(chunks).decode("utf-8"))
        return "alive" if isinstance(result, dict) and result.get("ok") else "unresponsive"
    except (TimeoutError, OSError, json.JSONDecodeError):
        return "unresponsive"
    finally:
        sock.close()


def is_running(timeout: float = DAEMON_PROBE_TIMEOUT) -> bool:
    """데몬 실행 중인지 확인 (ping으로 실제 응답 확인)."""
    return probe_daemon(timeout=timeout) == "alive"


def _start_timeout() -> float:
    raw = os.getenv("SAP_CLI_DAEMON_START_TIMEOUT", str(DEFAULT_START_TIMEOUT))
    try:
        return max(2.0, float(raw))
    except (TypeError, ValueError):
        return DEFAULT_START_TIMEOUT


def start_daemon_detailed(timeout: float | None = None) -> tuple[bool, str | None]:
    """데몬 백그라운드 시작. (성공여부, 실패원인) 튜플 반환.

    - 자식 stderr를 DEVNULL로 버리지 않고 로그 파일로 남겨, 잘못된 venv/
      import 실패/포트 점유 등 기동 실패 원인을 진단할 수 있게 한다.
    - 자식이 준비되기 전에 죽으면(`proc.poll()`) 예산을 다 쓰지 않고 즉시
      실패로 판정하고 로그 tail을 원인으로 돌려준다.
    - 포트가 잡혀 있으나 응답이 없으면(외부/wedged 프로세스) 새 데몬이 바인딩할
      수 없으므로 대기하지 않고 즉시 실패한다. 데몬은 runtime 초기화를 끝낸 뒤에
      포트를 바인딩하므로, 정상 기동 중에는 unresponsive로 보이지 않는다.
    """
    global _last_start_error
    status = probe_daemon()
    if status == "alive":
        _last_start_error = None
        return True, None
    if status == "unresponsive":
        _last_start_error = (
            f"포트 {PORT}가 응답 없는 프로세스에 점유되어 있습니다 "
            "(죽었지만 바인딩 유지, 또는 wedged daemon). 새 데몬을 띄울 수 없습니다. "
            "해당 프로세스를 종료한 뒤 다시 시도하세요."
        )
        return False, _last_start_error

    python = sys.executable
    cmd = [python, "-m", "sap_cli.daemon"]
    log_path = _daemon_log_path()

    log_handle: Any = subprocess.DEVNULL
    try:
        # 반복 실패로 무한정 커지지 않도록 가볍게 회전.
        if os.path.exists(log_path) and os.path.getsize(log_path) > 1_000_000:
            log_handle = open(log_path, "w", encoding="utf-8")
        else:
            log_handle = open(log_path, "a", encoding="utf-8")
    except OSError:
        log_handle = subprocess.DEVNULL

    kwargs: dict = {"stdout": subprocess.DEVNULL, "stderr": log_handle}
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True

    try:
        proc = subprocess.Popen(cmd, **kwargs)
    except OSError as exc:
        _last_start_error = f"데몬 프로세스 실행 실패 ({python}): {exc}"
        return False, _last_start_error
    finally:
        if log_handle is not subprocess.DEVNULL:
            log_handle.close()  # 자식이 자신의 복제 핸들을 유지한다

    deadline = time.monotonic() + (timeout if timeout is not None else _start_timeout())
    while time.monotonic() < deadline:
        status = probe_daemon(timeout=0.5)
        if status == "alive":
            _last_start_error = None
            return True, None
        if status == "unresponsive":
            # The port was grabbed by a process that will not answer; our
            # spawned daemon could not bind it. Stop waiting instead of
            # pinging a wedged port for the whole readiness window.
            tail = _read_log_tail(log_path)
            _last_start_error = (
                f"포트 {PORT}가 응답 없는 프로세스에 점유되어 새 데몬이 바인딩하지 "
                f"못했습니다. 인터프리터: {python}. 로그: {log_path}"
                + (f"\n--- daemon.log tail ---\n{tail}" if tail else "")
            )
            return False, _last_start_error
        exit_code = proc.poll()
        if exit_code is not None:
            tail = _read_log_tail(log_path)
            _last_start_error = (
                f"데몬이 기동 중 종료됨 (exit={exit_code}). "
                f"인터프리터: {python}. 로그: {log_path}"
                + (f"\n--- daemon.log tail ---\n{tail}" if tail else "")
            )
            return False, _last_start_error
        time.sleep(0.15)

    tail = _read_log_tail(log_path)
    _last_start_error = (
        f"데몬 기동 대기 시간 초과. 인터프리터: {python}. 로그: {log_path}"
        + (f"\n--- daemon.log tail ---\n{tail}" if tail else "")
    )
    return False, _last_start_error


def start_daemon() -> bool:
    """데몬 백그라운드 시작. 성공 시 True (하위호환용 wrapper)."""
    ok, _ = start_daemon_detailed()
    return ok


def ensure_daemon(autostart: bool = True) -> bool:
    """살아있는 데몬이 있으면 True. 없으면 (autostart 시) 띄운다.

    포트가 잡혀 있으나 응답하지 않으면(죽었거나 wedged) 실제 명령을 보내지 않고
    즉시 False를 돌려주어, 호출자가 command 타임아웃까지 매달리지 않게 한다.
    """
    status = probe_daemon()
    if status == "alive":
        return True
    if status == "unresponsive":
        return False
    if not autostart:
        return False
    ok, _ = start_daemon_detailed()
    return ok


def stop_daemon() -> bool:
    """데몬 종료."""
    result = send_command("shutdown", timeout=5)
    return result is not None and result.get("ok", False)


def wait_until_stopped(timeout: float = 5.0) -> bool:
    """데몬 포트가 실제로 닫힐 때까지 대기. 닫히면 True.

    shutdown 응답은 데몬이 완전히 내려가기 전에 돌아오므로(0.1초 지연 타이머),
    stop 직후 곧바로 start 하면 죽어가는 데몬의 ping 응답을 "already running"으로
    오판하는 레이스가 있다. restart 경로는 반드시 이 함수로 종료를 확인해야 한다.
    ping 대신 raw connect를 쓰는 이유: serve 루프가 멈춘 뒤에도 listen 소켓이
    닫히기 전까지는 연결이 backlog에 수락되므로, "연결 거부"만이 완전 종료의
    신호다.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            probe = socket.create_connection(("127.0.0.1", PORT), timeout=0.2)
            probe.close()
        except OSError:
            return True
        time.sleep(0.1)
    return False


def doctor() -> dict:
    """데몬 + 실행 환경 헬스 리포트.

    "데몬이 안 뜬다 / 다른 프로젝트에서 실행이 안 된다"를 자가 진단할 수
    있도록, 어떤 인터프리터/가상환경으로 도는지, sap_cli·pywin32·mcp를
    import 할 수 있는지, 데몬이 살아있는지, 실패 시 로그 tail을 모아 준다.
    """
    log_path = _daemon_log_path()
    info: dict[str, Any] = {
        "python_executable": sys.executable,
        "prefix": sys.prefix,
        "in_venv": sys.prefix != getattr(sys, "base_prefix", sys.prefix),
        "cwd": os.getcwd(),
        "platform": platform.system(),
        "port": PORT,
        "pid_file": PID_FILE,
        "log_path": log_path,
    }

    try:
        import sap_cli

        info["sap_cli_path"] = os.path.dirname(os.path.abspath(sap_cli.__file__ or ""))
    except Exception as exc:  # pragma: no cover - 방어적
        info["sap_cli_path"] = None
        info["sap_cli_import_error"] = str(exc)

    for module_name, key in (("win32com", "pywin32"), ("mcp", "mcp")):
        try:
            __import__(module_name)
            info[key] = True
        except Exception:
            info[key] = False

    running = is_running(timeout=1.0)
    info["daemon_running"] = running
    if running:
        ping = send_command("ping", timeout=2.0)
        if ping and ping.get("ok"):
            info["daemon"] = ping.get("data")
    else:
        if _last_start_error:
            info["last_start_error"] = _last_start_error
        tail = _read_log_tail(log_path)
        if tail:
            info["log_tail"] = tail

    return info


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        authenticated = False
        try:
            raw = self.rfile.readline()
            if not raw:
                return
            request = json.loads(raw.decode("utf-8").strip())
            if not isinstance(request, dict):
                raise IpcAuthError("invalid daemon IPC request")
            self.server.authenticate(request)
            authenticated = True
            result = self.server.dispatch(
                request.get("cmd", ""),
                request.get("args", {}),
                request.get("context"),
            )
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        except IpcAuthError:
            self.wfile.write(
                json.dumps(
                    {"ok": False, "error": "IPC authentication failed"}
                ).encode("utf-8")
            )
        except Exception as e:
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))
        finally:
            # Untrusted traffic must not cause any daemon-side work, including
            # event processing that sits outside the command dispatcher.
            if authenticated:
                self.server.pump_events()

class _DaemonServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self, *, authenticator: IpcAuthenticator | None = None):
        # Defer the bind until all runtime state below is ready. TCPServer binds
        # at the top of __init__ by default, which leaves the port accepting
        # connections into the backlog while the heavy setup that follows (COM,
        # Win32 events, ledger recovery, map hydration) is still running. A
        # client connecting during that window blocks on recv until its command
        # timeout instead of getting a fast connection-refused — the "daemon
        # hangs until timeout on first launch" symptom.
        super().__init__(("127.0.0.1", PORT), _Handler, bind_and_activate=False)
        self._ipc_authenticator = authenticator or IpcAuthenticator(
            load_or_create_secret()
        )
        self._sap = None
        self._sap_connected = False
        self._event_manager = None
        from .events.runtime import set_event_manager
        from .planning.approvals import PlanApprovalStore, set_plan_approval_store
        from .planning.runtime import PlanRuntime, set_plan_runtime

        # Optional dedicated STA COM thread. When enabled, every COM operation
        # (command dispatch, event pump, map traversal ticks) runs on this one
        # thread and owner-thread-bound state is pinned to it. Off by default.
        self._com_worker = None
        try:
            from .com_worker import ComWorker, com_worker_enabled

            if com_worker_enabled():
                self._com_worker = ComWorker()
                self._com_worker.start()
                logger.info("COM worker thread enabled (tid=%s)", self._com_worker.ident)
        except Exception as exc:  # pragma: no cover - defensive; fall back to inline
            logger.warning("COM worker unavailable, using inline dispatch: %s", exc)
            self._com_worker = None
        owner_tid = self._com_worker.ident if self._com_worker is not None else None

        set_event_manager(None)
        self._plan_runtime = PlanRuntime(owner_thread_id=owner_tid)
        set_plan_runtime(self._plan_runtime)
        self._plan_approval_store = PlanApprovalStore(owner_thread_id=owner_tid)
        set_plan_approval_store(self._plan_approval_store)
        from .jobs import (
            DaemonJobCoordinator,
            PlanJobLedger,
            set_job_coordinator,
            set_plan_job_ledger,
        )

        self._job_coordinator = DaemonJobCoordinator()
        set_job_coordinator(self._job_coordinator)
        plan_job_root = os.getenv("SAP_CLI_PLAN_JOB_DIR") or os.path.expanduser(
            "~/.sap_cli/plan_jobs"
        )
        self._plan_job_ledger = PlanJobLedger(plan_job_root)
        set_plan_job_ledger(self._plan_job_ledger)
        for record in self._plan_job_ledger.startup_recovered:
            self._job_coordinator.submit(
                record.job_id,
                "plan_execute",
                record.request_digest,
                slot_id=record.slot,
                agent_name=record.owner,
            )
            self._job_coordinator.claim(record.job_id)
            self._job_coordinator.mark_outcome_unknown(
                record.job_id, error_code=record.error_code
            )
        from .dispatchers.ui_maps import default_job_store, default_map_store
        from .service import get_sap
        from .ui_map.cache import CachedUiMapStore
        from .ui_map.recorder import MapRecorder
        from .ui_map.runtime import (
            set_map_recorder_factory,
            set_ui_map_scheduler,
            set_ui_map_store,
        )
        from .ui_map.scheduler import UiMapScheduler
        from .ui_map.semantic import MapSemanticReviewer

        self._ui_map_store = CachedUiMapStore(
            default_map_store(), on_head_change=self._plan_runtime.invalidate_map_scope
        )
        set_ui_map_store(self._ui_map_store)
        self._ui_map_job_store = default_job_store()
        self._ui_map_scheduler = UiMapScheduler(
            self._ui_map_job_store,
            get_sap,
            map_store=self._ui_map_store,
            coordinator=self._job_coordinator,
            semantic_reviewer=MapSemanticReviewer(),
        )
        self._ui_map_scheduler.hydrate_persisted_jobs()
        set_ui_map_scheduler(self._ui_map_scheduler)
        event_manager_getter = None
        if platform.system() == "Windows":
            from .events.manager import EventSubscriptionManager
            from .events.runtime import get_event_manager
            from .events.win32_backend import Win32EventBackend

            # The event backend pins itself to the thread that creates it, and
            # it must be the same STA thread that pumps and runs SAP COM.
            self._event_manager = self._run_com(
                lambda: EventSubscriptionManager(Win32EventBackend())
            )
            set_event_manager(self._event_manager)
            event_manager_getter = get_event_manager
        set_map_recorder_factory(
            lambda _slot, store=self._ui_map_store, manager=event_manager_getter: MapRecorder(
                store, get_sap, get_event_manager=manager
            )
        )

        # Runtime state is ready — now expose the port. Until this point a
        # connect() is refused instantly, so is_running()/first-contact probes
        # fail fast and the autostart loop simply retries until we are ready.
        try:
            self.server_bind()
            self.server_activate()
        except BaseException:
            self.server_close()
            raise

    def authenticate(self, request: dict) -> None:
        self._ipc_authenticator.verify_request(request)

    def _run_com(self, fn, *, timeout=None):
        """Run ``fn`` on the COM worker thread, or inline when it is disabled."""

        worker = getattr(self, "_com_worker", None)
        if worker is None:
            return fn()
        return worker.run(fn, timeout=timeout)

    def service_actions(self) -> None:
        # Idle SAP COM (event pump + one traversal tick) must run on the same
        # STA thread as command dispatch.
        self._run_com(self._service_actions_impl)

    def _service_actions_impl(self) -> None:
        self.pump_events()
        scheduler = getattr(self, "_ui_map_scheduler", None)
        if scheduler is not None:
            scheduler.tick()

    def pump_events(self) -> None:
        if self._event_manager is not None:
            self._event_manager.pump()
            from .service import get_pool

            pool = get_pool()
            stale_slots = {
                item["slot_id"]
                for item in self._event_manager.list_subscriptions()
                if not pool.has_slot(item["slot_id"])
            }
            for slot_id in stale_slots:
                self._event_manager.cleanup_slot(slot_id)

    def close_events(self) -> None:
        from .events.runtime import set_event_manager

        if self._event_manager is not None:
            # close_all touches COM sinks; run it on the owner (worker) thread.
            self._run_com(self._event_manager.close_all)
            self._event_manager = None
        set_event_manager(None)
        worker = getattr(self, "_com_worker", None)
        if worker is not None:
            worker.stop()
            self._com_worker = None
        from .ui_map.runtime import set_ui_map_scheduler
        from .planning.approvals import set_plan_approval_store
        from .planning.runtime import set_plan_runtime
        from .jobs import set_job_coordinator, set_plan_job_ledger
        from .ui_map.runtime import set_ui_map_store

        set_ui_map_scheduler(None)
        set_ui_map_store(None)
        set_plan_approval_store(None)
        set_plan_runtime(None)
        set_job_coordinator(None)
        set_plan_job_ledger(None)

    def _get_sap(self, slot_id: str = "ses0"):
        from .service import get_sap
        wrapper = get_sap(slot_id)
        self._sap = wrapper
        return wrapper

    def dispatch(self, cmd: str, args: dict, ctx_dict: dict | None = None) -> dict:
        if cmd == "ping":
            # Ping must answer instantly for liveness probing. With the COM
            # worker active the connection is owned by that thread, so read a
            # cheap cached flag instead of touching COM cross-thread.
            connected = False
            if self._com_worker is not None:
                connected = bool(getattr(self, "_sap_connected", False))
            elif self._sap is not None:
                try:
                    connected = self._sap.is_connected
                except Exception:
                    pass
            return {"ok": True, "data": {"daemon": "running", "sap_connected": connected}}

        if cmd == "shutdown":
            import threading
            threading.Timer(0.1, self.shutdown).start()
            return {"ok": True, "data": {"message": "daemon stopped"}}

        # DispatchContext 구성 — 클라이언트가 보낸 context가 있으면 반영
        ctx = DispatchContext(source="daemon")
        if ctx_dict:
            for field in ("source", "tool_name", "agent_name", "cli_invocation"):
                if field in ctx_dict:
                    setattr(ctx, field, str(ctx_dict[field]))
            if "raw_argv" in ctx_dict and isinstance(ctx_dict["raw_argv"], list):
                ctx.raw_argv = [str(item) for item in ctx_dict["raw_argv"]]
            if "include_post_action_context" in ctx_dict:
                ctx.include_post_action_context = bool(ctx_dict["include_post_action_context"])
            if "include_screen_summary" in ctx_dict:
                ctx.include_screen_summary = bool(ctx_dict["include_screen_summary"])
            if "slot_id" in ctx_dict:
                ctx.slot_id = str(ctx_dict["slot_id"])
            if "session_id" in ctx_dict:
                ctx.session_id = str(ctx_dict["session_id"])

        try:
            data = self._run_com(lambda: self._execute_tracked(cmd, args, ctx))
            return {"ok": True, "data": data}
        except ApprovalRequiredError as e:
            return {
                "ok": False,
                "approval_required": True,
                "tool": e.tool_name,
                "reason": e.reason,
            }
        except (PermissionDeniedError, ValueError, RuntimeError, SapGuiError) as e:
            return {"ok": False, "error": str(e)}

    def _execute_tracked(self, cmd: str, args: dict, ctx: DispatchContext) -> dict:
        """Run one command (on the COM worker when enabled) and track the slot."""

        data = dispatch_command(cmd, args, ctx)
        if cmd == "connect":
            from .service import get_pool

            wrapper = get_pool().get(ctx.slot_id)
            if wrapper:
                self._sap = wrapper
                self._sap_connected = True
        return data

    # -- 명령 핸들러 --

    def _cmd_connect(self, args: dict) -> dict:
        from .com_wrapper import SapGuiWrapper
        sap = SapGuiWrapper()
        info = sap.connect(args.get("connection", 0), args.get("session", 0))
        self._sap = sap
        return {"connected": True, "session": info.model_dump(mode="json")}

    def _cmd_session(self, _args: dict) -> dict:
        return self._get_sap().get_session_info().model_dump(mode="json")

    def _cmd_screen(self, args: dict) -> dict:
        sap = self._get_sap()
        ont = sap.get_screen()
        if args.get("fields_only"):
            return {
                "transaction": ont.transaction,
                "screen_number": ont.screen_number,
                "changeable_fields": [f.model_dump(mode="json") for f in ont.get_changeable_fields()],
            }
        if args.get("actions_only"):
            return {
                "transaction": ont.transaction,
                "screen_number": ont.screen_number,
                "available_actions": [a.model_dump(mode="json") for a in ont.get_available_actions()],
            }
        return ont.model_dump(mode="json")

    def _cmd_run(self, args: dict) -> dict:
        sap = self._get_sap()
        tcode = args["tcode"]
        sap.run_transaction(tcode)
        info = sap.get_session_info()
        status = sap.get_status()
        return {
            "executed": tcode,
            "current_transaction": info.transaction,
            "screen_number": info.screen_number,
            "status": status.model_dump(mode="json"),
        }

    def _cmd_set(self, args: dict) -> dict:
        sap = self._get_sap()
        eid = args["element_id"]
        val = args["value"]
        sap.set_field(eid, val)
        return {"field": eid, "value_set": val, "actual_value": sap.get_field(eid)}

    def _cmd_get(self, args: dict) -> dict:
        sap = self._get_sap()
        eid = args["element_id"]
        return {"field": eid, "value": sap.get_field(eid)}

    def _cmd_press(self, args: dict) -> dict:
        sap = self._get_sap()
        eid = args["element_id"]
        sap.press(eid)
        status = sap.get_status()
        info = sap.get_session_info()
        return {
            "pressed": eid,
            "screen_number": info.screen_number,
            "status": status.model_dump(mode="json"),
        }

    def _cmd_vkey(self, args: dict) -> dict:
        sap = self._get_sap()
        key = int(args["key"])
        sap.send_vkey(key)
        status = sap.get_status()
        info = sap.get_session_info()
        return {
            "vkey_sent": key,
            "screen_number": info.screen_number,
            "transaction": info.transaction,
            "status": status.model_dump(mode="json"),
        }

    def _cmd_status(self, _args: dict) -> dict:
        return self._get_sap().get_status().model_dump(mode="json")

    def _cmd_table(self, args: dict) -> dict:
        return self._get_sap().read_table(args["element_id"]).model_dump(mode="json")

    def _cmd_command(self, args: dict) -> dict:
        sap = self._get_sap()
        cmd_str = args["cmd"]
        sap.send_command(cmd_str)
        info = sap.get_session_info()
        status = sap.get_status()
        return {
            "command": cmd_str,
            "transaction": info.transaction,
            "screen_number": info.screen_number,
            "status": status.model_dump(mode="json"),
        }

    def _cmd_modal(self, _args: dict) -> dict:
        sap = self._get_sap()
        has = sap.has_modal()
        result: dict = {"has_modal": has}
        if has:
            result["text"] = sap.get_modal_text()
        return result


def _run_server():
    """데몬 서버 실행 (이 프로세스가 서버가 됨)."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [SAP-DAEMON] %(message)s",
    )
    pid = os.getpid()

    # 서버 생성(무거운 runtime 초기화 후 마지막에 포트 바인딩)이 실패하면 그
    # traceback이 자식 stderr(=daemon.log)로 남도록 하고, PID 파일은 성공했을
    # 때만 쓴다.
    try:
        server = _DaemonServer()
    except Exception:
        logger.exception("데몬 초기화 실패 (port=%d, pid=%d)", PORT, pid)
        raise

    with open(PID_FILE, "w") as f:
        f.write(str(pid))

    logger.info("시작 (port=%d, pid=%d)", PORT, pid)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.close_events()
        server.server_close()
        try:
            os.unlink(PID_FILE)
        except OSError:
            pass
        logger.info("종료")


if __name__ == "__main__":
    _run_server()
