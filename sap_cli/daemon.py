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
# Side-effect-free liveness probe. A healthy local daemon answers a ping in
# milliseconds, so this only bites when the port is held by a process that is
# not responding (dead-but-bound, or wedged on a blocking COM call). Keeping it
# short bounds how long a first contact waits before giving up.
DAEMON_PROBE_TIMEOUT = 2.0


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


def is_running() -> bool:
    """데몬 실행 중인지 확인 (ping으로 실제 응답 확인)."""
    return probe_daemon() == "alive"


def _spawn_and_wait() -> bool:
    """데몬 프로세스를 띄우고 응답 가능해질 때까지 대기."""
    python = sys.executable
    cmd = [python, "-m", "sap_cli.daemon"]

    kwargs: dict = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        kwargs["start_new_session"] = True

    subprocess.Popen(cmd, **kwargs)

    for _ in range(30):
        time.sleep(0.2)
        status = probe_daemon()
        if status == "alive":
            return True
        if status == "unresponsive":
            # The port was grabbed by a process that will not answer; our
            # spawned daemon could not bind it. Stop waiting instead of
            # pinging a wedged port for the whole readiness window.
            return False
    return False


def start_daemon() -> bool:
    """데몬 백그라운드 시작. 성공 시 True."""
    status = probe_daemon()
    if status == "alive":
        return True
    if status == "unresponsive":
        # Port held by an unresponsive process — launching another cannot help.
        return False
    return _spawn_and_wait()


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
    return bool(autostart) and _spawn_and_wait()


def stop_daemon() -> bool:
    """데몬 종료."""
    result = send_command("shutdown", timeout=5)
    return result is not None and result.get("ok", False)


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
        self._event_manager = None
        from .events.runtime import set_event_manager
        from .planning.approvals import PlanApprovalStore, set_plan_approval_store
        from .planning.runtime import PlanRuntime, set_plan_runtime

        set_event_manager(None)
        self._plan_runtime = PlanRuntime()
        set_plan_runtime(self._plan_runtime)
        self._plan_approval_store = PlanApprovalStore()
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
        set_map_recorder_factory(
            lambda _slot, store=self._ui_map_store: MapRecorder(store, get_sap)
        )
        if platform.system() == "Windows":
            from .events.manager import EventSubscriptionManager
            from .events.win32_backend import Win32EventBackend

            self._event_manager = EventSubscriptionManager(Win32EventBackend())
            set_event_manager(self._event_manager)

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

    def service_actions(self) -> None:
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
            self._event_manager.close_all()
            self._event_manager = None
        set_event_manager(None)
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
            connected = False
            if self._sap is not None:
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
            data = dispatch_command(
                cmd,
                args,
                ctx,
            )
            if cmd == "connect":
                from .service import get_pool
                slot_id = ctx.slot_id
                wrapper = get_pool().get(slot_id)
                if wrapper:
                    self._sap = wrapper
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
    with open(PID_FILE, "w") as f:
        f.write(str(pid))

    logger.info("시작 (port=%d, pid=%d)", PORT, pid)
    server = _DaemonServer()
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
