"""CPU feasibility gate (Task 0).

Measures simulated time per wall-clock time and peak memory for the extracted
subcircuit, then decides whether the planned trial budget fits inside the
configured wall-clock limit. The result is written with the project
documentation so the subcircuit size and trial budget have a recorded basis.
"""

from __future__ import annotations

import gc
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

LOGGER = logging.getLogger(__name__)


class BenchmarkError(RuntimeError):
    """Raised when the benchmark cannot run."""


def _peak_rss_mb() -> float:
    try:
        import psutil

        return psutil.Process().memory_info().rss / 1024**2
    except ImportError:  # pragma: no cover
        return float("nan")


@dataclass
class BenchmarkResult:
    """Throughput and memory measurements for one configuration."""

    label: str
    n_neurons: int
    n_synapses: int
    stage_sizes: dict[str, int]
    simulated_ms: float
    wall_seconds: float
    peak_memory_mb: float
    ms_per_wall_second: float = 0.0
    steps_per_second: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "n_neurons": self.n_neurons,
            "n_synapses": self.n_synapses,
            "stage_sizes": self.stage_sizes,
            "simulated_ms": self.simulated_ms,
            "wall_seconds": self.wall_seconds,
            "ms_per_wall_second": self.ms_per_wall_second,
            "steps_per_second": self.steps_per_second,
            "peak_memory_mb": self.peak_memory_mb,
            "detail": self.detail,
        }


def measure_throughput(network, config, label: str = "network") -> BenchmarkResult:
    """Time a fixed simulated window and report throughput and peak memory."""
    import brian2 as b2

    simulated_ms = float(config.get("benchmark.simulated_ms"))
    settle_ms = float(config.get("benchmark.settle_ms"))
    dt = float(config.get("simulation.dt"))

    monitor = b2.SpikeMonitor(
        [group.group for group in network.stages.values()], record=False
    )
    network.brian.add(monitor)
    network.brian.run(settle_ms * b2.ms)
    # Spikes before the measured window must not be counted.
    from .network import clear_monitors

    clear_monitors([monitor])
    gc.collect()

    before = _peak_rss_mb()
    started = time.perf_counter()
    network.brian.run(simulated_ms * b2.ms)
    wall = time.perf_counter() - started
    peak = max(before, _peak_rss_mb())
    spikes = int(monitor.num_spikes)
    network.brian.remove(monitor)

    steps = simulated_ms / dt
    return BenchmarkResult(
        label=label,
        n_neurons=int(network.n_neurons),
        n_synapses=int(network.n_synapses),
        stage_sizes=network.stage_sizes(),
        simulated_ms=simulated_ms,
        wall_seconds=wall,
        peak_memory_mb=peak,
        ms_per_wall_second=(simulated_ms * 1e-3) / wall if wall > 0 else float("inf"),
        steps_per_second=steps / wall if wall > 0 else float("inf"),
        detail={
            "dt_ms": dt,
            "steps": steps,
            "spikes": spikes,
            "spikes_per_step": spikes / steps if steps else 0.0,
        },
    )


def project_trial_budget(
    benchmark: BenchmarkResult, config
) -> dict[str, Any]:
    """Project the wall-clock cost of one full experiment from the benchmark."""
    trial_ms = (
        float(config.get("encoding.stimulus_ms"))
        + float(config.get("encoding.rest_ms"))
    )
    n_trials = int(config.get("evaluation.n_train_trials"))
    eval_words = int(config.get("evaluation.test_per_category")) * len(
        config.get("encoding.categories")
    )
    eval_trials = eval_words * int(config.get("evaluation.eval_repeats"))
    n_seeds = int(config.get("evaluation.n_seeds"))
    n_conditions = len(config.get("evaluation.conditions"))

    trials_per_run = n_trials + eval_trials
    total_trials = trials_per_run * n_seeds * n_conditions
    # Scale the measured window linearly to one trial's simulated duration.
    # The benchmark measures simulated_ms of network time at a measured cost.
    measured_ms = benchmark.simulated_ms
    measured_wall = benchmark.wall_seconds
    per_trial_seconds = measured_wall * (trial_ms / measured_ms) if measured_ms else 0.0
    per_trial_ms = per_trial_seconds * 1000.0
    projected_seconds = total_trials * per_trial_seconds
    limit_minutes = float(config.get("benchmark.wall_clock_limit_min"))

    return {
        "trial_ms": trial_ms,
        "n_train_trials": n_trials,
        "n_eval_trials_per_seed": eval_trials,
        "n_seeds": n_seeds,
        "n_conditions": n_conditions,
        "total_trials": total_trials,
        "measured_ms_per_trial": per_trial_ms,
        "measured_window_ms": measured_ms,
        "measured_wall_seconds": measured_wall,
        "projected_seconds": projected_seconds,
        "projected_minutes": projected_seconds / 60.0,
        "wall_clock_limit_min": limit_minutes,
        "fits_budget": (projected_seconds / 60.0) <= limit_minutes,
        "note": (
            "Projection uses the measured cost of one simulated trial, applied "
            "to the full condition x seed sweep. Brian2 per-run call overhead is "
            "not fully represented, so treat this as a lower bound."
        ),
    }


def run_gate(subcircuit, config, weight_scale: float, candidates=None) -> dict[str, Any]:
    """Run the feasibility gate for the chosen subcircuit.

    Returns the gate result: whether the planned trial budget fits the
    wall-clock limit, plus the measured throughput and peak memory.
    """
    from .network import build_network

    results: list[dict[str, Any]] = []
    network = build_network(subcircuit, config, weight_scale=weight_scale)
    primary = measure_throughput(network, config, label=config.get("subcircuit.candidate"))
    results.append(primary.as_dict())

    projection = project_trial_budget(primary, config)
    budget_mb = float(config.get("resources.memory_budget_mb"))
    headroom = float(config.get("benchmark.memory_headroom"))
    memory_ok = primary.peak_memory_mb <= budget_mb * headroom

    verdict = bool(projection["fits_budget"] and memory_ok)
    LOGGER.info(
        "gate: %.0f neurons, %.0f synapses, %.2f ms simulated per wall second, "
        "projected %.1f min of a %.0f min budget, peak %.0f MB of %.0f MB -> %s",
        primary.n_neurons, primary.n_synapses, primary.ms_per_wall_second * 1000.0,
        projection["projected_minutes"], projection["wall_clock_limit_min"],
        primary.peak_memory_mb, budget_mb,
        "PASS" if verdict else "FAIL",
    )
    return {
        "verdict": "pass" if verdict else "fail",
        "fits_wall_clock": projection["fits_budget"],
        "within_memory": memory_ok,
        "subcircuit": {
            "n_neurons": primary.n_neurons,
            "n_synapses": primary.n_synapses,
            "stage_sizes": primary.stage_sizes,
        },
        "throughput": primary.as_dict(),
        "projection": projection,
        "memory_budget_mb": budget_mb,
        "memory_headroom": headroom,
        "candidates": results,
    }


def write_gate_result(result: dict[str, Any], path) -> None:
    import json
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    LOGGER.info("wrote benchmark gate result to %s", target)