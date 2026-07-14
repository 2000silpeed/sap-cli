"""Daemon-process memory for previewed plans and idempotent executions.

The runtime deliberately stores :class:`ExecutionPlan` objects rather than
serialized payloads.  Sensitive variable values therefore never need a JSON,
disk, or cross-process cache representation inside this module.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Mapping

from .schema import ExecutionPlan


ExecutionStatus = Literal["running", "completed", "failed", "outcome_unknown"]


class PlanRuntimeError(RuntimeError):
    """Base error for daemon-owned planning runtime state."""


class PlanNotFoundError(PlanRuntimeError, KeyError):
    """Raised when a plan reference is absent or expired."""


class PlanIntegrityError(PlanRuntimeError):
    """Raised when a cached plan no longer matches its content hash."""


class PlanMapDependencyError(PlanRuntimeError):
    """Raised after evicting a plan whose exact UI-map dependency is stale."""


class ExecutionConflictError(PlanRuntimeError):
    """Raised when an execution id is reused for a different request."""


class SlotLeaseError(PlanRuntimeError):
    """Raised when another execution owns a SAP session slot."""


class OwnerThreadError(PlanRuntimeError):
    """Raised when daemon runtime state is touched off its owner thread."""


class RuntimeCapacityError(PlanRuntimeError):
    """Raised when active records prevent bounded-capacity eviction."""


@dataclass(frozen=True, slots=True)
class PlanReference:
    plan_id: str
    plan_hash: str
    expires_in_seconds: float


@dataclass(frozen=True, slots=True)
class MapDependency:
    """Exact, value-free UI-map snapshot required by a previewed plan."""

    map_id: str
    tcode: str
    system_id: str
    client: str
    language: str
    version: int
    fingerprint: str

    @classmethod
    def from_plan(cls, plan: ExecutionPlan) -> "MapDependency":
        scope = plan.map_scope
        return cls(
            map_id=str(plan.map_id).strip(),
            tcode=str(plan.tcode).strip().upper(),
            system_id=str(scope.get("system_id", scope.get("system", ""))).strip().upper(),
            client=str(scope.get("client", "")).strip(),
            language=str(scope.get("language", "")).strip().upper(),
            version=int(plan.map_version),
            fingerprint=str(plan.map_fingerprint or ""),
        )

    @property
    def scope(self) -> tuple[str, str, str, str, str]:
        return (
            self.map_id,
            self.tcode,
            self.system_id,
            self.client,
            self.language,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "map_id": self.map_id,
            "tcode": self.tcode,
            "system_id": self.system_id,
            "client": self.client,
            "language": self.language,
            "version": self.version,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True, slots=True)
class ExecutionSnapshot:
    execution_id: str
    request_digest: str
    status: ExecutionStatus
    slot_id: str
    agent_name: str
    result: Any = field(default=None, repr=False, compare=False)
    error_code: str = ""
    started_at: float = 0.0
    finished_at: float | None = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class SlotLease:
    slot_id: str
    agent_name: str
    execution_id: str
    acquired_at: float


@dataclass(slots=True)
class _PlanEntry:
    plan: ExecutionPlan = field(repr=False)
    expires_at: float
    map_dependency: MapDependency


@dataclass(slots=True)
class _ExecutionEntry:
    execution_id: str
    request_digest: str
    status: ExecutionStatus
    slot_id: str
    agent_name: str
    started_at: float
    result: Any = field(default=None, repr=False)
    error_code: str = ""
    finished_at: float | None = None


class PlanRegistry:
    """TTL-bound LRU of immutable preview plan objects."""

    def __init__(
        self,
        *,
        max_size: int = 128,
        ttl_seconds: float = 900.0,
        max_plan_steps: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
        owner_thread_id: int | None = None,
    ) -> None:
        if max_size < 1:
            raise ValueError("max_size must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_plan_steps < 1:
            raise ValueError("max_plan_steps must be positive")
        self.max_size = max_size
        self.ttl_seconds = float(ttl_seconds)
        self.max_plan_steps = max_plan_steps
        self._clock = clock
        self._owner_thread_id = (
            threading.get_ident() if owner_thread_id is None else owner_thread_id
        )
        self._plans: OrderedDict[tuple[str, str], _PlanEntry] = OrderedDict()

    @property
    def owner_thread_id(self) -> int:
        return self._owner_thread_id

    def assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise OwnerThreadError("plan runtime must run on its daemon owner thread")

    def register(self, plan: ExecutionPlan) -> PlanReference:
        self.assert_owner_thread()
        if not isinstance(plan, ExecutionPlan):
            raise TypeError("plan must be an ExecutionPlan object")
        if not plan.plan_id or not plan.plan_hash:
            raise ValueError("plan_id and plan_hash are required")
        if len(plan.steps) > self.max_plan_steps:
            raise RuntimeCapacityError("plan exceeds max_plan_steps")
        if not plan.verify_hash():
            raise PlanIntegrityError("plan hash verification failed")
        now = self._clock()
        self._purge_expired(now)
        key = (plan.plan_id, plan.plan_hash)
        existing = self._plans.get(key)
        if existing is None:
            while len(self._plans) >= self.max_size:
                self._plans.popitem(last=False)
            self._plans[key] = _PlanEntry(
                plan=plan,
                expires_at=now + self.ttl_seconds,
                map_dependency=MapDependency.from_plan(plan),
            )
        else:
            self._plans.move_to_end(key)
        entry = self._plans[key]
        return PlanReference(
            plan_id=plan.plan_id,
            plan_hash=plan.plan_hash,
            expires_in_seconds=max(0.0, entry.expires_at - now),
        )

    store = register

    def resolve(self, plan_id: str, plan_hash: str) -> ExecutionPlan:
        self.assert_owner_thread()
        now = self._clock()
        self._purge_expired(now)
        key = (str(plan_id), str(plan_hash))
        entry = self._plans.get(key)
        if entry is None:
            raise PlanNotFoundError(f"unknown or expired plan: {plan_id}")
        if not entry.plan.verify_hash() or entry.plan.plan_hash != plan_hash:
            self._plans.pop(key, None)
            raise PlanIntegrityError("cached plan hash verification failed")
        self._plans.move_to_end(key)
        return entry.plan

    get = resolve

    def validate_map_head(
        self,
        plan_id: str,
        plan_hash: str,
        head: Mapping[str, Any],
        *,
        fingerprint: str | None = None,
    ) -> ExecutionPlan:
        """Resolve a plan only when its exact map dependency is still current.

        ``UiMapStore.latest_head()`` can be passed directly.  A caller that has
        loaded the snapshot should also pass its content fingerprint.  Any
        mismatch evicts the plan before reporting the stale dependency.
        """

        self.assert_owner_thread()
        plan = self.resolve(plan_id, plan_hash)
        key = (str(plan_id), str(plan_hash))
        entry = self._plans[key]
        try:
            actual_scope = self._scope_from_head(head)
            actual_version = int(head["version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("map head requires an exact scope and version") from exc
        expected = entry.map_dependency
        matches = expected.scope == actual_scope and expected.version == actual_version
        if fingerprint is not None:
            matches = matches and expected.fingerprint == str(fingerprint)
        if not matches:
            self._plans.pop(key, None)
            raise PlanMapDependencyError("cached plan UI-map dependency is stale")
        return plan

    def invalidate_map_scope(
        self,
        *,
        map_id: str,
        tcode: str,
        system_id: str = "",
        client: str = "",
        language: str = "",
        head_version: int | None = None,
        head_fingerprint: str | None = None,
    ) -> int:
        """Evict plans for one normalized map scope, optionally keeping its head."""

        self.assert_owner_thread()
        self._purge_expired(self._clock())
        scope = self._normalize_scope(map_id, tcode, system_id, client, language)
        removed = 0
        for key, entry in list(self._plans.items()):
            dependency = entry.map_dependency
            if dependency.scope != scope:
                continue
            current = (
                head_version is not None
                and dependency.version == int(head_version)
                and (
                    head_fingerprint is None
                    or dependency.fingerprint == str(head_fingerprint)
                )
            )
            if current:
                continue
            self._plans.pop(key, None)
            removed += 1
        return removed

    def remove(self, plan_id: str, plan_hash: str) -> bool:
        self.assert_owner_thread()
        return self._plans.pop((str(plan_id), str(plan_hash)), None) is not None

    def purge_expired(self) -> int:
        self.assert_owner_thread()
        return self._purge_expired(self._clock())

    def summaries(self) -> list[dict[str, Any]]:
        """Return value-free diagnostics; cached plans are never serialized."""

        self.assert_owner_thread()
        now = self._clock()
        self._purge_expired(now)
        return [
            {
                "plan_id": plan_id,
                "plan_hash": plan_hash,
                "step_count": len(entry.plan.steps),
                "expires_in_seconds": max(0.0, entry.expires_at - now),
                "map_dependency": entry.map_dependency.as_dict(),
            }
            for (plan_id, plan_hash), entry in self._plans.items()
        ]

    def __len__(self) -> int:
        self.assert_owner_thread()
        self._purge_expired(self._clock())
        return len(self._plans)

    def _purge_expired(self, now: float) -> int:
        expired = [key for key, entry in self._plans.items() if entry.expires_at <= now]
        for key in expired:
            self._plans.pop(key, None)
        return len(expired)

    @classmethod
    def _scope_from_head(
        cls, head: Mapping[str, Any]
    ) -> tuple[str, str, str, str, str]:
        raw_scope = head.get("scope", head)
        if not isinstance(raw_scope, Mapping):
            raise ValueError("map head scope must be a mapping")
        return cls._normalize_scope(
            raw_scope.get("map_id", ""),
            raw_scope.get("tcode", ""),
            raw_scope.get("system_id", raw_scope.get("system", "")),
            raw_scope.get("client", ""),
            raw_scope.get("language", ""),
        )

    @staticmethod
    def _normalize_scope(
        map_id: Any, tcode: Any, system_id: Any, client: Any, language: Any
    ) -> tuple[str, str, str, str, str]:
        normalized = (
            str(map_id).strip(),
            str(tcode).strip().upper(),
            str(system_id).strip().upper(),
            str(client).strip(),
            str(language).strip().upper(),
        )
        if not normalized[0] or not normalized[1]:
            raise ValueError("map_id and tcode are required")
        return normalized


class PlanRuntime:
    """Owner-thread runtime with plan cache, execution dedupe, and slot leases."""

    def __init__(
        self,
        *,
        max_plans: int = 128,
        plan_ttl_seconds: float = 900.0,
        max_plan_steps: int = 10_000,
        max_executions: int = 1_024,
        execution_ttl_seconds: float = 3_600.0,
        clock: Callable[[], float] = time.monotonic,
        owner_thread_id: int | None = None,
    ) -> None:
        if max_executions < 1:
            raise ValueError("max_executions must be positive")
        if execution_ttl_seconds <= 0:
            raise ValueError("execution_ttl_seconds must be positive")
        self._clock = clock
        self._owner_thread_id = (
            threading.get_ident() if owner_thread_id is None else owner_thread_id
        )
        self.registry = PlanRegistry(
            max_size=max_plans,
            ttl_seconds=plan_ttl_seconds,
            max_plan_steps=max_plan_steps,
            clock=clock,
            owner_thread_id=self._owner_thread_id,
        )
        self.max_executions = max_executions
        self.execution_ttl_seconds = float(execution_ttl_seconds)
        self._executions: OrderedDict[str, _ExecutionEntry] = OrderedDict()
        self._leases: dict[str, SlotLease] = {}

    @property
    def owner_thread_id(self) -> int:
        return self._owner_thread_id

    def assert_owner_thread(self) -> None:
        self.registry.assert_owner_thread()

    def register_plan(self, plan: ExecutionPlan) -> PlanReference:
        return self.registry.register(plan)

    def get_plan(self, plan_id: str, plan_hash: str) -> ExecutionPlan:
        return self.registry.resolve(plan_id, plan_hash)

    def validate_plan_map_head(
        self,
        plan_id: str,
        plan_hash: str,
        head: Mapping[str, Any],
        *,
        fingerprint: str | None = None,
    ) -> ExecutionPlan:
        return self.registry.validate_map_head(
            plan_id, plan_hash, head, fingerprint=fingerprint
        )

    def invalidate_map_scope(self, **scope_and_head: Any) -> int:
        return self.registry.invalidate_map_scope(**scope_and_head)

    def begin_execution(
        self,
        execution_id: str,
        request_digest: str,
        *,
        slot_id: str,
        agent_name: str,
    ) -> ExecutionSnapshot:
        self.assert_owner_thread()
        execution_id = self._required(execution_id, "execution_id")
        request_digest = self._required(request_digest, "request_digest")
        slot_id = self._required(slot_id, "slot_id")
        agent_name = self._required(agent_name, "agent_name")
        now = self._clock()
        self._purge_executions(now)
        existing = self._executions.get(execution_id)
        if existing is not None:
            if (
                existing.request_digest != request_digest
                or existing.slot_id != slot_id
                or existing.agent_name != agent_name
            ):
                raise ExecutionConflictError(
                    "execution_id is already bound to a different request"
                )
            self._executions.move_to_end(execution_id)
            return self._snapshot(existing, replayed=True)

        lease = self._leases.get(slot_id)
        if lease is not None:
            raise SlotLeaseError(
                f"slot {slot_id} is leased by execution {lease.execution_id}"
            )
        self._ensure_execution_capacity(now)
        entry = _ExecutionEntry(
            execution_id=execution_id,
            request_digest=request_digest,
            status="running",
            slot_id=slot_id,
            agent_name=agent_name,
            started_at=now,
        )
        self._executions[execution_id] = entry
        self._leases[slot_id] = SlotLease(
            slot_id=slot_id,
            agent_name=agent_name,
            execution_id=execution_id,
            acquired_at=now,
        )
        return self._snapshot(entry)

    def complete_execution(
        self,
        execution_id: str,
        result: Any,
        *,
        slot_id: str | None = None,
        agent_name: str | None = None,
    ) -> ExecutionSnapshot:
        self.assert_owner_thread()
        entry = self._owned_execution(execution_id, slot_id, agent_name)
        if entry.status == "completed":
            return self._snapshot(entry, replayed=True)
        if entry.status != "running":
            raise ExecutionConflictError(
                f"cannot complete execution in state {entry.status}"
            )
        entry.status = "completed"
        entry.result = copy.deepcopy(result)
        entry.finished_at = self._clock()
        self._release_matching_lease(entry)
        return self._snapshot(entry)

    def fail_execution(
        self,
        execution_id: str,
        result: Any = None,
        *,
        error_code: str = "plan_failed",
        slot_id: str | None = None,
        agent_name: str | None = None,
    ) -> ExecutionSnapshot:
        """Record a definitive pre-commit failure and release its slot lease."""

        self.assert_owner_thread()
        entry = self._owned_execution(execution_id, slot_id, agent_name)
        if entry.status == "failed":
            return self._snapshot(entry, replayed=True)
        if entry.status != "running":
            raise ExecutionConflictError(
                f"cannot fail execution in state {entry.status}"
            )
        entry.status = "failed"
        entry.error_code = str(error_code or "plan_failed")
        entry.result = copy.deepcopy(result)
        entry.finished_at = self._clock()
        self._release_matching_lease(entry)
        return self._snapshot(entry)

    def mark_outcome_unknown(
        self,
        execution_id: str,
        *,
        error_code: str = "outcome_unknown",
        result: Any = None,
        slot_id: str | None = None,
        agent_name: str | None = None,
        retain_lease: bool = True,
    ) -> ExecutionSnapshot:
        self.assert_owner_thread()
        entry = self._owned_execution(execution_id, slot_id, agent_name)
        if entry.status in {"completed", "failed"}:
            raise ExecutionConflictError(
                f"{entry.status} execution cannot become outcome_unknown"
            )
        entry.status = "outcome_unknown"
        entry.error_code = str(error_code or "outcome_unknown")
        entry.result = copy.deepcopy(result)
        entry.finished_at = self._clock()
        if not retain_lease:
            self._release_matching_lease(entry)
        return self._snapshot(entry)

    def get_execution(self, execution_id: str) -> ExecutionSnapshot:
        self.assert_owner_thread()
        now = self._clock()
        self._purge_executions(now)
        entry = self._executions.get(str(execution_id))
        if entry is None:
            raise KeyError(f"unknown execution: {execution_id}")
        self._executions.move_to_end(entry.execution_id)
        return self._snapshot(entry)

    def lease_for_slot(self, slot_id: str) -> SlotLease | None:
        self.assert_owner_thread()
        return self._leases.get(str(slot_id))

    def release_execution_lease(
        self,
        execution_id: str,
        *,
        slot_id: str | None = None,
        agent_name: str | None = None,
    ) -> bool:
        """Release a reconciled running/unknown lease without deleting history."""

        self.assert_owner_thread()
        entry = self._owned_execution(execution_id, slot_id, agent_name)
        return self._release_matching_lease(entry)

    def reconcile_execution(
        self,
        execution_id: str,
        status: Literal["completed", "failed"],
        *,
        error_code: str = "",
        slot_id: str | None = None,
        agent_name: str | None = None,
    ) -> ExecutionSnapshot:
        """Record an operator-verified outcome and release quarantine.

        An unknown execution is never made runnable again. A verified
        not-applied outcome is recorded as a definitive failure with a distinct
        machine code so a caller may deliberately create a new execution id.
        """

        self.assert_owner_thread()
        entry = self._owned_execution(execution_id, slot_id, agent_name)
        if entry.status != "outcome_unknown":
            raise ExecutionConflictError(
                f"cannot reconcile execution in state {entry.status}"
            )
        if status not in {"completed", "failed"}:
            raise ValueError("reconciled execution status must be completed or failed")
        entry.status = status
        entry.error_code = (
            str(error_code or "manually_reconciled") if status == "failed" else ""
        )
        entry.finished_at = self._clock()
        self._release_matching_lease(entry)
        return self._snapshot(entry)

    def validate_reconciliation(
        self,
        execution_id: str,
        *,
        slot_id: str | None = None,
        agent_name: str | None = None,
    ) -> ExecutionSnapshot:
        """Validate reconciliation ownership/state without changing either lease."""

        self.assert_owner_thread()
        entry = self._owned_execution(execution_id, slot_id, agent_name)
        if entry.status != "outcome_unknown":
            raise ExecutionConflictError(
                f"cannot reconcile execution in state {entry.status}"
            )
        return self._snapshot(entry)

    def execution_summaries(self) -> list[dict[str, Any]]:
        self.assert_owner_thread()
        self._purge_executions(self._clock())
        return [
            {
                "execution_id": entry.execution_id,
                "request_digest": entry.request_digest,
                "status": entry.status,
                "slot_id": entry.slot_id,
                "agent_name": entry.agent_name,
                "error_code": entry.error_code,
            }
            for entry in self._executions.values()
        ]

    def _owned_execution(
        self,
        execution_id: str,
        slot_id: str | None,
        agent_name: str | None,
    ) -> _ExecutionEntry:
        entry = self._executions.get(str(execution_id))
        if entry is None:
            raise KeyError(f"unknown execution: {execution_id}")
        if slot_id is not None and str(slot_id) != entry.slot_id:
            raise SlotLeaseError("execution belongs to another slot")
        if agent_name is not None and str(agent_name) != entry.agent_name:
            raise SlotLeaseError("execution belongs to another agent")
        lease = self._leases.get(entry.slot_id)
        if entry.status in {"running", "outcome_unknown"} and (
            lease is None
            or lease.execution_id != entry.execution_id
            or lease.agent_name != entry.agent_name
        ):
            raise SlotLeaseError("execution no longer owns its slot lease")
        return entry

    def _release_matching_lease(self, entry: _ExecutionEntry) -> bool:
        lease = self._leases.get(entry.slot_id)
        if lease is None or lease.execution_id != entry.execution_id:
            return False
        self._leases.pop(entry.slot_id, None)
        return True

    def _ensure_execution_capacity(self, now: float) -> None:
        self._purge_executions(now)
        if len(self._executions) < self.max_executions:
            return
        for execution_id, entry in list(self._executions.items()):
            if entry.status in {"completed", "failed"} and entry.slot_id not in self._leases:
                self._executions.pop(execution_id, None)
                if len(self._executions) < self.max_executions:
                    return
        raise RuntimeCapacityError("execution registry is full of active records")

    def _purge_executions(self, now: float) -> int:
        expired: list[str] = []
        for execution_id, entry in self._executions.items():
            if entry.finished_at is None or entry.slot_id in self._leases:
                continue
            if entry.finished_at + self.execution_ttl_seconds <= now:
                expired.append(execution_id)
        for execution_id in expired:
            self._executions.pop(execution_id, None)
        return len(expired)

    @staticmethod
    def _required(value: str, name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{name} is required")
        return normalized

    @staticmethod
    def _snapshot(entry: _ExecutionEntry, *, replayed: bool = False) -> ExecutionSnapshot:
        return ExecutionSnapshot(
            execution_id=entry.execution_id,
            request_digest=entry.request_digest,
            status=entry.status,
            slot_id=entry.slot_id,
            agent_name=entry.agent_name,
            result=copy.deepcopy(entry.result),
            error_code=entry.error_code,
            started_at=entry.started_at,
            finished_at=entry.finished_at,
            replayed=replayed,
        )


_runtime: PlanRuntime | None = None


def set_plan_runtime(runtime: PlanRuntime | None) -> None:
    global _runtime
    _runtime = runtime


def get_plan_runtime() -> PlanRuntime:
    if _runtime is None:
        raise RuntimeError(
            "plan execution requires the persistent daemon runtime"
        )
    return _runtime


def try_get_plan_runtime() -> PlanRuntime | None:
    """Return the daemon runtime when bound, without masking runtime failures."""

    return _runtime


__all__ = [
    "ExecutionConflictError",
    "ExecutionSnapshot",
    "ExecutionStatus",
    "OwnerThreadError",
    "MapDependency",
    "PlanIntegrityError",
    "PlanMapDependencyError",
    "PlanNotFoundError",
    "PlanReference",
    "PlanRegistry",
    "PlanRuntime",
    "PlanRuntimeError",
    "RuntimeCapacityError",
    "SlotLease",
    "SlotLeaseError",
    "get_plan_runtime",
    "set_plan_runtime",
    "try_get_plan_runtime",
]
