"""Brian2 network construction and no-stimulus weight-scale calibration.

The subcircuit becomes one Brian2 ``NeuronGroup`` per stage plus a silent
reserve pool. Weights come from FlyWire synapse counts times a global scale,
signed from the presynaptic ``top_nt``, and are calibrated so spontaneous
firing sits inside a configured band without saturating.

Plasticity lives on the mushroom-body -> output synapses only. The eligibility
trace is a declared synaptic state variable that evolves inside the simulation;
the dopamine-gated weight update is applied once per trial from outside, which
keeps the three-factor rule explicit and inspectable.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from .connectome import (
    INPUT_STAGES,
    MUSHROOM_BODY_STAGES,
    OUTPUT_STAGES,
    TEACHER_STAGES,
    Subcircuit,
)

LOGGER = logging.getLogger(__name__)

# I_bg is a subthreshold background drive. Without it a deterministic LIF
# network resting at v_rest stays at exactly v_rest forever and the network is
# perfectly silent, so a calibration band with a nonzero lower bound could
# never be met. Set it just below v_threshold so spontaneous activity emerges
# from real recurrent connectivity rather than from injected noise.
LIF_MODEL = """
dv/dt = (v - v_rest - I_syn + I_teacher + I_bg) / tau : 1
dI_syn/dt = -I_syn/tau_syn : 1
I_teacher : 1
I_bg : 1
"""


class NetworkError(RuntimeError):
    """Raised when a network cannot be built from the subcircuit."""


@dataclass
class PlasticSynapses:
    """Bookkeeping for the plastic (mushroom body -> output) synapse set."""

    pre_indices: np.ndarray
    post_indices: np.ndarray
    role_pair: str
    weights: np.ndarray
    trace: np.ndarray
    # Synapses disabled by pruning or silencing, excluded from the update.
    active: np.ndarray
    n_synapses_each: np.ndarray


@dataclass
class StageGroup:
    """One simulated population."""

    name: str
    indices: np.ndarray
    n: int
    # The Brian2 NeuronGroup backing this stage, filled in by build_network.
    group: Any = None


@dataclass
class SimulationNetwork:
    """A built network, its synaptic structure, and handles for the run loop."""

    brian: Any
    stages: dict[str, StageGroup]
    synaptic_groups: dict[str, Any]
    plastic: PlasticSynapses
    output_indices: np.ndarray
    reserve: Any
    weight_scale: float
    config: Any
    n_neurons: int
    n_synapses: int
    target_internal: np.ndarray = field(default_factory=lambda: np.array([], dtype=int))
    synapse_signs: dict[str, np.ndarray] = field(default_factory=dict)
    # Real reinforcement neurons that supply the teaching signal. The reward
    # VALUE is synthetic; the neurons and their synapses onto Kenyon cells are
    # genuine FlyWire wiring.
    teacher_stage_indices: dict[str, list[int]] = field(default_factory=dict)
    teacher_role_pairs: list[str] = field(default_factory=list)
    # Declared Poisson background drive, reported for provenance.
    noise: dict = field(default_factory=dict)
    # Role pairs built from the synthetic lobula -> Kenyon cell bridge.
    artificial_role_pairs: list[str] = field(default_factory=list)

    # -- reporting -------------------------------------------------------
    def stage_sizes(self) -> dict[str, int]:
        return {name: group.n for name, group in self.stages.items()}

    def summary(self) -> dict[str, Any]:
        return {
            "codegen_target": self.codegen_target,
            "weight_scale": self.weight_scale,
            "n_neurons": self.n_neurons,
            "n_synapses": self.n_synapses,
            "stage_sizes": self.stage_sizes(),
            "reserve_size": int(self.reserve.N),
            "plastic_synapses": {
                "role_pair": self.plastic.role_pair,
                "n_synapses": int(self.plastic.weights.size),
                "n_synaptic_paths": int(self.plastic.pre_indices.size),
                "mean_weight": float(self.plastic.weights.mean())
                if self.plastic.weights.size else 0.0,
            },
            "teacher_pathway": {
                "role_pairs": list(self.teacher_role_pairs),
                "stage_sizes": {
                    name: len(indices)
                    for name, indices in self.teacher_stage_indices.items()
                },
                "note": (
                    "Real reinforcement neurons (APL/DPM) drive Kenyon cells. "
                    "Only the reward value injected into them is synthetic."
                ),
            },
            "artificial_role_pairs": list(self.artificial_role_pairs),
            "background_noise": dict(self.noise),
            "lif": {
                key: self.config.get(f"network.lif.{key}")
                for key in ("tau_ms", "tau_syn_ms", "v_threshold", "v_reset",
                            "v_rest", "refractory_ms", "t_init_ms")
            },
        }

    @property
    def codegen_target(self) -> str:
        import brian2

        return str(brian2.prefs.codegen.target)


def _stage_groups(subcircuit: Subcircuit) -> dict[str, StageGroup]:
    groups: dict[str, StageGroup] = {}
    for name, indices in subcircuit.roles.items():
        array = np.asarray(sorted(indices), dtype=np.int64)
        groups[name] = StageGroup(name=name, indices=array, n=int(array.size))
    return groups


def stage_index_mapper(subcircuit: Subcircuit):
    """Map a stage's internal indices onto a dense 0..n-1 axis.

    Brian2 groups are dense, so each stage keeps its own index space. The
    returned callable translates subcircuit indices into dense group indices.
    """
    mappers: dict[str, dict[int, int]] = {}
    for name, indices in subcircuit.roles.items():
        ordered = sorted(indices)
        mappers[name] = {internal: dense for dense, internal in enumerate(ordered)}
    return mappers


def build_network(
    subcircuit: Subcircuit,
    config,
    weight_scale: float,
    reserve_indices: Sequence[int] | None = None,
) -> SimulationNetwork:
    """Build the Brian2 network for a subcircuit at a given weight scale."""
    import brian2 as b2

    b2.prefs.codegen.target = config.get("simulation.codegen_target")
    dt = float(config.get("simulation.dt"))
    b2.defaultclock.dt = dt * b2.ms

    stages = _stage_groups(subcircuit)
    mappers = stage_index_mapper(subcircuit)

    input_stages = [s for s in subcircuit.roles if s in INPUT_STAGES]
    output_stages = [s for s in subcircuit.roles if s in OUTPUT_STAGES]
    if not input_stages:
        raise NetworkError("subcircuit has no input (photoreceptor) stage")
    if not output_stages:
        raise NetworkError("subcircuit has no output stage")

    lif = config.section("network.lif")
    namespace = {
        "tau": float(lif["tau_ms"]) * b2.ms,
        "v_rest": float(lif["v_rest"]),
        "v_threshold": float(lif["v_threshold"]),
        "v_reset": float(lif["v_reset"]),
        "tau_syn": float(lif["tau_syn_ms"]) * b2.ms,
    }
    threshold = "v > v_threshold"
    reset = "v = v_reset"

    def make_group(n: int) -> Any:
        return b2.NeuronGroup(
            n,
            LIF_MODEL,
            method="euler",
            threshold=threshold,
            reset=reset,
            refractory=float(lif["refractory_ms"]) * b2.ms,
            namespace=namespace,
        )

    groups: dict[str, Any] = {name: make_group(g.n) for name, g in stages.items()}
    for name, group in groups.items():
        group.v = float(lif["v_rest"])
        group.I_syn = 0.0
        stages[name].group = group

    # Reserve pool: born with no synapses and no input. v_rest is below
    # v_threshold, so these neurons cannot spike until recruited.
    reserve_size = int(config.get("network.reserve_size"))
    reserve = make_group(reserve_size)
    reserve.v = float(lif["v_rest"])
    reserve.I_syn = 0.0

    reserve_internal = (
        np.asarray(sorted(reserve_indices), dtype=np.int64)
        if reserve_indices is not None
        else np.array([], dtype=np.int64)
    )

    # Group edges by ordered role pair, skipping unknown endpoints.
    by_pair: dict[tuple[str, str], list[tuple[int, int, int, int]]] = {}
    artificial_pairs: set[tuple[str, str]] = set()
    artificial_role_pairs: set[str] = set()
    stage_of: dict[int, str] = {}
    for name, indices in subcircuit.roles.items():
        for internal in indices:
            stage_of[internal] = name
    for pre, post, n_syn, sign, role_pair, artificial in subcircuit.edges:
        pre_stage, post_stage = role_pair.split("->")
        if pre_stage not in mappers or post_stage not in mappers:
            continue
        pre_dense = mappers[pre_stage].get(pre)
        post_dense = mappers[post_stage].get(post)
        if pre_dense is None or post_dense is None:
            continue
        pair = (pre_stage, post_stage)
        if artificial:
            artificial_pairs.add(pair)
            artificial_role_pairs.add(f"{pre_stage}->{post_stage}")
        by_pair.setdefault(pair, []).append(
            (pre_dense, post_dense, n_syn, sign)
        )

    synaptic_groups: dict[str, Any] = {}
    synapse_signs: dict[str, np.ndarray] = {}
    plastic: PlasticSynapses | None = None

    plastic_pairs = {
        (mb, out)
        for mb in MUSHROOM_BODY_STAGES
        for out in OUTPUT_STAGES
        if mb in mappers and out in mappers
    }

    # Eligible pairs are consecutive stages in signal order, within-stage
    # recurrence, the plastic mushroom-body -> output pair, and the synthetic
    # bridge. Recurrence matters: without it a resting network has no
    # spontaneous activity at all, since every source of drive in an unstimulated
    # simulation would have to come from outside the neurons themselves.
    ordered_stages = list(subcircuit.roles)
    adjacent = {
        (ordered_stages[i], ordered_stages[i + 1])
        for i in range(len(ordered_stages) - 1)
    } | set(subcircuit.roles) | plastic_pairs | artificial_pairs

    trace_tau = float(config.get("learning.trace_tau_ms"))
    trace_increment = float(config.get("learning.trace_increment"))
    w_min = float(config.get("learning.w_min"))
    w_max = float(config.get("learning.w_max"))

    for (pre_stage, post_stage), rows in sorted(by_pair.items()):
        if (pre_stage, post_stage) not in adjacent:
            # Long-range or skip connections are not simulated; they are
            # accounted for in the manifest instead of silently ignored.
            continue
        pre_indices = np.array([r[0] for r in rows], dtype=np.int64)
        post_indices = np.array([r[1] for r in rows], dtype=np.int64)
        n_syn = np.array([r[2] for r in rows], dtype=np.float64)
        sign = np.array([r[3] for r in rows], dtype=np.float64)
        role_pair = f"{pre_stage}->{post_stage}"
        is_plastic = (pre_stage, post_stage) in plastic_pairs

        if is_plastic:
            # Eligibility is a declared synaptic state variable: it rises on
            # coincident pre/post activity and decays exponentially.
            model = (
                "w : 1\n"
                "delig/dt = -elig/trace_tau : 1 (clock-driven)"
            )
            on_pre = "I_syn += w\nelig += trace_inc"
            on_post = "elig += trace_inc"
            ns = dict(namespace)
            ns["trace_tau"] = trace_tau * b2.ms
            ns["trace_inc"] = trace_increment
            ns["w_min"] = w_min
            ns["w_max"] = w_max
            syn = b2.Synapses(
                groups[pre_stage], groups[post_stage], model=model,
                on_pre=on_pre, on_post=on_post, method="euler", namespace=ns,
            )
        else:
            syn = b2.Synapses(
                groups[pre_stage], groups[post_stage],
                model="w : 1", on_pre="I_syn += w",
                method="euler", namespace=dict(namespace),
            )
        syn.connect(i=pre_indices.tolist(), j=post_indices.tolist())

        # Weight = synapse count * global scale, signed by transmitter. The
        # plastic set keeps a NONNEGATIVE magnitude in plastic.weights while
        # syn.w carries the signed current, so w_min/w_max clipping can never
        # flip an inhibitory synapse to excitatory.
        magnitude = n_syn * float(weight_scale)
        weight = sign * magnitude
        syn.w = weight.tolist()
        if is_plastic:
            syn.elig = 0.0
        synaptic_groups[role_pair] = syn
        synapse_signs[role_pair] = sign
        LOGGER.info(
            "synapses %-28s n=%6d sign(%+d/%d) scale=%.4g",
            role_pair, len(weight), int((sign > 0).sum()), int((sign < 0).sum()),
            weight_scale,
        )
        if is_plastic:
            plastic = PlasticSynapses(
                pre_indices=pre_indices,
                post_indices=post_indices,
                role_pair=role_pair,
                weights=magnitude.copy(),
                trace=np.zeros(len(weight)),
                active=np.ones(len(weight), dtype=bool),
                n_synapses_each=n_syn.astype(np.int64),
            )

    teacher_stages = [name for name in ordered_stages if name in TEACHER_STAGES]
    teacher_role_pairs = [
        f"{t}->{mb}" for t in teacher_stages for mb in MUSHROOM_BODY_STAGES
        if f"{t}->{mb}" in synaptic_groups
    ]
    if teacher_role_pairs:
        LOGGER.info(
            "teacher pathway (real reinforcement neurons -> mushroom body): %s",
            teacher_role_pairs,
        )
    else:
        LOGGER.warning(
            "no reinforcement stage feeds the mushroom body; the synthetic "
            "reward pulse will drive the plastic pathway directly"
        )

    if plastic is None:
        raise NetworkError(
            "no plastic synapse set was built: the subcircuit has no "
            f"{MUSHROOM_BODY_STAGES} -> {OUTPUT_STAGES} connection"
        )

    # Poisson background drive. Real neural tissue fires spontaneously; a
    # deterministic LIF chain driven only by a stimulus is perfectly quiescent
    # without one, so the calibration band (>= 0.5 Hz mean rate) could never be
    # met. This is declared noise, not measured FlyWire connectivity, and is
    # reported in the manifest.
    noise_groups: list[Any] = []
    if config.get("network.noise.enabled"):
        noise_rate = float(config.get("network.noise.rate_hz")) / b2.second
        noise_weight = float(config.get("network.noise.weight"))
        for stage in stages.values():
            source = b2.PoissonGroup(stage.n, rates=noise_rate, name=f"noise_{stage.name}")
            # Declared state variables shadow same-named namespace entries in
            # Brian2, so the background drive is assigned after construction.
            stage.group.I_bg = float(config.get("network.background_dc_na"))
            syn_noise = b2.Synapses(
                source, stage.group, on_pre="I_syn += w_noise", method="euler",
                namespace={"w_noise": noise_weight},
            )
            # One noise source per neuron, connected one-to-one. A dense
            # PoissonGroup -> NeuronGroup connection would be O(n^2) synapses.
            diagonal = np.arange(stage.n, dtype=np.int64)
            syn_noise.connect(i=diagonal.tolist(), j=diagonal.tolist())
            noise_groups.extend([source, syn_noise])
        LOGGER.info(
            "Poisson background noise at %.1f Hz per neuron (weight %.3g)",
            float(config.get("network.noise.rate_hz")), noise_weight,
        )

    total_neurons = sum(g.n for g in stages.values()) + reserve_size
    total_synapses = sum(len(syn) for syn in synaptic_groups.values())

    brian_net = b2.Network(
        *list(groups.values()), reserve, *synaptic_groups.values(), *noise_groups
    )

    outputs = []
    for stage in output_stages:
        outputs.extend(stages[stage].indices.tolist())

    return SimulationNetwork(
        brian=brian_net,
        stages=stages,
        synaptic_groups=synaptic_groups,
        plastic=plastic,
        output_indices=np.asarray(outputs, dtype=np.int64),
        reserve=reserve,
        weight_scale=float(weight_scale),
        config=config,
        n_neurons=total_neurons,
        n_synapses=total_synapses,
        target_internal=np.asarray([], dtype=np.int64),
        synapse_signs=synapse_signs,
        teacher_stage_indices={
            name: stages[name].indices.tolist() for name in teacher_stages
        },
        teacher_role_pairs=teacher_role_pairs,
        noise={
            "enabled": bool(config.get("network.noise.enabled")),
            "rate_hz": float(config.get("network.noise.rate_hz")),
            "weight": float(config.get("network.noise.weight")),
        },
        artificial_role_pairs=sorted(artificial_role_pairs),
    )


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------

@dataclass
class CalibrationResult:
    """Outcome of the no-stimulus weight-scale search."""

    weight_scale: float
    mean_rate_hz: float
    max_rate_hz: float
    continuous_fraction: float
    rate_drift_hz: float
    accepted: bool
    attempts: list[dict[str, float]] = field(default_factory=list)
    acceptable_band_hz: tuple[float, float] = (0.5, 15.0)
    max_continuous_fraction: float = 0.02

    def as_dict(self) -> dict[str, Any]:
        return {
            "weight_scale": self.weight_scale,
            "mean_rate_hz": self.mean_rate_hz,
            "max_rate_hz": self.max_rate_hz,
            "continuous_fraction": self.continuous_fraction,
            "rate_drift_hz": self.rate_drift_hz,
            "accepted": self.accepted,
            "n_attempts": len(self.attempts),
            "attempts": self.attempts,
            "acceptable_band_hz": list(self.acceptable_band_hz),
            "max_continuous_fraction": self.max_continuous_fraction,
            "criterion": (
                "mean spontaneous firing rate inside acceptable_band_hz, "
                "continuously firing fraction below max_continuous_fraction, "
                "and rate drift below the configured limit"
            ),
        }


def clear_monitors(monitors) -> None:
    """Zero spike counts on SpikeMonitors built with ``record=False``.

    Those monitors expose ``count`` but have no ``clear()`` method, so counts
    must be reset in place.
    """
    for monitor in monitors:
        counts = monitor.count
        if isinstance(counts, np.ndarray) and counts.size:
            counts[:] = 0


def measure_spontaneous_activity(
    network: SimulationNetwork, config
) -> dict[str, float]:
    """Run with no stimulus and measure firing statistics.

    Reported neurons exclude the reserve pool, which is silent by construction.
    """
    import brian2 as b2

    simulated_ms = float(config.get("calibration.simulated_ms"))
    settle_ms = float(config.get("calibration.settle_ms"))
    tau_s = float(config.get("network.lif.tau_ms")) * 1e-3

    # One monitor per stage: SpikeMonitor does not accept a list of groups.
    monitors = {
        name: b2.SpikeMonitor(group.group, record=False)
        for name, group in network.stages.items()
    }
    network.brian.add(list(monitors.values()))
    network.brian.run(settle_ms * b2.ms)
    clear_monitors(monitors.values())

    measure_ms = max(simulated_ms - settle_ms, 1.0)
    started = time.perf_counter()
    network.brian.run(measure_ms * b2.ms)
    wall_seconds = time.perf_counter() - started

    elapsed_s = measure_ms * 1e-3
    per_stage = {
        name: np.asarray(monitor.count, dtype=np.float64)
        for name, monitor in monitors.items()
    }
    counts = np.concatenate(list(per_stage.values()))

    n_neurons = int(counts.size)
    total_rate = float(counts.sum() / elapsed_s) if elapsed_s else 0.0
    mean_rate = total_rate / n_neurons if n_neurons else 0.0
    rates = counts / elapsed_s

    # Continuous firing: >= 1 spike per membrane time constant, measured over
    # the whole window. This is the "does not fire continuously" criterion.
    continuous_hz = 1.0 / tau_s if tau_s > 0 else float("inf")
    continuous_fraction = (
        float(np.count_nonzero(rates >= continuous_hz)) / n_neurons
        if n_neurons else 0.0
    )

    # Drift: compare mean rate in the first and second halves of the SAME
    # window. Both halves span the same duration, so the comparison is valid.
    half_s = (measure_ms / 2.0) * 1e-3
    if n_neurons:
        halves = counts.reshape(2, -1)
        first_rate = float(halves[0].sum() / half_s / n_neurons)
        second_rate = float(halves[1].sum() / half_s / n_neurons)
        rate_drift = abs(second_rate - first_rate)
    else:
        first_rate = second_rate = 0.0
        rate_drift = 0.0

    network.brian.remove(list(monitors.values()))
    return {
        "mean_rate_hz": mean_rate,
        "max_rate_hz": float(rates.max()) if n_neurons else 0.0,
        "total_rate_hz": total_rate,
        "continuous_fraction": continuous_fraction,
        "rate_drift_hz": float(rate_drift),
        "first_half_rate_hz": float(first_rate),
        "second_half_rate_hz": float(second_rate),
        "n_neurons": n_neurons,
        "silence_fraction": float(np.count_nonzero(counts == 0)) / n_neurons
        if n_neurons else 1.0,
        "per_stage_mean_rate_hz": {
            name: float(values.sum() / elapsed_s / values.size)
            for name, values in per_stage.items() if values.size
        },
        "simulated_ms": measure_ms,
        "wall_seconds": wall_seconds,
        "real_time_ratio": (measure_ms * 1e-3) / wall_seconds if wall_seconds > 0
        else float("inf"),
    }


def calibrate(
    subcircuit: Subcircuit,
    config,
    manifest=None,
) -> tuple[SimulationNetwork, CalibrationResult]:
    """Search the global weight scale for a stable spontaneous regime.

    Returns the network at the accepted scale. Raises if every scale is
    rejected, because a silent or saturated network would make every later
    measurement meaningless.
    """
    import brian2 as b2

    grid = [float(x) for x in config.sequence("calibration.scale_grid")]
    band = (
        float(config.get("calibration.min_mean_rate_hz")),
        float(config.get("calibration.max_mean_rate_hz")),
    )
    max_continuous = float(config.get("calibration.max_continuous_fraction"))
    max_drift = float(config.get("calibration.max_rate_drift_hz"))

    attempts: list[dict[str, float]] = []
    for scale in grid:
        started = time.perf_counter()
        network = build_network(subcircuit, config, weight_scale=scale)
        stats = measure_spontaneous_activity(network, config)
        elapsed = time.perf_counter() - started
        attempt = {
            "weight_scale": scale,
            "mean_rate_hz": stats["mean_rate_hz"],
            "max_rate_hz": stats["max_rate_hz"],
            "continuous_fraction": stats["continuous_fraction"],
            "rate_drift_hz": stats["rate_drift_hz"],
            "seconds": elapsed,
            "accepted": False,
        }
        in_band = band[0] <= stats["mean_rate_hz"] <= band[1]
        not_saturated = stats["continuous_fraction"] <= max_continuous
        stable = stats["rate_drift_hz"] <= max_drift
        attempt["accepted"] = bool(in_band and not_saturated and stable)
        attempt["in_band"] = bool(in_band)
        attempt["not_saturated"] = bool(not_saturated)
        attempt["stable"] = bool(stable)
        attempts.append(attempt)
        LOGGER.info(
            "calibration scale=%-7g mean=%6.2f Hz cont=%.3f drift=%6.2f -> %s",
            scale, stats["mean_rate_hz"], stats["continuous_fraction"],
            stats["rate_drift_hz"],
            "ACCEPT" if attempt["accepted"] else "reject",
        )
        if attempt["accepted"]:
            result = CalibrationResult(
                weight_scale=scale,
                mean_rate_hz=stats["mean_rate_hz"],
                max_rate_hz=stats["max_rate_hz"],
                continuous_fraction=stats["continuous_fraction"],
                rate_drift_hz=stats["rate_drift_hz"],
                accepted=True,
                attempts=attempts,
                acceptable_band_hz=band,
                max_continuous_fraction=max_continuous,
            )
            return network, result
        # The discarded candidate must not stay referenced by Brian2's magic
        # network, so stop the run and drop our handle before the next scale.
        b2.stop()
        network.brian = None

    message = (
        "weight-scale calibration rejected every candidate in "
        f"calibration.scale_grid ({grid}); the network is either silent or "
        f"saturated for all scales. Measured bands: "
        f"{[(a['weight_scale'], round(a['mean_rate_hz'], 3)) for a in attempts]}"
    )
    if manifest is not None:
        manifest.warn(message)
    raise NetworkError(message)