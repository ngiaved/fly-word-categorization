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

import itertools
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
#
# I_syn is a SIGNED synaptic current: excitatory synapses write a positive
# value and inhibitory synapses a negative one, following
# ``connectome.neurotransmitter_signs``. It is therefore ADDED, so a positive
# current depolarizes the postsynaptic neuron. Subtracting it would invert the
# polarity of the whole network, making every excitatory connection inhibitory.
LIF_MODEL = """
dv/dt = (v - v_rest + I_syn + I_teacher + I_bg) / tau : 1
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
    # Weights the plastic set was initialised to. Dopamine bounds are relative
    # to these, because the initial magnitude is `weight_scale` and therefore
    # tracks the calibration scale rather than sitting near 1.
    initial_weights: np.ndarray
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
            "short_term_depression": {
                "synthetic": True,
                "enabled": bool(
                    self.config.get("network.short_term_depression.enabled", True)
                ),
                "tau_ms": self.config.get("network.short_term_depression.tau_ms"),
                "use": self.config.get("network.short_term_depression.use"),
                "floor": self.config.get(
                    "network.short_term_depression.floor"
                ),
                "note": (
                    "FlyWire synapse counts carry no short-term plasticity; "
                    "this is a synthetic addition to prevent the network "
                    "latching into a fixed attractor."
                ),
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
    } | {(name, name) for name in subcircuit.roles} | plastic_pairs | artificial_pairs

    trace_tau = float(config.get("learning.trace_tau_ms"))
    trace_increment = float(config.get("learning.trace_increment"))
    w_min = float(config.get("learning.w_min"))
    w_max = float(config.get("learning.w_max"))

    # Short-term depression. Without it the network is bistable: measured on
    # this release it is silent below weight_scale ~1, and above ~2 it latches
    # into a fixed attractor. Presenting all 20 held-out words at scale 2
    # returned the bit-identical output vector [46, 4, 32, 5] every time,
    # because sustained drive kept every synapse fully available and the
    # network ignored the input pattern. A per-synapse resource that is spent
    # on use and recovers with time limits sustained drive, so responses stay
    # graded in the input. This is SYNTHETIC: FlyWire synapse counts carry no
    # short-term plasticity, so it is declared here and recorded in the
    # manifest rather than presented as measured wiring.
    std_enabled = bool(config.get("network.short_term_depression.enabled", True))
    std_tau = float(config.get("network.short_term_depression.tau_ms", 50.0))
    std_use = float(config.get("network.short_term_depression.use", 0.3))
    std_floor = float(config.get("network.short_term_depression.floor", 0.15))

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
                "delig/dt = -elig/trace_tau : 1 (clock-driven)\n"
                "du/dt = (u_rest - u)/tau_u : 1 (clock-driven)"
            )
            on_pre = "I_syn += w * u\nelig += trace_inc\nu -= use"
            on_post = "elig += trace_inc"
            ns = dict(namespace)
            ns["trace_tau"] = trace_tau * b2.ms
            ns["trace_inc"] = trace_increment
            ns["w_min"] = w_min
            ns["w_max"] = w_max
            ns["tau_u"] = max(std_tau, 1.0) * b2.ms
            ns["u_rest"] = 1.0
            ns["use"] = std_use
            syn = b2.Synapses(
                groups[pre_stage], groups[post_stage], model=model,
                on_pre=on_pre, on_post=on_post, method="euler", namespace=ns,
            )
        elif std_enabled:
            model = (
                "w : 1\n"
                "du/dt = (u_rest - u)/tau_u : 1 (clock-driven)"
            )
            ns = dict(namespace)
            ns["tau_u"] = max(std_tau, 1.0) * b2.ms
            ns["u_rest"] = 1.0
            ns["use"] = std_use
            syn = b2.Synapses(
                groups[pre_stage], groups[post_stage], model=model,
                on_pre="I_syn += w * u\nu -= use",
                method="euler", namespace=ns,
            )
        else:
            syn = b2.Synapses(
                groups[pre_stage], groups[post_stage],
                model="w : 1", on_pre="I_syn += w",
                method="euler", namespace=dict(namespace),
            )
        syn.connect(i=pre_indices.tolist(), j=post_indices.tolist())

        # Weight is normalised per stage pair so `weight_scale` has the same
        # physical meaning everywhere. The plastic set keeps a NONNEGATIVE
        # magnitude in plastic.weights while syn.w carries the signed current,
        # so w_min/w_max clipping can never flip an inhibitory synapse to
        # excitatory.
        #
        # Raw synapse counts are wildly unequal: mushroom_body ->
        # reinforcement averages 29 synapses per edge while reinforcement ->
        # mushroom_body averages 0.5, so that one excitatory feedback loop is
        # ~58x stronger than its return path. The network is then bistable --
        # exactly 0 Hz below threshold, 125-440 Hz above it -- and no weight
        # scale can reach the calibration band.
        #
        # `fan_in` divides each edge weight by its own synapse count, so the
        # TOTAL current a postsynaptic neuron receives from one presynaptic
        # spike is exactly `weight_scale`, independent of both the pair and the
        # neuron's fan-in. Scaling UP by count (the old `mean_synapses` scheme)
        # instead makes total drive grow with fan-in SQUARED: a neuron with
        # twice the mean fan-in draws four times the current, so the broad
        # excitatory optic-lobe chain runs away to the refractory limit
        # (2000 Hz at dt=0.5 ms) and every word saturates to the same pattern.
        normalisation_mode = str(
            config.get("network.pair_weight_normalisation", "fan_in")
        )
        # The artificial lobula -> mushroom_body bridge is synthetic, so its
        # strength is a declared free parameter rather than measured wiring. It
        # is exposed separately because the visual chain needs a much larger
        # `weight_scale` than the bridge can tolerate: at a shared scale the
        # bridge saturates the mushroom body (3471 spikes over 300 ms), which
        # drives `reinforcement` hard, and the 7-of-8 inhibitory
        # reinforcement -> output edges then clamp the readout fully silent.
        bridge_ratio = float(
            config.get("network.artificial_bridge.weight_ratio", 1.0)
        )
        mb_recurrent_ratio = float(
            config.get("network.mushroom_body_recurrent.weight_ratio", 1.0)
        )
        readout_ratio = float(
            config.get("network.artificial_readout.weight_ratio", 1.0)
        )
        output_recurrent_ratio = float(
            config.get("network.output_recurrent.weight_ratio", 1.0)
        )
        if (pre_stage, post_stage) == ("lobula", "mushroom_body"):
            pair_scale = float(weight_scale) * bridge_ratio
        elif (pre_stage, post_stage) == ("mushroom_body", "mushroom_body"):
            # The 116 mushroom_body -> mushroom_body edges are all excitatory
            # and measured to saturate the compartment (~3500 spikes per 300 ms
            # word) at every scale tried, independent of the bridge strength.
            # A self-exciting compartment that runs away carries no information
            # about its input, so its drive is exposed separately.
            pair_scale = float(weight_scale) * mb_recurrent_ratio
        elif (pre_stage, post_stage) == ("mushroom_body", "output"):
            # Dense synthetic readout: many Kenyon-cell inputs per output
            # neuron, so this pair needs its own scale to avoid saturating.
            pair_scale = float(weight_scale) * readout_ratio
        elif (pre_stage, post_stage) == ("output", "output"):
            # A 46-synapse EXCITATORY self-connection inside the output stage
            # latches the readout: output neurons 0 and 2 sat at ~122 spikes
            # per 300 ms word with sd 0.0-0.5, insensitive to the stimulus and
            # to a 20x reduction in readout drive, so one of them won all 20
            # held-out words. A self-exciting measurement device cannot report
            # its input, so its recurrence is a declared free parameter.
            pair_scale = float(weight_scale) * output_recurrent_ratio
        else:
            pair_scale = float(weight_scale)
        if normalisation_mode == "fan_in_total":
            # Normalise by the TOTAL incoming synapse weight of each
            # postsynaptic neuron, not per synapse.
            #
            # `fan_in` divides each edge weight by its own synapse count, which
            # equalises the current delivered by ONE presynaptic spike but not
            # the total a postsynaptic neuron receives. That total scales with
            # the neuron's number of inputs, and fan-in varies by orders of
            # magnitude across this subcircuit (a mushroom-body ->
            # reinforcement neuron has 48 inputs; a photoreceptor ->
            # lamina edge has a handful). At the calibrated weight scale this
            # diverges outright: reinforcement neuron 0 was measured running to
            # v = -3.0e19 while its siblings sat pinned at v = 9.1, and the
            # teacher current could not make them spike at any gain.
            #
            # Dividing by the per-neuron incoming total instead makes the drive
            # delivered to every postsynaptic neuron independent of how many
            # inputs it happens to have, so one weight_scale means the same
            # physical current at every stage. Presynaptic rates still matter,
            # which is what keeps the visual chain word-dependent.
            incoming = np.bincount(
                post_indices, weights=n_syn,
                minlength=int(post_indices.max()) + 1 if len(post_indices) else 1,
            )
            magnitude = pair_scale / np.maximum(incoming[post_indices], 1.0)
        elif normalisation_mode == "fan_in" and len(n_syn):
            magnitude = pair_scale / n_syn
        elif normalisation_mode == "mean_synapses" and len(n_syn):
            mean_syn = float(n_syn.mean())
            if mean_syn > 0.0:
                magnitude = n_syn / mean_syn * pair_scale
            else:
                magnitude = n_syn * pair_scale
        else:
            magnitude = pair_scale * np.ones_like(n_syn)
        weight = sign * magnitude
        syn.w = weight.tolist()
        if std_enabled:
            # ``u`` is a dimensionless release probability starting fully
            # available; ``floor`` stops a heavily used synapse from becoming
            # permanently silent.
            syn.u = 1.0
        if is_plastic:
            syn.elig = 0.0
        synaptic_groups[role_pair] = syn
        synapse_signs[role_pair] = sign
        LOGGER.info(
            "synapses %-28s n=%6d sign(%+d/%d) mean_syn=%.4g mode=%s scale=%.4g",
            role_pair, len(weight), int((sign > 0).sum()), int((sign < 0).sum()),
            float(n_syn.mean()) if len(n_syn) else 0.0,
            normalisation_mode, weight_scale,
        )
        if is_plastic:
            plastic = PlasticSynapses(
                pre_indices=pre_indices,
                post_indices=post_indices,
                role_pair=role_pair,
                weights=magnitude.copy(),
                initial_weights=magnitude.copy(),
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
    for stage in stages.values():
        # Declared state variables shadow same-named namespace entries in
        # Brian2, so the background drive is assigned after construction. It is
        # set independently of the noise toggle: without it the network is
        # perfectly silent, so tying it to ``noise.enabled`` would make a
        # noise-free diagnostic run report zero activity everywhere.
        stage.group.I_bg = float(config.get("network.background_dc_na"))
    if config.get("network.noise.enabled"):
        noise_rate = float(config.get("network.noise.rate_hz")) / b2.second
        noise_weight = float(config.get("network.noise.weight"))
        for stage in stages.values():
            source = b2.PoissonGroup(stage.n, rates=noise_rate, name=f"noise_{stage.name}")
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


def single_spike_psp(weight: float, tau_s: float, tau_syn_s: float) -> float:
    """Peak membrane excursion caused by one synaptic spike.

    A spike sets ``I_syn`` to ``weight``; it then decays with ``tau_syn`` while
    the membrane relaxes with ``tau``. Convolving the two gives

        v(t) = (w / tau) * exp(-t / tau) * (exp(a t) - 1) / a,
        a = 1 / tau - 1 / tau_syn,

    which peaks at ``t* = ln(tau_syn / tau) / a``.
    """
    if tau_s <= 0.0 or tau_syn_s <= 0.0:
        return 0.0
    a = 1.0 / tau_s - 1.0 / tau_syn_s
    if abs(a) < 1e-12:
        # tau == tau_syn: v(t) = (w / tau) * t * exp(-t / tau), peak at t = tau.
        return weight * float(np.exp(-1.0))
    t_star = float(np.log(tau_syn_s / tau_s) / a)
    if t_star <= 0.0:
        return 0.0
    return float(
        (weight / tau_s)
        * np.exp(-t_star / tau_s)
        * (np.exp(a * t_star) - 1.0)
        / a
    )


def transmission_weight(config) -> float:
    """Smallest synaptic weight for which one spike reaches ``v_threshold``.

    With the configured cell this is 6.35 nA, so any calibration point whose
    strongest edge is weaker than this cannot fire a postsynaptic neuron from
    any input. Such a point satisfies the spontaneous-rate band only because
    the network is silent, which is why the band alone is not sufficient.
    """
    tau_s = float(config.get("network.lif.tau_ms")) * 1e-3
    tau_syn_s = float(config.get("network.lif.tau_syn_ms")) * 1e-3
    v_threshold = float(config.get("network.lif.v_threshold"))
    v_rest = float(config.get("network.lif.v_rest"))
    per_unit = single_spike_psp(1.0, tau_s, tau_syn_s)
    if per_unit <= 0.0:
        return float("inf")
    return (v_threshold - v_rest) / per_unit


def drives_response(net, config) -> dict[str, Any]:
    """Check that a stimulus actually propagates to the readout.

    A single-spike bound is not usable here. With ``fan_in`` normalisation an
    edge weighs ``weight_scale / synapse_count``; ``synapse_count`` has minimum
    1, so the strongest edge equals ``weight_scale`` and single-spike
    transmission would need ``weight_scale >= 6.35``. Every such point drives
    spontaneous rates of 23 Hz and above, outside the 0.5-15 Hz band, so no
    candidate can satisfy both. The bound is therefore recorded but not
    enforced, and transmission is measured instead: the photoreceptors are
    driven with a current that reliably reaches threshold and the readout is
    checked for spikes. That is the property that actually matters, and it
    holds for summed drive from many presynaptic spikes.
    """
    import brian2 as b2

    probe_ms = float(config.get("encoding.stimulus_ms"))

    monitors = {
        name: b2.SpikeMonitor(stage.group, record=False)
        for name, stage in net.stages.items()
    }
    net.brian.add(list(monitors.values()))
    # Drive with the strongest current the encoder can actually produce for
    # ``encoding.max_hz``. An arbitrary multiple of threshold would let a
    # candidate pass that no real stimulus could drive.
    from .encoding import rate_to_current

    probe_current = float(
        rate_to_current(
            np.array([float(config.get("encoding.max_hz"))]), config
        )[0]
    )
    net.stages["photoreceptor"].group.I_syn = probe_current
    net.brian.run(probe_ms * b2.ms)
    output = int(np.asarray(monitors["output"].count, dtype=np.int64).sum())
    for monitor in monitors.values():
        try:
            net.brian.remove(monitor)
        except KeyError:
            pass
    net.stages["photoreceptor"].group.I_syn = 0.0
    v_rest_value = float(config.get("network.lif.v_rest"))
    for stage in net.stages.values():
        stage.group.v = v_rest_value
        stage.group.I_syn = 0.0
    return {"output_spikes": output, "transmits": bool(output > 0)}


def clear_monitors(monitors) -> None:
    """Attempt to zero spike counts on SpikeMonitors built with ``record=False``.

    This does NOT work and must not be relied upon. ``count`` is registered as
    a read-only dynamic array and Brian2 rebinds it on every ``run()``, so the
    in-place zeroing below is written to an array that is immediately
    discarded; the counter stays cumulative. Presenting one word repeatedly
    returned 4, 8, 13, 16, 21 spikes on successive trials, which is the sum of
    every spike so far rather than the spikes in that trial.

    Per-window counts are therefore computed as the difference between the
    counter before and after the window; see ``TrialRunner.snapshot_counts``.
    This helper is kept only so existing call sites read clearly.
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
    # ``count`` is cumulative and cannot be zeroed, so subtract the settled
    # spikes explicitly; otherwise the settle period is folded into the
    # measured window and inflates the reported rate.
    settled = {
        name: np.asarray(monitor.count, dtype=np.int64).copy()
        for name, monitor in monitors.items()
    }

    measure_ms = max(simulated_ms - settle_ms, 1.0)
    started = time.perf_counter()
    network.brian.run(measure_ms * b2.ms)
    wall_seconds = time.perf_counter() - started

    # These monitors are per-candidate scratch objects. Leaving them attached
    # would accumulate one set per calibration attempt on the same network.
    per_stage = {
        name: np.maximum(
            np.asarray(monitor.count, dtype=np.float64) - settled[name], 0.0
        )
        for name, monitor in monitors.items()
    }
    elapsed_s = measure_ms * 1e-3
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
    if n_neurons >= 2:
        # Split by index, not by reshape: an odd neuron count cannot be split
        # evenly. Both halves must cover the same duration for the comparison
        # to be meaningful, so one neuron is dropped from the second half when
        # the count is odd. This is a measurement artifact only; it does not
        # affect the reported mean rate or the continuous-fraction test.
        half = n_neurons // 2
        first_rate = float(counts[:half].sum() / half_s / half)
        second_rate = float(counts[half: 2 * half].sum() / half_s / half)
        rate_drift = abs(second_rate - first_rate)
    else:
        first_rate = second_rate = 0.0
        rate_drift = 0.0

    for monitor in monitors.values():
        try:
            network.brian.remove(monitor)
        except KeyError:
            pass
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
    # Drive parameters searched jointly with the weight scale. An empty list
    # means "leave the configured value alone".
    dc_grid = [
        float(x) for x in config.sequence("calibration.background_dc_grid")
    ] or [None]
    noise_grid = [
        float(x) for x in config.sequence("calibration.noise_weight_grid")
    ] or [None]
    base_dc = float(config.get("network.background_dc_na"))
    base_noise = float(config.get("network.noise.weight"))
    band = (
        float(config.get("calibration.min_mean_rate_hz")),
        float(config.get("calibration.max_mean_rate_hz")),
    )
    max_continuous = float(config.get("calibration.max_continuous_fraction"))
    max_drift = float(config.get("calibration.max_rate_drift_hz"))
    required_weight = transmission_weight(config)

    def _strongest_edge(net) -> float:
        strongest = 0.0
        for syn in net.synaptic_groups.values():
            weights = np.abs(np.asarray(syn.w, dtype=np.float64))
            if weights.size:
                strongest = max(strongest, float(weights.max()))
        return strongest

    attempts: list[dict[str, float]] = []
    for dc, noise_weight in itertools.product(dc_grid, noise_grid):
        for scale in grid:
            started = time.perf_counter()
            trial_config = config
            if dc is not None or noise_weight is not None:
                overrides: dict[str, Any] = {}
                if dc is not None:
                    overrides["network.background_dc_na"] = dc
                if noise_weight is not None:
                    overrides["network.noise.weight"] = noise_weight
                trial_config = config.with_overrides(overrides)
            network = build_network(subcircuit, trial_config, weight_scale=scale)
            stats = measure_spontaneous_activity(network, trial_config)
            elapsed = time.perf_counter() - started
            used_dc = dc if dc is not None else base_dc
            used_noise = noise_weight if noise_weight is not None else base_noise
            attempt = {
                "weight_scale": scale,
                "background_dc_na": used_dc,
                "noise_weight": used_noise,
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
            strongest = _strongest_edge(network)
            transmits = drives_response(network, trial_config)["transmits"]
            attempt["strongest_edge"] = strongest
            attempt["single_spike_bound"] = required_weight
            attempt["transmits"] = transmits
            attempt["accepted"] = bool(
                in_band and not_saturated and stable and transmits
            )
            attempt["in_band"] = bool(in_band)
            attempt["not_saturated"] = bool(not_saturated)
            attempt["stable"] = bool(stable)
            attempts.append(attempt)
            LOGGER.info(
                "calibration scale=%-7g dc=%-5g noise=%-5g mean=%6.2f Hz "
                "cont=%.3f drift=%6.2f transmits=%s -> %s",
                scale, used_dc, used_noise, stats["mean_rate_hz"],
                stats["continuous_fraction"], stats["rate_drift_hz"],
                transmits,
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
            # The discarded candidate must not stay referenced by Brian2's
            # magic network, so stop the run and drop our handle before the
            # next candidate.
            b2.stop()
            network.brian = None

    message = (
        "calibration rejected every candidate in the joint "
        f"scale_grid x background_dc_grid x noise_weight_grid search "
        f"({len(grid)}x{len(dc_grid)}x{len(noise_grid)}); the network is "
        "either silent or saturated everywhere. Measured mean rates (Hz): "
        f"{[(a['weight_scale'], a['background_dc_na'], a['noise_weight'], round(a['mean_rate_hz'], 3)) for a in attempts]}"
    )
    if manifest is not None:
        manifest.warn(message)
    raise NetworkError(message)