"""pywin32 connection-point backend for SAP GUI Scripting events."""

from __future__ import annotations

import platform
import threading
from dataclasses import dataclass
from typing import Any

from .manager import EventEmitter


@dataclass(slots=True)
class _Attachment:
    sink: Any
    target: Any


class Win32EventBackend:
    """Attach and pump COM event sinks on one STA owner thread."""

    def __init__(self, *, pythoncom_module: Any = None, client_module: Any = None) -> None:
        if pythoncom_module is None or client_module is None:
            if platform.system() != "Windows":
                raise RuntimeError("SAP GUI COM events are only available on Windows")
            import pythoncom  # type: ignore[import-untyped]
            import win32com.client  # type: ignore[import-untyped]

            pythoncom_module = pythoncom
            client_module = win32com.client
        self._pythoncom = pythoncom_module
        self._client = client_module
        self._owner_thread = threading.get_ident()
        self._attachments: list[_Attachment] = []

    def attach(
        self,
        target: Any,
        event_names: tuple[str, ...],
        emit: EventEmitter,
    ) -> _Attachment:
        self._check_thread()
        methods: dict[str, Any] = {}
        for event_name in event_names:
            methods[event_name] = _event_method(event_name, emit)
        handler_type = type("SapGuiVerifiedEventSink", (), methods)
        sink = self._client.WithEvents(target, handler_type)
        attachment = _Attachment(sink=sink, target=target)
        self._attachments.append(attachment)
        return attachment

    def detach(self, handle: _Attachment) -> None:
        self._check_thread()
        if handle not in self._attachments:
            return
        close = getattr(handle.sink, "close", None)
        if callable(close):
            close()
        self._attachments.remove(handle)
        handle.sink = None
        handle.target = None

    def pump(self) -> None:
        self._check_thread()
        self._pythoncom.PumpWaitingMessages()

    def _check_thread(self) -> None:
        if threading.get_ident() != self._owner_thread:
            raise RuntimeError("COM event backend must run on its STA owner thread")


def _event_method(event_name: str, emit: EventEmitter):
    def callback(self: Any, *args: Any) -> None:
        del self
        emit(event_name, args, {})

    callback.__name__ = event_name
    return callback
