"""Value objects used by deterministic execution benchmarks.

The benchmark package intentionally does not open sockets, connect to SAP, or
read a wall clock.  A Windows canary (or a test) supplies measured samples and
this module gives those samples a stable, serializable shape.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


class BenchmarkMode(StrEnum):
    """Execution paths compared by the Phase 8 performance gate."""

    COLD_DIRECT = "cold_direct"
    WARM_DAEMON = "warm_daemon"
    RECIPE_REPLAY = "recipe_replay"
    COMPILED_BATCH = "compiled_batch"


@dataclass(frozen=True, slots=True)
class StructuralCounters:
    """Machine-independent work performed during one benchmark sample."""

    ipc_calls: int = 0
    python_initializations: int = 0
    com_initializations: int = 0
    com_state_reads: int = 0
    full_screen_scans: int = 0
    sap_actions: int = 0

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def __add__(self, other: object) -> "StructuralCounters":
        if not isinstance(other, StructuralCounters):
            return NotImplemented
        return StructuralCounters(
            **{
                name: value + getattr(other, name)
                for name, value in asdict(self).items()
            }
        )

    def divided_by(self, count: int) -> Mapping[str, float]:
        if count < 1:
            raise ValueError("count must be positive")
        return MappingProxyType(
            {name: value / count for name, value in asdict(self).items()}
        )

    def to_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class BenchmarkSample:
    """One externally measured run supplied to the aggregation harness."""

    mode: BenchmarkMode
    duration_ms: float
    counters: StructuralCounters = field(default_factory=StructuralCounters)
    stage_durations_ms: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", BenchmarkMode(self.mode))
        if isinstance(self.duration_ms, bool) or self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        stages = dict(self.stage_durations_ms)
        for name, duration in stages.items():
            if not name or isinstance(duration, bool) or duration < 0:
                raise ValueError("stage names must be set and durations non-negative")
        object.__setattr__(self, "stage_durations_ms", MappingProxyType(stages))


@dataclass(frozen=True, slots=True)
class ModeBenchmark:
    """Deterministic aggregate for one execution mode."""

    mode: BenchmarkMode
    sample_count: int
    minimum_ms: float
    maximum_ms: float
    mean_ms: float
    p50_ms: float
    p95_ms: float
    counters_total: StructuralCounters
    counters_per_sample: Mapping[str, float]
    stage_p50_ms: Mapping[str, float] = field(default_factory=dict)
    stage_p95_ms: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", BenchmarkMode(self.mode))
        object.__setattr__(
            self, "counters_per_sample", MappingProxyType(dict(self.counters_per_sample))
        )
        object.__setattr__(self, "stage_p50_ms", MappingProxyType(dict(self.stage_p50_ms)))
        object.__setattr__(self, "stage_p95_ms", MappingProxyType(dict(self.stage_p95_ms)))


@dataclass(frozen=True, slots=True)
class BenchmarkReport:
    """Aggregates in canonical mode order."""

    results: tuple[ModeBenchmark, ...]

    def result_for(self, mode: BenchmarkMode | str) -> ModeBenchmark:
        wanted = BenchmarkMode(mode)
        for result in self.results:
            if result.mode == wanted:
                return result
        raise KeyError(wanted.value)
