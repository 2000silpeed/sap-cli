from __future__ import annotations

import threading

from sap_cli.events.win32_backend import Win32EventBackend


class FakePythonCom:
    def __init__(self) -> None:
        self.pumps = 0

    def PumpWaitingMessages(self) -> None:
        self.pumps += 1


class FakeSink:
    def __init__(self, handler_type) -> None:
        self.handler = handler_type()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self) -> None:
        self.sinks = []

    def WithEvents(self, target, handler_type):
        sink = FakeSink(handler_type)
        self.sinks.append((target, sink))
        return sink


def test_backend_builds_named_handlers_pumps_and_detaches() -> None:
    pythoncom = FakePythonCom()
    client = FakeClient()
    backend = Win32EventBackend(
        pythoncom_module=pythoncom, client_module=client
    )
    emitted = []
    handle = backend.attach(
        object(), ("StartRequest",), lambda name, args, kwargs: emitted.append((name, args, kwargs))
    )

    client.sinks[0][1].handler.StartRequest("session")
    backend.pump()
    backend.detach(handle)

    assert emitted == [("StartRequest", ("session",), {})]
    assert pythoncom.pumps == 1
    assert client.sinks[0][1].closed


def test_backend_rejects_cross_thread_access() -> None:
    backend = Win32EventBackend(
        pythoncom_module=FakePythonCom(), client_module=FakeClient()
    )
    errors = []
    thread = threading.Thread(
        target=lambda: _capture_error(backend, errors)
    )
    thread.start()
    thread.join()
    assert isinstance(errors[0], RuntimeError)
    assert "STA owner thread" in str(errors[0])


def _capture_error(backend, errors) -> None:
    try:
        backend.pump()
    except Exception as exc:
        errors.append(exc)
