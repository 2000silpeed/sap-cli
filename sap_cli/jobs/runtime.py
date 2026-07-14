"""Process-local access to the daemon-owned common job coordinator."""

from __future__ import annotations

from .coordinator import DaemonJobCoordinator
from .store import PlanJobLedger


_coordinator: DaemonJobCoordinator | None = None
_ledger: PlanJobLedger | None = None


def set_job_coordinator(value: DaemonJobCoordinator | None) -> None:
    global _coordinator
    _coordinator = value


def get_job_coordinator() -> DaemonJobCoordinator:
    if _coordinator is None:
        raise RuntimeError("job coordination requires the persistent daemon runtime")
    return _coordinator


def try_get_job_coordinator() -> DaemonJobCoordinator | None:
    return _coordinator


def set_plan_job_ledger(value: PlanJobLedger | None) -> None:
    global _ledger
    _ledger = value


def try_get_plan_job_ledger() -> PlanJobLedger | None:
    return _ledger


__all__ = [
    "get_job_coordinator",
    "set_job_coordinator",
    "try_get_job_coordinator",
    "set_plan_job_ledger",
    "try_get_plan_job_ledger",
]
