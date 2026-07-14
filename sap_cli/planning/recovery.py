"""Conservative recovery policy for precompiled SAP GUI plans.

The module deliberately returns stable error codes instead of exception text.
Exception messages can contain business values, field contents, or other data
that must not leak into execution traces.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
import re
import time
from typing import Any

from sap_cli.ui_map.schema import SideEffectClass


class RecoveryErrorKind(str, Enum):
    """Safe, serializable failure classes used by the executor trace."""

    TRANSIENT_BUSY = "transient_busy"
    TRANSIENT_COM = "transient_com"
    LOCATOR_DRIFT = "locator_drift"
    DISCONNECTED = "disconnected"
    BUSINESS = "business"
    PERMANENT = "permanent"


# Only these two COM failures are safe to repeat.  All other COM errors are
# permanent unless another explicit classifier below proves otherwise.
_RETRYABLE_COM_HRESULTS = frozenset({0x80010001, 0x8001010A})
_DISCONNECTED_HRESULTS = frozenset({0x80010108, 0x800401FD, 0x800706BA})

_BUSY_PATTERN = re.compile(
    r"\b(?:sap\s+gui|session|application|server)?\s*(?:is\s+)?busy\b"
    r"|일시적으로\s*사용\s*중|세션이\s*사용\s*중",
    re.IGNORECASE,
)
_LOCATOR_PATTERN = re.compile(
    r"findbyid|element\s+(?:was\s+)?not\s+found|control\s+(?:was\s+)?not\s+found"
    r"|요소(?:를|가)?\s*찾을\s*수\s*없|컨트롤(?:을|이)?\s*찾을\s*수\s*없",
    re.IGNORECASE,
)
_DISCONNECTED_PATTERN = re.compile(
    r"not\s+connected|disconnected|connection\s+(?:is\s+)?closed"
    r"|연결되지\s*않|연결이\s*끊|연결이\s*닫",
    re.IGNORECASE,
)


def classify_execution_error(error: BaseException) -> RecoveryErrorKind:
    """Classify an exception and its cause/context chain without exposing text."""

    chain = tuple(_exception_chain(error))
    hresults = {
        normalized
        for exc in chain
        for value in _exception_integers(exc)
        if (normalized := _normalize_hresult(value)) is not None
    }

    # A dead session must never be retried, even when an outer wrapper has a
    # generic "busy" message.
    if hresults & _DISCONNECTED_HRESULTS:
        return RecoveryErrorKind.DISCONNECTED
    if any(_truthy_marker(exc, "disconnected", "connection_lost") for exc in chain):
        return RecoveryErrorKind.DISCONNECTED

    if hresults & _RETRYABLE_COM_HRESULTS:
        return RecoveryErrorKind.TRANSIENT_COM
    if any(_truthy_marker(exc, "busy", "is_busy", "retry_later") for exc in chain):
        return RecoveryErrorKind.TRANSIENT_BUSY

    messages = "\n".join(str(exc) for exc in chain)
    if _DISCONNECTED_PATTERN.search(messages):
        return RecoveryErrorKind.DISCONNECTED
    if _BUSY_PATTERN.search(messages):
        return RecoveryErrorKind.TRANSIENT_BUSY
    if _LOCATOR_PATTERN.search(messages):
        return RecoveryErrorKind.LOCATOR_DRIFT

    if any(_is_business_error(exc) for exc in chain):
        return RecoveryErrorKind.BUSINESS
    return RecoveryErrorKind.PERMANENT


def safe_error_code(error: BaseException) -> str:
    """Return the only error detail that may be persisted in a trace."""

    return classify_execution_error(error).value


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Compute a strict upper bound for execution attempts."""

    read_only_max_attempts: int = 3
    idempotent_max_attempts: int = 2
    default_max_attempts: int = 1

    def __post_init__(self) -> None:
        for value in (
            self.read_only_max_attempts,
            self.idempotent_max_attempts,
            self.default_max_attempts,
        ):
            if value < 1:
                raise ValueError("retry attempt limits must be at least one")

    def attempt_limit(
        self,
        *,
        side_effect: SideEffectClass | str,
        idempotent: bool = False,
        requested_attempts: int | Mapping[str, Any] | None = None,
        commit_boundary: bool = False,
        commit_attempted: bool = False,
    ) -> int:
        """Return the effective number of attempts, never a retry count."""

        effect = _coerce_side_effect(side_effect)
        if commit_attempted or commit_boundary or effect in {
            SideEffectClass.COMMIT,
            SideEffectClass.DESTRUCTIVE,
            SideEffectClass.EXTERNAL,
        }:
            return 1

        if effect is SideEffectClass.READ_ONLY:
            policy_limit = self.read_only_max_attempts
        elif idempotent:
            policy_limit = self.idempotent_max_attempts
        else:
            policy_limit = self.default_max_attempts

        requested = _requested_attempts(requested_attempts)
        return policy_limit if requested is None else min(policy_limit, requested)

    @staticmethod
    def retryable(kind: RecoveryErrorKind | str) -> bool:
        """Only transient busy and the two allowlisted COM errors are retryable."""

        try:
            normalized = RecoveryErrorKind(kind)
        except ValueError:
            return False
        return normalized in {
            RecoveryErrorKind.TRANSIENT_BUSY,
            RecoveryErrorKind.TRANSIENT_COM,
        }


