"""Daemon-memory, one-time approval tokens for immutable execution plans."""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable


class ApprovalTokenError(RuntimeError):
    """Base error for opaque approval-token validation."""


class ApprovalTokenInvalidError(ApprovalTokenError):
    pass


class ApprovalTokenExpiredError(ApprovalTokenError):
    pass


class ApprovalTokenConsumedError(ApprovalTokenError):
    pass


@dataclass(frozen=True, slots=True)
class ApprovalReceipt:
    token: str
    expires_in_seconds: float


@dataclass(slots=True)
class _ApprovalRecord:
    plan_id: str
    plan_hash: str
    step_ids: tuple[str, ...]
    subject_agent: str
    slot_id: str
    expires_at: float
    consumed: bool = False


class PlanApprovalStore:
    """Owner-thread store whose raw tokens and grants are never serialized."""

    def __init__(
        self,
        *,
        max_tokens: int = 1024,
        default_ttl_seconds: float = 300.0,
        max_ttl_seconds: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] = lambda: secrets.token_urlsafe(32),
        owner_thread_id: int | None = None,
    ) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        if default_ttl_seconds <= 0 or max_ttl_seconds <= 0:
            raise ValueError("approval TTL must be positive")
        if default_ttl_seconds > max_ttl_seconds:
            raise ValueError("default approval TTL cannot exceed maximum")
        self.max_tokens = int(max_tokens)
        self.default_ttl_seconds = float(default_ttl_seconds)
        self.max_ttl_seconds = float(max_ttl_seconds)
        self._clock = clock
        self._token_factory = token_factory
        self._owner_thread_id = (
            threading.get_ident() if owner_thread_id is None else owner_thread_id
        )
        self._records: dict[str, _ApprovalRecord] = {}

    def issue(
        self,
        *,
        plan_id: str,
        plan_hash: str,
        step_ids: list[str] | tuple[str, ...],
        subject_agent: str,
        slot_id: str,
        ttl_seconds: float | None = None,
    ) -> ApprovalReceipt:
        self._assert_owner_thread()
        now = self._clock()
        self._purge_expired(now)
        if len(self._records) >= self.max_tokens:
            raise ApprovalTokenError("approval token capacity exhausted")
        steps = tuple(dict.fromkeys(self._required(item, "step_id") for item in step_ids))
        if not steps:
            raise ValueError("at least one approval step_id is required")
        ttl = self.default_ttl_seconds if ttl_seconds is None else float(ttl_seconds)
        if ttl <= 0 or ttl > self.max_ttl_seconds:
            raise ValueError("approval token TTL is outside the allowed range")
        raw_token = self._required(self._token_factory(), "approval token")
        token_hash = self._token_hash(raw_token)
        if token_hash in self._records:
            raise ApprovalTokenError("approval token collision")
        self._records[token_hash] = _ApprovalRecord(
            plan_id=self._required(plan_id, "plan_id"),
            plan_hash=self._required(plan_hash, "plan_hash"),
            step_ids=steps,
            subject_agent=self._required(subject_agent, "subject_agent"),
            slot_id=self._required(slot_id, "slot_id"),
            expires_at=now + ttl,
        )
        return ApprovalReceipt(token=raw_token, expires_in_seconds=ttl)

    def validate(
        self,
        token: str,
        *,
        plan_id: str,
        plan_hash: str,
        subject_agent: str,
        slot_id: str,
    ) -> frozenset[str]:
        """Validate without consuming; callers consume after job setup succeeds."""

        self._assert_owner_thread()
        record = self._resolve(token)
        self._assert_scope(
            record,
            plan_id=plan_id,
            plan_hash=plan_hash,
            subject_agent=subject_agent,
            slot_id=slot_id,
        )
        return frozenset(record.step_ids)

    def consume(
        self,
        token: str,
        *,
        plan_id: str,
        plan_hash: str,
        subject_agent: str,
        slot_id: str,
    ) -> frozenset[str]:
        self._assert_owner_thread()
        record = self._resolve(token)
        self._assert_scope(
            record,
            plan_id=plan_id,
            plan_hash=plan_hash,
            subject_agent=subject_agent,
            slot_id=slot_id,
        )
        record.consumed = True
        return frozenset(record.step_ids)

    def clear(self) -> None:
        self._assert_owner_thread()
        self._records.clear()

    def _resolve(self, token: str) -> _ApprovalRecord:
        raw_token = self._required(token, "approval_token")
        record = self._records.get(self._token_hash(raw_token))
        if record is None:
            raise ApprovalTokenInvalidError("invalid approval token")
        if record.consumed:
            raise ApprovalTokenConsumedError("approval token has already been consumed")
        if record.expires_at <= self._clock():
            raise ApprovalTokenExpiredError("approval token has expired")
        return record

    @staticmethod
    def _assert_scope(
        record: _ApprovalRecord,
        *,
        plan_id: str,
        plan_hash: str,
        subject_agent: str,
        slot_id: str,
    ) -> None:
        if (
            record.plan_id != str(plan_id)
            or record.plan_hash != str(plan_hash)
            or record.subject_agent != str(subject_agent)
            or record.slot_id != str(slot_id)
        ):
            raise ApprovalTokenInvalidError("approval token scope mismatch")

    def _purge_expired(self, now: float) -> None:
        for token_hash, record in list(self._records.items()):
            if record.expires_at <= now:
                self._records.pop(token_hash, None)

    def _assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise ApprovalTokenError("approval store must run on its daemon owner thread")

    @staticmethod
    def _required(value: object, name: str) -> str:
        normalized = str(value or "").strip()
        if not normalized or len(normalized) > 512 or any(ord(char) < 32 for char in normalized):
            raise ValueError(f"{name} is required or invalid")
        return normalized

    @staticmethod
    def _token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()


_store: PlanApprovalStore | None = None


def set_plan_approval_store(store: PlanApprovalStore | None) -> None:
    global _store
    _store = store


def get_plan_approval_store() -> PlanApprovalStore:
    if _store is None:
        raise RuntimeError("plan approval requires the persistent daemon runtime")
    return _store


def try_get_plan_approval_store() -> PlanApprovalStore | None:
    return _store


__all__ = [
    "ApprovalReceipt",
    "ApprovalTokenConsumedError",
    "ApprovalTokenError",
    "ApprovalTokenExpiredError",
    "ApprovalTokenInvalidError",
    "PlanApprovalStore",
    "get_plan_approval_store",
    "set_plan_approval_store",
    "try_get_plan_approval_store",
]
