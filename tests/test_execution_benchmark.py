from __future__ import annotations

import pytest

from sap_cli.benchmark import (
    BenchmarkHarness,
    BenchmarkMode,
    BenchmarkSample,
    StructuralCounters,
    nearest_rank_percentile,
)


def sample(
    mode: BenchmarkMode,
    duration: float,
    *,
    scans: int = 0,
    state_reads: int = 0,
    stages: dict[str, float] | None = None,
) -> BenchmarkSample:
    return BenchmarkSample(
        mode=mode,
        duration_ms=duration,
        counters=StructuralCounters(
            ipc_calls=1,
            python_initializations=1 if mode is BenchmarkMode.COLD_DIRECT else 0,
            com_initializations=1 if mode is BenchmarkMode.COLD_DIRECT else 0,
            com_state_reads=state_reads,
            full_screen_scans=scans,
            sap_actions=2,
        ),
        stage_durations_ms=stages or {},
    )


def test_nearest_rank_p50_and_p95_are_non_interpolated() -> None:
    values = list(range(1, 21))

    assert nearest_rank_percentile(values, 0.50) == 10
    assert nearest_rank_percentile(values, 0.95) == 19
    assert nearest_rank_percentile([7], 0.95) == 7


def test_harness_aggregates_in_canonical_mode_order() -> None:
    samples = []
    for mode in reversed(tuple(BenchmarkMode)):
        samples.extend(
            [
                sample(mode, 20, scans=0, state_reads=2),
                sample(mode, 10, scans=0, state_reads=4),
            ]
        )

    report = BenchmarkHarness(samples).run()

    assert [result.mode for result in report.results] == list(BenchmarkMode)
    compiled = report.result_for("compiled_batch")
    assert compiled.sample_count == 2
    assert compiled.minimum_ms == 10
    assert compiled.maximum_ms == 20
    assert compiled.mean_ms == 15
    assert compiled.p50_ms == 10
    assert compiled.p95_ms == 20
    assert compiled.counters_total.com_state_reads == 6
    assert compiled.counters_total.full_screen_scans == 0
    assert compiled.counters_per_sample["sap_actions"] == 2


def test_stage_percentiles_use_injected_durations_only() -> None:
    harness = BenchmarkHarness(
        [
            sample(mode, 10 + index, stages={"compile": index, "execute": index + 5})
            for mode in BenchmarkMode
            for index in range(1, 21)
        ]
    )

    result = harness.run().result_for(BenchmarkMode.WARM_DAEMON)

    assert result.stage_p50_ms == {"compile": 10, "execute": 15}
    assert result.stage_p95_ms == {"compile": 19, "execute": 24}


def test_harness_requires_every_comparison_mode_by_default() -> None:
    harness = BenchmarkHarness([sample(BenchmarkMode.COLD_DIRECT, 1)])

    with pytest.raises(ValueError, match="warm_daemon.*recipe_replay.*compiled_batch"):
        harness.run()

    report = harness.run(require_all_modes=False)
    assert report.result_for("cold_direct").sample_count == 1


@pytest.mark.parametrize(
    "counters",
    [StructuralCounters(ipc_calls=0), StructuralCounters(full_screen_scans=0)],
)
def test_structural_counters_are_value_objects(counters: StructuralCounters) -> None:
    assert counters.to_dict()["ipc_calls"] >= 0


def test_invalid_samples_and_counters_fail_before_aggregation() -> None:
    with pytest.raises(ValueError, match="non-negative integer"):
        StructuralCounters(ipc_calls=-1)
    with pytest.raises(ValueError, match="duration_ms"):
        BenchmarkSample(mode=BenchmarkMode.WARM_DAEMON, duration_ms=-0.1)
    with pytest.raises(ValueError, match="at least one"):
        nearest_rank_percentile([], 0.50)
