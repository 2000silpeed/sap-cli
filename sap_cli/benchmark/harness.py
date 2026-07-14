"""Pure aggregation for injected benchmark observations."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Iterable, Sequence

from .schema import (
    BenchmarkMode,
    BenchmarkReport,
    BenchmarkSample,
    ModeBenchmark,
    StructuralCounters,
)


def nearest_rank_percentile(values: Sequence[float], percentile: float) -> float:
    """Return a percentile using the nearest-rank definition.

    Nearest rank uses ``ceil(percentile * n)`` with a one-based rank.  There is
    deliberately no interpolation, which keeps reports identical across
    implementations and platforms.
    """

    if not values:
        raise ValueError("at least one value is required")
    if percentile <= 0 or percentile > 1:
        raise ValueError("percentile must be in the interval (0, 1]")
    ordered = sorted(float(value) for value in values)
    rank = math.ceil(percentile * len(ordered))
    return ordered[rank - 1]


class BenchmarkHarness:
    """Aggregate injected samples without performing benchmark side effects."""

    def __init__(self, samples: Iterable[BenchmarkSample] = ()) -> None:
        self._samples = list(samples)

    def add(self, sample: BenchmarkSample) -> None:
        if not isinstance(sample, BenchmarkSample):
            raise TypeError("sample must be a BenchmarkSample")
        self._samples.append(sample)

    def run(self, *, require_all_modes: bool = True) -> BenchmarkReport:
        grouped: dict[BenchmarkMode, list[BenchmarkSample]] = defaultdict(list)
        for sample in self._samples:
            grouped[sample.mode].append(sample)

        if require_all_modes:
            missing = [mode.value for mode in BenchmarkMode if not grouped[mode]]
            if missing:
                raise ValueError(f"missing benchmark modes: {', '.join(missing)}")

        results = tuple(
            self._summarize(mode, grouped[mode])
            for mode in BenchmarkMode
            if grouped[mode]
        )
        return BenchmarkReport(results=results)

    @staticmethod
    def _summarize(
        mode: BenchmarkMode, samples: Sequence[BenchmarkSample]
    ) -> ModeBenchmark:
        durations = [float(sample.duration_ms) for sample in samples]
        counters = StructuralCounters()
        stages: dict[str, list[float]] = defaultdict(list)
        for sample in samples:
            counters = counters + sample.counters
            for name, duration in sample.stage_durations_ms.items():
                stages[name].append(float(duration))

        stage_names = sorted(stages)
        return ModeBenchmark(
            mode=mode,
            sample_count=len(samples),
            minimum_ms=min(durations),
            maximum_ms=max(durations),
            mean_ms=sum(durations) / len(durations),
            p50_ms=nearest_rank_percentile(durations, 0.50),
            p95_ms=nearest_rank_percentile(durations, 0.95),
            counters_total=counters,
            counters_per_sample=counters.divided_by(len(samples)),
            stage_p50_ms={
                name: nearest_rank_percentile(stages[name], 0.50)
                for name in stage_names
            },
            stage_p95_ms={
                name: nearest_rank_percentile(stages[name], 0.95)
                for name in stage_names
            },
        )
