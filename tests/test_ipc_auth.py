import copy
import json
import socket
import stat
from types import SimpleNamespace

import pytest

from sap_cli.ipc_auth import IpcAuthError, IpcAuthenticator, load_or_create_secret


class Clock:
    value = 1_700_000_000.0

    def __call__(self):
        return self.value


def authenticators(clock=None):
    clock = clock or Clock()
    nonces = iter(
        [
            "nonce_00000000000000000001",
            "nonce_00000000000000000002",
            "nonce_00000000000000000003",
        ]
    )
    secret = b"ipc-test-secret".ljust(32, b"!")
    return (
        IpcAuthenticator(secret, clock=clock, nonce_factory=lambda: next(nonces)),
        IpcAuthenticator(secret, clock=clock),
    )


def test_signed_request_covers_command_args_and_agent_context():
    client, server = authenticators()
    request = client.sign_request(
        "plan_execute",
        {"plan_id": "p", "execution_id": "e"},
        {"source": "mcp", "agent_name": "operator", "slot_id": "ses0"},
    )

    server.verify_request(request)
    assert request["auth"]["signature"] not in json.dumps(request["args"])

    for path, value in (
        (("cmd",), "job_reconcile"),
        (("args", "execution_id"), "different"),
        (("context", "agent_name"), "supervisor"),
        (("context", "slot_id"), "ses1"),
    ):
        changed = copy.deepcopy(request)
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        fresh_server = authenticators()[1]
        with pytest.raises(IpcAuthError, match="signature mismatch"):
            fresh_server.verify_request(changed)


def test_unsigned_expired_wrong_secret_and_nonce_replay_are_rejected():
    clock = Clock()
    client, server = authenticators(clock)
    request = client.sign_request("ping")
    with pytest.raises(IpcAuthError, match="unsigned"):
        server.verify_request({"cmd": "ping", "args": {}})

    wrong = IpcAuthenticator(b"different-secret".ljust(32, b"!"), clock=clock)
    with pytest.raises(IpcAuthError, match="signature mismatch"):
        wrong.verify_request(request)

    server.verify_request(request)
    with pytest.raises(IpcAuthError, match="replayed"):
        server.verify_request(request)

    expired = client.sign_request("ping")
    clock.value += 31
    with pytest.raises(IpcAuthError, match="expired"):
        server.verify_request(expired)


def test_secret_file_is_stable_owner_only_and_overbroad_mode_is_rejected(tmp_path):
    path = tmp_path / "private" / "daemon.secret"
    first = load_or_create_secret(path)
    second = load_or_create_secret(path)

    assert first == second
    assert len(first) == 32
    if hasattr(stat, "S_IMODE"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.chmod(0o644)
    with pytest.raises(IpcAuthError, match="too broad"):
        load_or_create_secret(path)


def test_handler_rejects_unsigned_request_before_dispatch():
    from sap_cli.daemon import _Handler

    client_socket, handler_socket = socket.socketpair()
    dispatch_calls = []
    pump_calls = []
    server = SimpleNamespace(
        authenticate=lambda request: (_ for _ in ()).throw(IpcAuthError("unsigned")),
        dispatch=lambda *args: dispatch_calls.append(args),
        pump_events=lambda: pump_calls.append(True),
    )
    try:
        client_socket.sendall(b'{"cmd":"ping","args":{}}\n')
        client_socket.shutdown(socket.SHUT_WR)
        _Handler(handler_socket, ("local", 0), server)
        response = json.loads(client_socket.recv(4096).decode("utf-8"))
    finally:
        client_socket.close()
        handler_socket.close()

    assert response == {"ok": False, "error": "IPC authentication failed"}
    assert dispatch_calls == []
    assert pump_calls == []


def test_handler_dispatches_and_pumps_only_after_authentication():
    from sap_cli.daemon import _Handler

    client_auth, server_auth = authenticators()
    request = client_auth.sign_request(
        "ping", {}, {"source": "mcp", "agent_name": "operator", "slot_id": "ses0"}
    )
    client_socket, handler_socket = socket.socketpair()
    dispatch_calls = []
    pump_calls = []
    server = SimpleNamespace(
        authenticate=server_auth.verify_request,
        dispatch=lambda *args: dispatch_calls.append(args) or {"ok": True},
        pump_events=lambda: pump_calls.append(True),
    )
    try:
        client_socket.sendall(json.dumps(request).encode("utf-8") + b"\n")
        client_socket.shutdown(socket.SHUT_WR)
        _Handler(handler_socket, ("local", 0), server)
        response = json.loads(client_socket.recv(4096).decode("utf-8"))
    finally:
        client_socket.close()
        handler_socket.close()

    assert response == {"ok": True}
    assert dispatch_calls == [
        ("ping", {}, {"source": "mcp", "agent_name": "operator", "slot_id": "ses0"})
    ]
    assert pump_calls == [True]


def test_handler_rejects_tampered_agent_context_before_dispatch():
    from sap_cli.daemon import _Handler

    client_auth, server_auth = authenticators()
    request = client_auth.sign_request(
        "plan_execute",
        {"plan_id": "p", "execution_id": "e"},
        {"source": "mcp", "agent_name": "operator", "slot_id": "ses0"},
    )
    request["context"]["agent_name"] = "supervisor"
    client_socket, handler_socket = socket.socketpair()
    dispatch_calls = []
    pump_calls = []
    server = SimpleNamespace(
        authenticate=server_auth.verify_request,
        dispatch=lambda *args: dispatch_calls.append(args),
        pump_events=lambda: pump_calls.append(True),
    )
    try:
        client_socket.sendall(json.dumps(request).encode("utf-8") + b"\n")
        client_socket.shutdown(socket.SHUT_WR)
        _Handler(handler_socket, ("local", 0), server)
        response = json.loads(client_socket.recv(4096).decode("utf-8"))
    finally:
        client_socket.close()
        handler_socket.close()

    assert response == {"ok": False, "error": "IPC authentication failed"}
    assert dispatch_calls == []
    assert pump_calls == []