def attempt_limit(
    *,
    side_effect: SideEffectClass | str,
    idempotent: bool = False,
    requested_attempts: int | Mapping[str, Any] | None = None,
    commit_boundary: bool = False,
    commit_attempted: bool = False,
    policy: RetryPolicy | None = None,
) -> int:
    """Functional convenience wrapper for executor integration."""

    return (policy or RetryPolicy()).attempt_limit(
        side_effect=side_effect,
        idempotent=idempotent,
        requested_attempts=requested_attempts,
        commit_boundary=commit_boundary,
        commit_attempted=commit_attempted,
    )


@dataclass(frozen=True, slots=True)
class BackoffPolicy:
    """Small capped exponential delay with an injectable sleeping seam."""

    initial_seconds: float = 0.05
    multiplier: float = 2.0
    max_seconds: float = 0.5

    def __post_init__(self) -> None:
        if self.initial_seconds < 0 or self.max_seconds < 0:
            raise ValueError("backoff durations cannot be negative")
        if self.multiplier < 1:
            raise ValueError("backoff multiplier must be at least one")

    def delay_for_retry(self, retry_number: int) -> float:
        """Return delay before retry 1, 2, ... (zero is accepted as no delay)."""

        if retry_number <= 0:
            return 0.0
        return min(
            self.max_seconds,
            self.initial_seconds * (self.multiplier ** (retry_number - 1)),
        )

    def wait(
        self,
        retry_number: int,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> float:
        delay = self.delay_for_retry(retry_number)
        if delay:
            sleep(delay)
        return delay


@dataclass(slots=True)
class RecoveryBudget:
    """Per-execution caps for expensive or state-changing recovery actions."""

    targeted_inspects: int = 1
    full_rescans: int = 1
    _used: dict[str, int] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.targeted_inspects < 0 or self.full_rescans < 0:
            raise ValueError("recovery budgets cannot be negative")

    def consume_targeted_inspect(self) -> bool:
        return self._consume("targeted_inspect", self.targeted_inspects)

    def consume_full_rescan(self) -> bool:
        return self._consume("full_rescan", self.full_rescans)

    def remaining(self, action: str) -> int:
        limits = {
            "targeted_inspect": self.targeted_inspects,
            "full_rescan": self.full_rescans,
        }
        if action not in limits:
            raise ValueError("unknown recovery action")
        return max(0, limits[action] - self._used.get(action, 0))

    def snapshot(self) -> dict[str, dict[str, int]]:
        """Return a value-free representation suitable for traces and tests."""

        return {
            action: {
                "used": self._used.get(action, 0),
                "remaining": self.remaining(action),
            }
            for action in ("targeted_inspect", "full_rescan")
        }

    def _consume(self, action: str, limit: int) -> bool:
        used = self._used.get(action, 0)
        if used >= limit:
            return False
        self._used[action] = used + 1
        return True


def _exception_chain(error: BaseException) -> Iterator[BaseException]:
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _exception_integers(error: BaseException) -> Iterator[int]:
    for name in ("hresult", "winerror", "scode"):
        value = getattr(error, name, None)
        if isinstance(value, int) and not isinstance(value, bool):
            yield value
    yield from _nested_integers(error.args)


def _nested_integers(value: Any) -> Iterator[int]:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        yield value
        return
    if isinstance(value, (tuple, list)):
        for item in value:
            yield from _nested_integers(item)


def _normalize_hresult(value: int) -> int | None:
    if not -(2**31) <= value <= 0xFFFFFFFF:
        return None
    return value & 0xFFFFFFFF


def _truthy_marker(error: BaseException, *names: str) -> bool:
    return any(getattr(error, name, False) is True for name in names)


def _is_business_error(error: BaseException) -> bool:
    if _truthy_marker(error, "business_error", "is_business_error"):
        return True
    status = getattr(error, "status_type", None) or getattr(error, "message_type", None)
    return isinstance(status, str) and status.upper() in {"E", "A"}


def _coerce_side_effect(value: SideEffectClass | str) -> SideEffectClass:
    try:
        return value if isinstance(value, SideEffectClass) else SideEffectClass(value)
    except ValueError:
        # Unknown effects never gain retries.
        return SideEffectClass.NAVIGATION


def _requested_attempts(value: int | Mapping[str, Any] | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        for key in ("max_attempts", "attempts", "count"):
            if key in value:
                value = value[key]
                break
        else:
            return None
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return 1
