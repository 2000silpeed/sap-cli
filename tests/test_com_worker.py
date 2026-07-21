from __future__ import annotations

import threading
import time

import pytest

from sap_cli.com_worker import ComWorker, com_worker_enabled


@pytest.fixture
def worker():
    w = ComWorker(pump_interval=0.01)
    w.start(timeout=5)
    try:
        yield w
    finally:
        w.stop()


def test_runs_on_a_single_dedicated_thread(worker):
    caller = threading.get_ident()
    tids = {worker.run(lambda: threading.get_ident()) for _ in range(5)}
    assert len(tids) == 1
    assert caller not in tids
    assert worker.ident in tids


def test_returns_value_and_propagates_exception(worker):
    assert worker.run(lambda: 2 + 3) == 5

    def boom():
        raise ValueError("com failed")

    with pytest.raises(ValueError, match="com failed"):
        worker.run(boom)


def test_reentrant_call_runs_inline_without_deadlock(worker):
    def outer():
        # A COM op that dispatches another COM op must not deadlock on the queue.
        inner_tid = worker.run(lambda: threading.get_ident())
        return threading.get_ident(), inner_tid

    outer_tid, inner_tid = worker.run(outer)
    assert outer_tid == inner_tid == worker.ident


def test_tasks_execute_in_submission_order(worker):
    seen: list[int] = []
    done = []

    def make(n):
        def task():
            seen.append(n)
        return task

    # Submit from several threads but assert the worker serialized them.
    for n in range(10):
        worker.run(make(n))
    assert seen == list(range(10))


def test_timeout_raises(worker):
    with pytest.raises(TimeoutError):
        worker.run(lambda: time.sleep(0.5), timeout=0.05)


def test_stop_rejects_further_work(worker):
    worker.stop()
    with pytest.raises(RuntimeError):
        worker.run(lambda: 1)


def test_enabled_flag_parses_env(monkeypatch):
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("SAP_CLI_COM_WORKER", value)
        assert com_worker_enabled() is True
    for value in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("SAP_CLI_COM_WORKER", value)
        assert com_worker_enabled() is False
    monkeypatch.delenv("SAP_CLI_COM_WORKER", raising=False)
    assert com_worker_enabled() is False
