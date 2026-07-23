"""A single dedicated STA thread that owns all SAP GUI COM in the daemon.

Why this exists
---------------
The daemon serves requests on one ``serve_forever`` thread. That thread is not a
well-behaved COM apartment for out-of-process automation: the *first* call into
the SAP GUI Scripting engine (another process's STA) can stall for ~60 seconds
before it succeeds, while the same call from a short-lived CLI process is
instant. Routing every COM operation onto one long-lived thread that
``CoInitialize``s STA and pumps its message queue makes the daemon behave like a
normal desktop script, so the first connect is fast.

The COM/threading behaviour can only be verified on Windows. This module is
therefore opt-in (``SAP_CLI_COM_WORKER=1``); when disabled the daemon runs
exactly as before. The marshalling itself (thread + queue + result/exception
propagation + re-entrancy) is plain Python and is unit-tested without COM.
"""

from __future__ import annotations

import os
import queue
import threading
from typing import Any, Callable


def com_worker_enabled() -> bool:
    """True when the operator opted into the dedicated COM worker thread."""

    return os.getenv("SAP_CLI_COM_WORKER", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


class _Task:
    __slots__ = ("fn", "done", "value", "exc")

    def __init__(self, fn: Callable[[], Any]) -> None:
        self.fn = fn
        self.done = threading.Event()
        self.value: Any = None
        self.exc: BaseException | None = None


class ComWorker:
    """Execute callables on one STA thread that pumps COM messages when idle."""

    def __init__(self, *, pump_interval: float = 0.05) -> None:
        self._queue: "queue.Queue[_Task | None]" = queue.Queue()
        self._pump_interval = max(0.005, float(pump_interval))
        self._thread = threading.Thread(
            target=self._run, name="sap-com-worker", daemon=True
        )
        self._ready = threading.Event()
        self._ident: int | None = None
        self._stopped = False

    @property
    def ident(self) -> int | None:
        return self._ident

    def start(self, timeout: float = 15.0) -> None:
        self._thread.start()
        if not self._ready.wait(timeout):
            raise RuntimeError("COM worker thread failed to initialize")

    def _run(self) -> None:
        self._ident = threading.get_ident()
        pythoncom = None
        try:  # pragma: no cover - Windows/COM only
            import pythoncom as _pythoncom

            _pythoncom.CoInitialize()
            pythoncom = _pythoncom
        except Exception:
            pythoncom = None
        self._ready.set()
        try:
            while True:
                try:
                    task = self._queue.get(timeout=self._pump_interval)
                except queue.Empty:
                    if pythoncom is not None:  # pragma: no cover - COM only
                        try:
                            pythoncom.PumpWaitingMessages()
                        except Exception:
                            pass
                    continue
                if task is None:
                    break
                try:
                    task.value = task.fn()
                except BaseException as exc:  # noqa: BLE001 - propagate to caller
                    task.exc = exc
                finally:
                    task.done.set()
        finally:
            if pythoncom is not None:  # pragma: no cover - COM only
                try:
                    pythoncom.CoUninitialize()
                except Exception:
                    pass

    def run(self, fn: Callable[[], Any], *, timeout: float | None = None) -> Any:
        """Run ``fn`` on the worker thread and return its result.

        A call made from the worker thread itself runs inline so a COM operation
        that dispatches another COM operation cannot deadlock on its own queue.
        """

        if self._stopped:
            raise RuntimeError("COM worker is stopped")
        if threading.get_ident() == self._ident:
            return fn()
        task = _Task(fn)
        self._queue.put(task)
        if not task.done.wait(timeout):
            raise TimeoutError("COM worker task timed out")
        if task.exc is not None:
            raise task.exc
        return task.value

    def stop(self) -> None:
        self._stopped = True
        self._queue.put(None)


__all__ = ["ComWorker", "com_worker_enabled"]
