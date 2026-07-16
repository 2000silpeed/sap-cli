"""Durable lifecycle records for resumable SAP UI-map traversal jobs."""

from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from sap_cli.storage import atomic_write_json

from .traversal import TraversalCheckpoint, TraversalResult


JobStatus = Literal[
    "queued",
    "running",
    "needs_user_input",
    "paused",
    "interrupted",
    "completed",
    "failed",
    "cancelled",
]
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_TERMINAL = {"completed", "failed", "cancelled"}
BUDGET_STOP_REASONS = frozenset(
    {
        "max_seconds",
        "max_states",
        "max_transitions",
        "max_pages_per_collection",
        "max_nodes_per_tree",
    }
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class MapJobRecord(BaseModel):
    job_id: str
    tcode: str
    map_id: str
    owner_agent: str = ""
    slot_id: str = "ses0"
    traversal_config: dict[str, Any] = Field(default_factory=dict)
    status: JobStatus = "queued"
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)
    stop_reason: str = ""
    error: str = ""
    checkpoint: TraversalCheckpoint | None = None
    pending_questions: list[dict[str, Any]] = Field(default_factory=list)
    resume_provenance: list[dict[str, Any]] = Field(default_factory=list)


class MapJobStore:
    """Thread-safe JSON job store that never persists answer values."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def create(
        self,
        *,
        tcode: str,
        map_id: str,
        checkpoint: TraversalCheckpoint | None = None,
        job_id: str | None = None,
        owner_agent: str = "",
        slot_id: str = "ses0",
        traversal_config: dict[str, Any] | None = None,
    ) -> MapJobRecord:
        resolved_id = uuid.uuid4().hex if job_id is None else job_id
        path = self._path(resolved_id)
        with self._lock:
            if path.exists():
                raise FileExistsError(f"map job already exists: {resolved_id}")
            record = MapJobRecord(
                job_id=resolved_id,
                tcode=tcode.upper(),
                map_id=map_id,
                owner_agent=owner_agent,
                slot_id=slot_id,
                traversal_config=dict(traversal_config or {}),
                checkpoint=checkpoint,
            )
            self._write(record)
            return record.model_copy(deep=True)

    def get(self, job_id: str) -> MapJobRecord:
        with self._lock:
            path = self._path(job_id)
            if not path.exists():
                raise KeyError(f"unknown map job: {job_id}")
            return MapJobRecord.model_validate_json(path.read_text(encoding="utf-8"))

    def authorize(self, job_id: str, *, owner_agent: str, slot_id: str) -> MapJobRecord:
        """Reject accidental cross-agent or cross-session job access."""

        record = self.get(job_id)
        if record.owner_agent and record.owner_agent != owner_agent:
            raise PermissionError("map job belongs to another agent")
        if record.slot_id != slot_id:
            raise PermissionError("map job belongs to another SAP slot")
        return record

    def list(self, *, status: JobStatus | None = None) -> list[MapJobRecord]:
        with self._lock:
            records: list[MapJobRecord] = []
            for path in sorted(self.root.glob("*.json")):
                record = MapJobRecord.model_validate_json(path.read_text(encoding="utf-8"))
                if status is None or record.status == status:
                    records.append(record)
            return sorted(records, key=lambda item: (item.updated_at, item.job_id), reverse=True)

    def checkpoint_callback(self, job_id: str):
        """Return the callback expected by :class:`TraversalEngine`."""

        self._path(job_id)

        def save(checkpoint: TraversalCheckpoint) -> None:
            self.save_checkpoint(job_id, checkpoint)

        return save

    def save_checkpoint(
        self, job_id: str, checkpoint: TraversalCheckpoint
    ) -> MapJobRecord:
        with self._lock:
            record = self.get(job_id)
            self._ensure_mutable(record)
            record.checkpoint = checkpoint.model_copy(deep=True)
            record.pending_questions = self._question_summaries(checkpoint)
            record.status = (
                "needs_user_input" if checkpoint.needs_user_input else "running"
            )
            record.updated_at = _now()
            self._write(record)
            return record.model_copy(deep=True)

    def finish(self, job_id: str, result: TraversalResult) -> MapJobRecord:
        with self._lock:
            record = self.get(job_id)
            self._ensure_mutable(record)
            record.checkpoint = result.checkpoint.model_copy(deep=True)
            record.stop_reason = result.stop_reason
            record.pending_questions = self._question_summaries(result.checkpoint)
            if result.stop_reason == "needs_user_input":
                record.status = "needs_user_input"
            elif result.stop_reason in BUDGET_STOP_REASONS:
                record.status = "paused"
            elif result.stop_reason in {"complete", "passive_complete"}:
                record.status = "completed"
            else:
                record.status = "failed"
            record.updated_at = _now()
            self._write(record)
            return record.model_copy(deep=True)

    def resume_paused(
        self, job_id: str, *, budget: dict[str, Any]
    ) -> MapJobRecord:
        """Persist a validated larger budget and requeue a paused checkpoint."""

        with self._lock:
            record = self.get(job_id)
            if record.status != "paused":
                raise ValueError("only a budget-paused map job can extend its budget")
            previous_reason = record.stop_reason
            record.traversal_config = {
                **record.traversal_config,
                "budget": dict(budget),
            }
            record.resume_provenance.append(
                {
                    "kind": "budget_extension",
                    "previous_stop_reason": previous_reason,
                    "fields": sorted(str(key) for key in budget),
                    "value_stored": False,
                }
            )
            record.status = "queued"
            record.stop_reason = ""
            record.error = ""
            record.updated_at = _now()
            self._write(record)
            return record.model_copy(deep=True)

    def record_resume_answers(
        self, job_id: str, answers: dict[str, Any]
    ) -> MapJobRecord:
        """Record only token and provided field names, never business values."""

        with self._lock:
            record = self.get(job_id)
            if record.status not in {"needs_user_input", "interrupted", "queued"}:
                raise ValueError(f"job cannot resume from status {record.status}")
            pending = {
                str(item.get("resume_token", "")): item
                for item in record.pending_questions
                if item.get("resume_token")
            }
            unknown = sorted(set(answers) - set(pending))
            if unknown:
                raise ValueError(f"unknown resume token: {', '.join(unknown)}")
            for token, answer in sorted(answers.items()):
                self._validate_resume_answer(pending[token], answer)
                fields = (
                    sorted(str(key) for key in answer)
                    if isinstance(answer, dict)
                    else []
                )
                record.resume_provenance.append(
                    {
                        "resume_token": token,
                        "provided": answer is not None,
                        "fields": fields,
                        "value_stored": False,
                    }
                )
            record.status = "queued"
            record.updated_at = _now()
            self._write(record)
            return record.model_copy(deep=True)

    @staticmethod
    def _validate_resume_answer(question: dict[str, Any], answer: Any) -> None:
        """Validate the HITL contract without retaining answer values."""

        required_names: list[str] = []
        for field in question.get("required_fields") or []:
            if isinstance(field, str):
                required_names.append(field)
            elif isinstance(field, dict):
                name = str(field.get("name") or field.get("field") or "").strip()
                if name:
                    required_names.append(name)
        if required_names and not isinstance(answer, dict):
            raise ValueError("resume answer must be an object")
        missing = [name for name in required_names if name not in answer]
        if missing:
            raise ValueError(f"resume answer missing required fields: {', '.join(missing)}")
        if "continue" in required_names and answer.get("continue") is not True:
            raise ValueError("resume answer continue must be true")
        if question.get("reason") != "guided_candidate_selection":
            if question.get("reason") != "semantic_review":
                return
            accepted = answer.get("accepted_candidate_ids")
            if not isinstance(accepted, list) or not all(
                isinstance(item, str) for item in accepted
            ):
                raise ValueError("semantic review requires accepted_candidate_ids as an array")
            allowed = {
                str(item.get("candidate_id", "")).strip()
                for item in question.get("choices") or []
                if isinstance(item, dict)
            }
            if any(item not in allowed for item in accepted):
                raise ValueError("semantic review selects an unknown candidate")
            return
        candidate_id = str(answer.get("candidate_id", "")).strip()
        allowed = {
            str(item.get("candidate_id", "")).strip()
            for item in question.get("choices") or []
            if isinstance(item, dict)
        }
        if not candidate_id or candidate_id not in allowed:
            raise ValueError("resume answer selects an unknown guided candidate")

    def request_cancel(self, job_id: str) -> MapJobRecord:
        with self._lock:
            record = self.get(job_id)
            if record.status in _TERMINAL:
                return record
            record.status = "cancelled"
            record.updated_at = _now()
            self._write(record)
            return record.model_copy(deep=True)

    def fail(self, job_id: str, error: str) -> MapJobRecord:
        with self._lock:
            record = self.get(job_id)
            self._ensure_mutable(record)
            record.status = "failed"
            normalized = str(error or "map_job_failed").strip()
            record.error = (
                normalized
                if len(normalized) <= 80
                and all(
                    char.isascii() and (char.isalnum() or char in "_-.: ")
                    for char in normalized
                )
                else "map_job_failed"
            )
            record.updated_at = _now()
            self._write(record)
            return record.model_copy(deep=True)

    def recover_interrupted(self) -> list[MapJobRecord]:
        """Mark dirty write-ahead checkpoints left by a crashed process."""

        recovered: list[MapJobRecord] = []
        with self._lock:
            for record in self.list(status="running"):
                if record.checkpoint is None or not record.checkpoint.in_flight.get("dirty"):
                    continue
                record.status = "interrupted"
                record.stop_reason = "dirty_checkpoint_recovery_required"
                record.updated_at = _now()
                self._write(record)
                recovered.append(record.model_copy(deep=True))
        return recovered

    def reconcile_interrupted(
        self, job_id: str, resolution: str
    ) -> MapJobRecord:
        """Resolve a dirty checkpoint without inferring the SAP action outcome."""

        with self._lock:
            record = self.get(job_id)
            if record.status != "interrupted":
                raise ValueError("only interrupted map jobs can be reconciled")
            normalized = str(resolution or "").strip().lower()
            if normalized == "cancel":
                record.status = "cancelled"
            elif normalized == "verified_not_applied":
                if record.checkpoint is None or not record.checkpoint.in_flight.get(
                    "dirty"
                ):
                    raise ValueError("map job has no dirty checkpoint")
                record.checkpoint = record.checkpoint.model_copy(
                    update={"in_flight": {}}, deep=True
                )
                record.resume_provenance.append(
                    {
                        "kind": "dirty_checkpoint_reconciliation",
                        "resolution": "verified_not_applied",
                        "value_stored": False,
                    }
                )
                record.status = "queued"
                record.stop_reason = ""
            else:
                raise ValueError(
                    "resolution must be verified_not_applied or cancel"
                )
            record.updated_at = _now()
            self._write(record)
            return record.model_copy(deep=True)

    @staticmethod
    def _question_summaries(
        checkpoint: TraversalCheckpoint,
    ) -> list[dict[str, Any]]:
        return [
            {
                "resume_token": item.resume_token,
                "question": item.question,
                "reason": item.reason,
                "required_fields": item.required_fields,
                "choices": item.choices,
                "risk": item.risk,
                "scope": item.scope,
            }
            for item in checkpoint.needs_user_input
        ]

    @staticmethod
    def _ensure_mutable(record: MapJobRecord) -> None:
        if record.status in _TERMINAL:
            raise ValueError(f"terminal map job cannot be changed: {record.status}")

    def _path(self, job_id: str) -> Path:
        if not _SAFE_ID.fullmatch(job_id):
            raise ValueError("invalid map job id")
        path = (self.root / f"{job_id}.json").resolve()
        if path.parent != self.root:
            raise ValueError("map job path escapes store root")
        return path

    def _write(self, record: MapJobRecord) -> None:
        path = self._path(record.job_id)
        atomic_write_json(path, record.model_dump(mode="json"), mode=0o600)


__all__ = ["BUDGET_STOP_REASONS", "JobStatus", "MapJobRecord", "MapJobStore"]
