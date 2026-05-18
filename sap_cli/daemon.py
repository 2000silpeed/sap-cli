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
import socket
import socketserver
import subprocess
import sys
import tempfile
import time

from .com_wrapper import SapGuiError
from .dispatcher import (
    ApprovalRequiredError,
    DispatchContext,
    PermissionDeniedError,
    dispatch_command,
)

logger = logging.getLogger(__name__)

PORT = 18471
PID_FILE = os.path.join(tempfile.gettempdir(), "sap_cli_daemon.pid")


# ---------------------------------------------------------------------------
# Client (경량 - stdlib만 사용, cli.py에서 import해도 무거운 의존성 없음)
# ---------------------------------------------------------------------------

def send_command(
    cmd: str,
    args: dict | None = None,
    timeout: float = 60,
    context: dict | None = None,
) -> dict | None:
    """데몬에 명령 전송. 데몬 미실행 시 None 반환.

    context가 주어지면 DispatchContext 필드를 daemon에 전달하여
    MCP 등 외부 소스의 요청 문맥을 보존한다.
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(("127.0.0.1", PORT))
        request: dict = {"cmd": cmd, "args": args or {}}
        if context:
            request["context"] = context
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


def is_running() -> bool:
    """데몬 실행 중인지 확인 (ping으로 실제 응답 확인)."""
    result = send_command("ping", timeout=2)
    return result is not None and result.get("ok", False)


def start_daemon() -> bool:
    """데몬 백그라운드 시작. 성공 시 True."""
    if is_running():
        return True

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
        if is_running():
            return True
    return False


def stop_daemon() -> bool:
    """데몬 종료."""
    result = send_command("shutdown", timeout=5)
    return result is not None and result.get("ok", False)


# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            raw = self.rfile.readline()
            if not raw:
                return
            request = json.loads(raw.decode("utf-8").strip())
            result = self.server.dispatch(
                request.get("cmd", ""),
                request.get("args", {}),
                request.get("context"),
            )
            self.wfile.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        except Exception as e:
            self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode("utf-8"))


class _DaemonServer(socketserver.TCPServer):
    allow_reuse_address = True

    def __init__(self):
        super().__init__(("127.0.0.1", PORT), _Handler)
        self._sap = None

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
        server.server_close()
        try:
            os.unlink(PID_FILE)
        except OSError:
            pass
        logger.info("종료")


if __name__ == "__main__":
    _run_server()
