"""Durable, value-free ledger for daemon plan-execution jobs.

The ledger is deliberately narrower than the in-memory plan runtime.  It keeps
only immutable execution identity, ownership, lifecycle timestamps and a safe
machine error code.  Bound values, approval grants, plans, command parameters,
results and exception messages have no serializable field here.
"""

from __future__ import annotations

import os
import hashlib
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict

from sap_cli.storage import atomic_write_json


PlanJobStatus = Literal[
    "queued",
    "running",
    "completed",
    "failed",
    "outcome_unknown",
    "cancelled",
]

_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,255}$")
_SAFE_ERROR_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}$")
_TERMINAL = frozenset({"completed", "failed", "cancelled"})
_FINISHED = frozenset({*_TERMINAL, "outcome_unknown"})
_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset(
        {"running", "failed", "outcome_unknown", "cancelled"}
    ),
    "running": frozenset(
        {"completed", "failed", "outcome_unknown", "cancelled"}
    ),
    "outcome_unknown": frozenset({"completed", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobLedgerError(RuntimeError):
    """Base class for durable plan-job ledger failures."""


class JobLedgerConflictError(JobLedgerError):
    """Raised when an id or lifecycle state conflicts with durable history."""


class JobLedgerNotFoundError(JobLedgerError, KeyError):
    """Raised when no durable record exists for a job id."""


class PlanJobRecord(BaseModel):
    """Strict durable schema containing no business values or execution output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: str
    execution_id: str
    plan_id: str
    plan_hash: str
    request_digest: str
    owner: str
    slot: str
    status: PlanJobStatus
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str = ""

    @property
    def scope(self) -> tuple[str, str, str, str, str, str]:
        """Immutable identity used for idempotency and authorization."""

        return (
            self.execution_id,
            self.plan_id,
            self.plan_hash,
            self.request_digest,
            self.owner,
            self.slot,
        )


class PlanJobLedger:
    """Thread-safe, per-job JSON ledger with conservative crash recovery.

    Construction performs startup recovery by default.  Any record left in
    ``running`` is atomically changed to ``outcome_unknown``; it is never
    queued or executed again based on persistence alone.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], str] = _utc_now,
        recover_on_startup: bool = True,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        self.startup_recovered: tuple[PlanJobRecord, ...] = ()
        if recover_on_startup:
            self.startup_recovered = tuple(self.recover_running())

    def create(
        self,
        *,
        job_id: str,
        execution_id: str,
        plan_id: str,
        plan_hash: str,
        request_digest: str,
        owner: str,
        slot: str,
        status: PlanJobStatus = "running",
    ) -> PlanJobRecord:
        """Create once, or return the same immutable job scope idempotently."""

        normalized = {
            "job_id": self._safe_identifier(job_id, "job_id"),
            "execution_id": self._safe_identifier(execution_id, "execution_id"),
            "plan_id": self._safe_identifier(plan_id, "plan_id"),
            "plan_hash": self._required(plan_hash, "plan_hash", max_length=256),
            "request_digest": self._required(
                request_digest, "request_digest", max_length=256
            ),
            "owner": self._required(owner, "owner", max_length=256),
            "slot": self._required(slot, "slot", max_length=256),
        }
        if status not in _TRANSITIONS:
            raise ValueError(f"unsupported plan job status: {status}")

        with self._lock:
            path = self._path(normalized["job_id"])
            if path.exists():
                existing = self._read(path)
                requested_scope = (
                    normalized["execution_id"],
                    normalized["plan_id"],
                    normalized["plan_hash"],
                    normalized["request_digest"],
                    normalized["owner"],
                    normalized["slot"],
                )
                if existing.scope != requested_scope:
                    raise JobLedgerConflictError(
                        "job id is already bound to another execution scope"
                    )
                return existing.model_copy(deep=True)

            now = self._timestamp()
            record = PlanJobRecord(
                **normalized,
                status=status,
                created_at=now,
                updated_at=now,
                started_at=now if status == "running" else None,
                finished_at=now if status in _FINISHED else None,
                error_code=self._error_for_status(status, ""),
            )
            self._write(record)
            return record.model_copy(deep=True)

    def update(
        self,
        job_id: str,
        *,
        status: PlanJobStatus,
        error_code: str = "",
    ) -> PlanJobRecord:
        """Apply one lifecycle transition without changing immutable scope."""

        if status not in _TRANSITIONS:
            raise ValueError(f"unsupported plan job status: {status}")
        with self._lock:
            record = self._get(job_id)
            if status == record.status:
                return record.model_copy(deep=True)
            if status not in _TRANSITIONS[record.status]:
                raise JobLedgerConflictError(
                    f"invalid durable job transition: {record.status} -> {status}"
                )
            now = self._timestamp()
            updated = record.model_copy(
                update={
                    "status": status,
                    "updated_at": now,
                    "started_at": record.started_at
                    or (now if status == "running" else None),
                    "finished_at": now if status in _FINISHED else None,
                    "error_code": self._error_for_status(status, error_code),
                },
                deep=True,
            )
            self._write(updated)
            return updated.model_copy(deep=True)

    def recover_running(self) -> list[PlanJobRecord]:
        """Quarantine in-flight jobs found after startup, never rerun them."""

        recovered: list[PlanJobRecord] = []
        with self._lock:
            for record in self.list(status="running"):
                now = self._timestamp()
                updated = record.model_copy(
                    update={
                        "status": "outcome_unknown",
                        "updated_at": now,
                        "finished_at": now,
                        "error_code": "startup_outcome_unknown",
                    },
                    deep=True,
                )
                self._write(updated)
                recovered.append(updated.model_copy(deep=True))
        return recovered

    def get(self, job_id: str) -> PlanJobRecord:
        with self._lock:
            return self._get(job_id).model_copy(deep=True)

    def list(
        self,
        *,
        status: PlanJobStatus | None = None,
        owner: str | None = None,
        slot: str | None = None,
    ) -> list[PlanJobRecord]:
        """List records deterministically, optionally restricted to a scope."""

        if status is not None and status not in _TRANSITIONS:
            raise ValueError(f"unsupported plan job status: {status}")
        with self._lock:
            records: list[PlanJobRecord] = []
            for path in sorted(self.root.glob("*.json")):
                record = self._read(path)
                if status is not None and record.status != status:
                    continue
                if owner is not None and record.owner != str(owner):
                    continue
                if slot is not None and record.slot != str(slot):
                    continue
                records.append(record.model_copy(deep=True))
            return sorted(records, key=lambda item: (item.created_at, item.job_id))

    def authorize(self, job_id: str, *, owner: str, slot: str) -> PlanJobRecord:
        """Authorize access to a job's agent and SAP-session scope."""

        return self.authorize_scope(job_id, owner=owner, slot=slot)

    def authorize_scope(
        self,
        job_id: str,
        *,
        owner: str | None = None,
        slot: str | None = None,
        execution_id: str | None = None,
        plan_id: str | None = None,
        plan_hash: str | None = None,
        request_digest: str | None = None,
    ) -> PlanJobRecord:
        """Check any supplied immutable scope fields using a generic denial."""

        record = self.get(job_id)
        expected = {
            "owner": owner,
            "slot": slot,
            "execution_id": execution_id,
            "plan_id": plan_id,
            "plan_hash": plan_hash,
            "request_digest": request_digest,
        }
        if any(
            value is not None and getattr(record, name) != str(value)
            for name, value in expected.items()
        ):
            # Do not disclose which identity or digest mismatched.
            raise PermissionError("plan job belongs to another execution scope")
        return record

    def _get(self, job_id: str) -> PlanJobRecord:
        path = self._path(job_id)
        if not path.exists():
            raise JobLedgerNotFoundError(str(job_id))
        return self._read(path)

    def _path(self, job_id: str) -> Path:
        safe_id = self._safe_identifier(job_id, "job_id")
        filename = (
            safe_id
            if ":" not in safe_id
            else "job-" + hashlib.sha256(safe_id.encode()).hexdigest()
        )
        path = (self.root / f"{filename}.json").resolve()
        if path.parent != self.root:
            raise ValueError("plan job path escapes ledger root")
        return path

    @staticmethod
    def _read(path: Path) -> PlanJobRecord:
        return PlanJobRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def _write(self, record: PlanJobRecord) -> None:
        path = self._path(record.job_id)
        atomic_write_json(path, record.model_dump(mode="json"), mode=0o600)
        # Existing files created under unusual umasks remain owner-only too.
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Windows ACLs do not provide POSIX chmod semantics; the atomic
            # writer still requested the narrowest portable creation mode.
            if os.name != "nt":
                raise

    def _timestamp(self) -> str:
        value = str(self._clock() or "").strip()
        if not value:
            raise ValueError("ledger clock returned an empty timestamp")
        return value

    @staticmethod
    def _required(value: str, name: str, *, max_length: int) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{name} is required")
        if len(normalized) > max_length or any(ord(char) < 32 for char in normalized):
            raise ValueError(f"invalid {name}")
        return normalized

    @classmethod
    def _safe_identifier(cls, value: str, name: str) -> str:
        normalized = cls._required(value, name, max_length=256)
        if not _SAFE_ID.fullmatch(normalized):
            raise ValueError(f"invalid {name}")
        return normalized

    @staticmethod
    def _safe_error_code(value: str, default: str) -> str:
        normalized = str(value or default).strip()
        return normalized if _SAFE_ERROR_CODE.fullmatch(normalized) else default

    @classmethod
    def _error_for_status(cls, status: PlanJobStatus, value: str) -> str:
        if status == "failed":
            return cls._safe_error_code(value, "job_failed")
        if status == "outcome_unknown":
            return cls._safe_error_code(value, "outcome_unknown")
        return ""


# Names kept explicit so daemon integration can choose terminology without an
# adapter while the persisted schema remains singular.
DaemonJobLedger = PlanJobLedger
PlanExecutionJobStore = PlanJobLedger
PlanExecutionJobRecord = PlanJobRecord


__all__ = [
    "DaemonJobLedger",
    "JobLedgerConflictError",
    "JobLedgerError",
    "JobLedgerNotFoundError",
    "PlanExecutionJobRecord",
    "PlanExecutionJobStore",
    "PlanJobLedger",
    "PlanJobRecord",
    "PlanJobStatus",
]
