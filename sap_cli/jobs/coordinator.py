"""Owner-thread FIFO coordination for daemon SAP jobs.

The coordinator owns scheduling state only.  It deliberately does not retain
job inputs, bound values, plans, traversal checkpoints, results, or exception
messages.  Those objects remain in their purpose-built runtimes while the
value-safe snapshots from this module can be exposed as daemon diagnostics.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Literal


JobKind = Literal["plan_execute", "map_traverse"]
JobStatus = Literal[
    "queued",
    "running",
    "needs_user_input",
    "paused",
    "completed",
    "failed",
    "outcome_unknown",
    "cancelled",
]
ReconciledStatus = Literal["completed", "failed", "cancelled"]

_ACTIVE_STATUSES = frozenset({"queued", "running", "needs_user_input", "paused"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_LEASED_STATUSES = frozenset(
    {"queued", "running", "needs_user_input", "outcome_unknown"}
)
_SUPPORTED_KINDS = frozenset({"plan_execute", "map_traverse"})


class JobCoordinatorError(RuntimeError):
    """Base class for job coordination failures."""


class OwnerThreadError(JobCoordinatorError):
    """Raised when coordinator state is accessed outside its daemon thread."""


class JobCapacityError(JobCoordinatorError):
    """Raised when the bounded record or queue capacity is exhausted."""


class JobNotFoundError(JobCoordinatorError, KeyError):
    """Raised when a job id is not registered."""


class JobConflictError(JobCoordinatorError):
    """Raised when a job id is reused for another immutable request."""


class JobStateError(JobCoordinatorError):
    """Raised for an invalid job state transition."""


@dataclass(frozen=True, slots=True)
class JobSnapshot:
    """Value-free public job state.

    ``request_digest`` is intentionally omitted: even a digest need not be
    disclosed to clients to identify or schedule a job.
    """

    job_id: str
    kind: JobKind
    status: JobStatus
    slot_id: str
    agent_name: str
    sequence: int
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error_code: str = ""
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class SlotLeaseSnapshot:
    """Value-free diagnostic view of an exclusive slot lease."""

    slot_id: str
    job_id: str
    kind: JobKind
    status: Literal["queued", "running", "needs_user_input", "outcome_unknown"]
    quarantined: bool
    acquired_at: float


@dataclass(slots=True)
class _JobRecord:
    job_id: str
    kind: JobKind
    request_digest: str
    status: JobStatus
    slot_id: str
    agent_name: str
    sequence: int
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    error_code: str = ""


@dataclass(slots=True)
class _SlotLease:
    slot_id: str
    job_id: str
    acquired_at: float
    quarantined: bool = False


class DaemonJobCoordinator:
    """Bounded, owner-thread scheduler shared by plans and map traversals.

    Jobs are dequeued in submission order among jobs whose SAP slot is
    currently available.  A job waiting on a busy or quarantined slot does not
    block earlier-submitted work for a different free slot.

    Records are never evicted automatically.  This preserves job-id dedupe and
    prevents an old terminal or unknown execution from being silently rerun.
    Call :meth:`forget` explicitly for terminal history when capacity must be
    reclaimed.
    """

    def __init__(
        self,
        *,
        max_jobs: int = 1_024,
        max_queued: int = 256,
        clock: Callable[[], float] = time.monotonic,
        owner_thread_id: int | None = None,
    ) -> None:
        if max_jobs < 1:
            raise ValueError("max_jobs must be positive")
        if max_queued < 1:
            raise ValueError("max_queued must be positive")
        if max_queued > max_jobs:
            raise ValueError("max_queued cannot exceed max_jobs")
        self.max_jobs = int(max_jobs)
        self.max_queued = int(max_queued)
        self._clock = clock
        self._owner_thread_id = (
            threading.get_ident() if owner_thread_id is None else owner_thread_id
        )
        self._jobs: dict[str, _JobRecord] = {}
        self._queue: deque[str] = deque()
        self._leases: dict[str, _SlotLease] = {}
        self._next_sequence = 1

    @property
    def owner_thread_id(self) -> int:
        return self._owner_thread_id

    def assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise OwnerThreadError(
                "job coordinator must run on its daemon owner thread"
            )

    def submit(
        self,
        job_id: str,
        kind: JobKind,
        request_digest: str,
        *,
        slot_id: str,
        agent_name: str,
    ) -> JobSnapshot:
        """Register a job once and append it to the shared FIFO."""

        self.assert_owner_thread()
        job_id = self._required(job_id, "job_id")
        request_digest = self._required(request_digest, "request_digest")
        slot_id = self._required(slot_id, "slot_id")
        agent_name = self._required(agent_name, "agent_name")
        if kind not in _SUPPORTED_KINDS:
            raise ValueError(f"unsupported job kind: {kind}")

        existing = self._jobs.get(job_id)
        if existing is not None:
            if (
                existing.kind != kind
                or existing.request_digest != request_digest
                or existing.slot_id != slot_id
                or existing.agent_name != agent_name
            ):
                raise JobConflictError("job id is already bound to another request")
            # Idempotent submission is observational only.  It never queues or
            # reruns an existing job, regardless of its current state.
            return self._snapshot(existing, replayed=True)

        if len(self._jobs) >= self.max_jobs:
            raise JobCapacityError("job record capacity exhausted")
        if len(self._queue) >= self.max_queued:
            raise JobCapacityError("job queue capacity exhausted")

        now = self._clock()
        record = _JobRecord(
            job_id=job_id,
            kind=kind,
            request_digest=request_digest,
            status="queued",
            slot_id=slot_id,
            agent_name=agent_name,
            sequence=self._next_sequence,
            created_at=now,
        )
        self._next_sequence += 1
        self._jobs[job_id] = record
        self._queue.append(job_id)
        return self._snapshot(record)

    def claim_next(self) -> JobSnapshot | None:
        """Claim the oldest runnable job and acquire its exclusive slot lease."""

        self.assert_owner_thread()
        for job_id in tuple(self._queue):
            record = self._jobs[job_id]
            lease = self._leases.get(record.slot_id)
            if lease is not None and lease.job_id != job_id:
                continue
            self._queue.remove(job_id)
            now = self._clock()
            if lease is None:
                self._leases[record.slot_id] = _SlotLease(
                    slot_id=record.slot_id,
                    job_id=job_id,
                    acquired_at=now,
                )
            record.status = "running"
            record.error_code = ""
            if record.started_at is None:
                record.started_at = now
            return self._snapshot(record)
        return None

    def claim(self, job_id: str) -> JobSnapshot | None:
        """Claim one queued job without overtaking earlier work for its slot."""

        self.assert_owner_thread()
        record = self._get(job_id)
        self._require_status(record, "queued")
        lease = self._leases.get(record.slot_id)
        if lease is not None and lease.job_id != record.job_id:
            return None
        for queued_id in self._queue:
            queued = self._jobs[queued_id]
            if queued_id == record.job_id:
                break
            if queued.slot_id == record.slot_id:
                return None
        self._remove_from_queue(record.job_id)
        now = self._clock()
        if lease is None:
            self._leases[record.slot_id] = _SlotLease(
                slot_id=record.slot_id,
                job_id=record.job_id,
                acquired_at=now,
            )
        record.status = "running"
        record.error_code = ""
        if record.started_at is None:
            record.started_at = now
        return self._snapshot(record)

    def needs_user_input(self, job_id: str) -> JobSnapshot:
        """Pause a running traversal while retaining exclusive GUI ownership."""

        self.assert_owner_thread()
        record = self._get(job_id)
        self._require_status(record, "running")
        self._assert_lease(record)
        record.status = "needs_user_input"
        return self._snapshot(record)

    def resume(self, job_id: str) -> JobSnapshot:
        """Explicitly requeue a user-guided or budget-paused job."""

        self.assert_owner_thread()
        record = self._get(job_id)
        if record.status == "needs_user_input":
            self._assert_lease(record)
        elif record.status != "paused":
            raise JobStateError(
                f"job status must be needs_user_input or paused, got {record.status}"
            )
        if len(self._queue) >= self.max_queued:
            raise JobCapacityError("job queue capacity exhausted")
        record.status = "queued"
        self._queue.append(job_id)
        return self._snapshot(record)

    def pause(self, job_id: str) -> JobSnapshot:
        """Suspend a map job at a clean budget checkpoint and release its slot."""

        self.assert_owner_thread()
        record = self._get(job_id)
        if record.kind != "map_traverse":
            raise JobStateError("only map traversal jobs can be budget-paused")
        if record.status == "queued":
            self._remove_from_queue(record.job_id)
            lease = self._leases.get(record.slot_id)
            if lease is not None and lease.job_id == record.job_id:
                self._release_lease(record)
        elif record.status == "running":
            self._assert_lease(record)
            self._release_lease(record)
        else:
            raise JobStateError(
                f"only queued or running map jobs can be paused, got {record.status}"
            )
        record.status = "paused"
        record.error_code = ""
        record.finished_at = None
        return self._snapshot(record)

    def complete(self, job_id: str) -> JobSnapshot:
        return self._finish(job_id, "completed")

    def fail(self, job_id: str, *, error_code: str = "job_failed") -> JobSnapshot:
        return self._finish(job_id, "failed", error_code=error_code)

    def cancel(self, job_id: str) -> JobSnapshot:
        """Cancel non-running work without pretending an in-flight action stopped."""

        self.assert_owner_thread()
        record = self._get(job_id)
        release_lease = False
        if record.status == "queued":
            self._remove_from_queue(job_id)
            lease = self._leases.get(record.slot_id)
            release_lease = lease is not None and lease.job_id == record.job_id
        elif record.status == "needs_user_input":
            self._assert_lease(record)
            release_lease = True
        elif record.status == "running":
            # Coordinator access is owner-thread confined, so a cancel command
            # cannot overlap the SAP action slice itself.
            self._assert_lease(record)
            release_lease = True
        elif record.status == "paused":
            release_lease = False
        else:
            raise JobStateError(
                "only queued, idle-running, paused, or needs_user_input jobs can be cancelled"
            )
        record.status = "cancelled"
        record.error_code = ""
        record.finished_at = self._clock()
        if release_lease:
            self._release_lease(record)
        return self._snapshot(record)

    def mark_outcome_unknown(
        self,
        job_id: str,
        *,
        error_code: str = "outcome_unknown",
    ) -> JobSnapshot:
        """Quarantine a running job's slot until explicit reconciliation."""

        self.assert_owner_thread()
        record = self._get(job_id)
        self._require_status(record, "running")
        lease = self._assert_lease(record)
        record.status = "outcome_unknown"
        record.error_code = self._safe_error_code(error_code, "outcome_unknown")
        record.finished_at = self._clock()
        lease.quarantined = True
        return self._snapshot(record)

    def reconcile(
        self,
        job_id: str,
        status: ReconciledStatus,
        *,
        error_code: str = "",
    ) -> JobSnapshot:
        """Record the operator-confirmed outcome and release quarantine."""

        self.assert_owner_thread()
        record = self._get(job_id)
        self._require_status(record, "outcome_unknown")
        lease = self._assert_lease(record)
        if not lease.quarantined:
            raise JobStateError("outcome_unknown slot is not quarantined")
        if status not in _TERMINAL_STATUSES:
            raise ValueError("reconciled status must be completed, failed, or cancelled")
        record.status = status
        record.error_code = (
            self._safe_error_code(error_code, "reconciled_failure")
            if status == "failed"
            else ""
        )
        record.finished_at = self._clock()
        self._release_lease(record)
        return self._snapshot(record)

    def validate_reconciliation(self, job_id: str) -> JobSnapshot:
        """Validate reconciliation state and quarantine without releasing it."""

        self.assert_owner_thread()
        record = self._get(job_id)
        self._require_status(record, "outcome_unknown")
        lease = self._assert_lease(record)
        if not lease.quarantined:
            raise JobStateError("outcome_unknown slot is not quarantined")
        return self._snapshot(record)

    def requeue_reconciled_map(self, job_id: str) -> JobSnapshot:
        """Resume a dirty map job only after the operator proved no action occurred."""

        self.assert_owner_thread()
        record = self._get(job_id)
        self._require_status(record, "outcome_unknown")
        if record.kind != "map_traverse":
            raise JobStateError("only map traversal can be requeued after reconciliation")
        lease = self._assert_lease(record)
        if not lease.quarantined:
            raise JobStateError("outcome_unknown slot is not quarantined")
        if len(self._queue) >= self.max_queued:
            raise JobCapacityError("job queue capacity exhausted")
        lease.quarantined = False
        record.status = "queued"
        record.error_code = ""
        record.finished_at = None
        self._queue.append(record.job_id)
        return self._snapshot(record)

    def get(self, job_id: str) -> JobSnapshot:
        self.assert_owner_thread()
        return self._snapshot(self._get(job_id))

    def snapshots(self) -> tuple[JobSnapshot, ...]:
        """Return immutable, value-free snapshots in submission order."""

        self.assert_owner_thread()
        return tuple(
            self._snapshot(record)
            for record in sorted(self._jobs.values(), key=lambda item: item.sequence)
        )

    def lease_for_slot(self, slot_id: str) -> SlotLeaseSnapshot | None:
        self.assert_owner_thread()
        lease = self._leases.get(str(slot_id))
        if lease is None:
            return None
        record = self._jobs[lease.job_id]
        if record.status not in _LEASED_STATUSES:
            raise JobStateError("slot lease points to a non-leased job")
        return SlotLeaseSnapshot(
            slot_id=lease.slot_id,
            job_id=lease.job_id,
            kind=record.kind,
            status=record.status,
            quarantined=lease.quarantined,
            acquired_at=lease.acquired_at,
        )

    def forget(self, job_id: str) -> bool:
        """Explicitly reclaim one terminal record after callers retain history."""

        self.assert_owner_thread()
        record = self._get(job_id)
        if record.status not in _TERMINAL_STATUSES:
            raise JobStateError("only reconciled terminal jobs can be forgotten")
        lease = self._leases.get(record.slot_id)
        if lease is not None and lease.job_id == record.job_id:
            raise JobStateError("cannot forget a job with an active slot lease")
        self._jobs.pop(record.job_id)
        return True

    def __len__(self) -> int:
        self.assert_owner_thread()
        return len(self._jobs)

    @property
    def queued_count(self) -> int:
        self.assert_owner_thread()
        return len(self._queue)

    def _finish(
        self,
        job_id: str,
        status: Literal["completed", "failed"],
        *,
        error_code: str = "",
    ) -> JobSnapshot:
        self.assert_owner_thread()
        record = self._get(job_id)
        self._require_status(record, "running")
        self._assert_lease(record)
        record.status = status
        record.error_code = (
            self._safe_error_code(error_code, "job_failed")
            if status == "failed"
            else ""
        )
        record.finished_at = self._clock()
        self._release_lease(record)
        return self._snapshot(record)

    def _get(self, job_id: str) -> _JobRecord:
        job_id = self._required(job_id, "job_id")
        record = self._jobs.get(job_id)
        if record is None:
            raise JobNotFoundError(job_id)
        return record

    def _assert_lease(self, record: _JobRecord) -> _SlotLease:
        lease = self._leases.get(record.slot_id)
        if lease is None or lease.job_id != record.job_id:
            raise JobStateError("job does not own its SAP slot lease")
        return lease

    def _release_lease(self, record: _JobRecord) -> None:
        self._assert_lease(record)
        self._leases.pop(record.slot_id)

    def _remove_from_queue(self, job_id: str) -> None:
        try:
            self._queue.remove(job_id)
        except ValueError as exc:  # invariant violation, not caller input
            raise JobStateError("queued job is missing from FIFO") from exc

    @staticmethod
    def _require_status(record: _JobRecord, status: JobStatus) -> None:
        if record.status != status:
            raise JobStateError(
                f"job must be {status}; current status is {record.status}"
            )

    @staticmethod
    def _required(value: str, name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{name} is required")
        return normalized

    @staticmethod
    def _safe_error_code(value: str, default: str) -> str:
        normalized = str(value or default).strip()
        if not normalized:
            return default
        # Public status contains stable machine codes only, never exception text.
        if len(normalized) > 80 or any(
            not (char.isascii() and (char.isalnum() or char in "_-.:"))
            for char in normalized
        ):
            return default
        return normalized

    @staticmethod
    def _snapshot(record: _JobRecord, *, replayed: bool = False) -> JobSnapshot:
        return JobSnapshot(
            job_id=record.job_id,
            kind=record.kind,
            status=record.status,
            slot_id=record.slot_id,
            agent_name=record.agent_name,
            sequence=record.sequence,
            created_at=record.created_at,
            started_at=record.started_at,
            finished_at=record.finished_at,
            error_code=record.error_code,
            replayed=replayed,
        )
