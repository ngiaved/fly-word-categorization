"""Dopamine-gated plasticity, readout, and the trial loop.

Three-factor rule (design D4), applied only at mushroom body -> output
synapses:

1. an eligibility trace, a declared synaptic state variable, rises on
   coincident pre/post activity and decays with a time constant;
2. the output spikes are counted over the response window and argmaxed to give
   a predicted category;
3. a scalar dopamine signal (+1 reward, -1 punishment) converts eligibility
   into weight change, clipped to configured bounds.

The weight update runs between trials, so the rule is explicit and testable
rather than buried in the simulation loop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Sequence

import numpy as np

from .encoding import (
    GridMapping,
    darkness_to_rate,
    encode_word,
    rate_to_current,
    render_word,
)
from .network import SimulationNetwork, clear_monitors
from .repro import make_rng

LOGGER = logging.getLogger(__name__)

NO_RESPONSE = -1


class LearningError(RuntimeError):
    """Raised when the learning loop is set up incorrectly."""


# --------------------------------------------------------------------------
# Readout
# --------------------------------------------------------------------------

@dataclass
class ReadoutResult:
    """Outcome of one response-window readout."""

    predicted: int
    counts: np.ndarray
    total_spikes: int
    is_no_response: bool

    @property
    def predicted_category(self) -> str | None:
        return None if self.is_no_response else self.predicted


def readout(
    counts: Sequence[int] | np.ndarray,
    seed: int = 0,
    tie_break: str = "seeded",
    stream: str = "readout",
) -> ReadoutResult:
    """Winner-take-most over output spike counts.

    ``tie_break``:

    * ``"seeded"`` - choose uniformly among tied maxima from a seeded stream,
      so a repeated call with the same seed returns the same category.
    * ``"lowest_index"`` - always choose the lowest tied index.
    """
    values = np.asarray(counts, dtype=np.float64).reshape(-1)
    if values.size == 0:
        raise LearningError("readout requires at least one output neuron")
    total = int(values.sum())
    if total == 0:
        return ReadoutResult(
            predicted=NO_RESPONSE, counts=values.astype(np.int64),
            total_spikes=0, is_no_response=True,
        )
    best = float(values.max())
    tied = np.flatnonzero(values == best)
    if tied.size == 1 or tie_break == "lowest_index":
        chosen = int(tied[0])
    else:
        chosen = int(tied[make_rng(seed, stream).integers(0, tied.size)])
    return ReadoutResult(
        predicted=chosen,
        counts=values.astype(np.int64),
        total_spikes=total,
        is_no_response=False,
    )


# --------------------------------------------------------------------------
# Plasticity
# --------------------------------------------------------------------------

@dataclass
class PlasticityStats:
    """Cumulative plasticity bookkeeping for a run."""

    updates: int = 0
    positive_updates: int = 0
    negative_updates: int = 0
    rewards: int = 0
    punishments: int = 0
    no_responses: int = 0
    clipped_low: int = 0
    clipped_high: int = 0
    weight_sum_start: float = 0.0
    weight_sum_end: float = 0.0
    trace_max: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "updates": self.updates,
            "positive_updates": self.positive_updates,
            "negative_updates": self.negative_updates,
            "rewards": self.rewards,
            "punishments": self.punishments,
            "no_responses": self.no_responses,
            "clipped_low": self.clipped_low,
            "clipped_high": self.clipped_high,
            "weight_sum_start": self.weight_sum_start,
            "weight_sum_end": self.weight_sum_end,
            "mean_weight_change": (
                (self.weight_sum_end - self.weight_sum_start) / self.updates
                if self.updates else 0.0
            ),
            "trace_max": self.trace_max,
        }


def apply_dopamine(
    network: SimulationNetwork,
    config,
    dopamine: float,
    stats: PlasticityStats | None = None,
) -> dict[str, Any]:
    """Apply one dopamine-gated weight update to the plastic synapse set.

    Only synapses that are still active (not pruned, not silenced) are updated.

    Weights are clipped to ``learning.w_min`` / ``learning.w_max``, which are
    nonnegative, so a weight is a synaptic MAGNITUDE and the excitatory or
    inhibitory character of the synapse lives in ``synapse_signs``. The
    magnitude is converted back to a signed current before it reaches
    ``I_syn``, otherwise clipping inhibitory synapses at zero would silently
    turn every one of them excitatory.
    """
    if dopamine == 0.0:
        return {"changed": 0, "dopamine": 0.0}
    if not config.get("learning.enabled"):
        return {"changed": 0, "dopamine": dopamine, "skipped": "learning disabled"}

    plastic = network.plastic
    syn = network.synaptic_groups[plastic.role_pair]

    trace = np.asarray(syn.elig[:], dtype=np.float64)
    active = plastic.active
    if stats is not None:
        stats.trace_max = max(stats.trace_max, float(trace.max()) if trace.size else 0.0)

    rate = float(config.get("learning.learning_rate"))
    before = plastic.weights.copy()
    delta = rate * float(dopamine) * trace * active
    # Bounds are relative to the INITIAL synaptic weight, not absolute. The
    # plastic mushroom_body -> output synapse is initialised to `weight_scale`
    # (measured 500 in the calibrated operating regime), so absolute bounds of
    # 0..1 made the very first dopamine update clip the readout drive down by
    # ~500x and leave the update range far too weak to matter -- a postsynaptic
    # spike needs a current of about tau_mem/v_threshold, i.e. roughly 333 at
    # these LIF settings. Learning could therefore only ever destroy the
    # readout, never shape it. Ratios keep plasticity meaningful at any scale.
    initial = np.asarray(
        getattr(plastic, "initial_weights", plastic.weights), dtype=np.float64
    )
    if config.get("learning.relative_bounds", True):
        lo = initial * float(config.get("learning.w_min_ratio", 0.0))
        hi = initial * float(config.get("learning.w_max_ratio", 2.0))
        updated = np.clip(before + delta, np.minimum(lo, hi), np.maximum(lo, hi))
        w_min, w_max = float(lo.min()), float(hi.max())
    else:
        w_min = float(config.get("learning.w_min"))
        w_max = float(config.get("learning.w_max"))
        updated = np.clip(before + delta, w_min, w_max)

    plastic.weights = updated
    plastic.trace = trace
    signs = network.synapse_signs.get(plastic.role_pair)
    if signs is None:
        raise LearningError(
            f"no sign vector recorded for plastic role pair {plastic.role_pair!r}"
        )
    # Store the signed current on the synapse; keep the nonnegative magnitude in
    # plastic.weights so w_min/w_max, pruning, and reporting stay well defined.
    syn.w = (np.asarray(signs, dtype=np.float64) * updated).tolist()

    if stats is not None:
        stats.updates += 1
        if dopamine > 0:
            stats.positive_updates += 1
        else:
            stats.negative_updates += 1
        stats.clipped_low += int(np.count_nonzero((delta < 0) & (updated <= w_min)))
        stats.clipped_high += int(np.count_nonzero((delta > 0) & (updated >= w_max)))
        stats.weight_sum_end = float(updated.sum())

    return {
        "changed": int(np.count_nonzero(delta)),
        "dopamine": float(dopamine),
        "max_abs_delta": float(np.abs(delta).max()) if delta.size else 0.0,
    }


def dopamine_for(
    predicted: int, true_label: int, config
) -> tuple[float, str]:
    """Three-factor signal for one trial: reward, punishment, or none."""
    if predicted == NO_RESPONSE:
        return 0.0, "no_response"
    reward = float(config.get("learning.reward"))
    punishment = float(config.get("learning.punishment"))
    if predicted == true_label:
        return reward, "reward"
    return punishment, "punishment"


def fixed_synapses_unchanged(
    before: dict[str, np.ndarray], network: SimulationNetwork
) -> dict[str, bool]:
    """Check that non-plastic synapses did not move (plasticity-locus test)."""
    plastic_pair = network.plastic.role_pair
    result: dict[str, bool] = {}
    for role_pair, syn in network.synaptic_groups.items():
        if role_pair == plastic_pair:
            continue
        current = np.asarray(syn.w[:], dtype=np.float64)
        result[role_pair] = bool(
            current.shape == before[role_pair].shape
            and np.array_equal(current, before[role_pair])
        )
    return result


def snapshot_fixed_weights(network: SimulationNetwork) -> dict[str, np.ndarray]:
    plastic_pair = network.plastic.role_pair
    return {
        role_pair: np.asarray(syn.w[:], dtype=np.float64)
        for role_pair, syn in network.synaptic_groups.items()
        if role_pair != plastic_pair
    }


# --------------------------------------------------------------------------
# Trial loop
# --------------------------------------------------------------------------

@dataclass
class TrialRecord:
    """One presented word and its outcome."""

    trial: int
    word: str
    true_label: int
    predicted: int
    correct: bool
    dopamine: float
    spike_counts: list[int]
    no_response: bool
    reward_type: str


@dataclass
class TrialRunner:
    """Drives stimulus presentation, readout, dopamine, and inter-trial reset."""

    network: SimulationNetwork
    config: Any
    mapping: GridMapping
    seed: int
    input_gain: float = 1.0
    tie_break: str = "seeded"
    stats: PlasticityStats = field(default_factory=PlasticityStats)
    history: list[TrialRecord] = field(default_factory=list)
    _monitors: dict[str, Any] = field(default_factory=dict, repr=False)
    _output_locations: dict[int, tuple[str, int]] = field(
        default_factory=dict, repr=False
    )
    # Stimulus-window snapshots, taken before the monitors are cleared for the
    # rest period. Structural plasticity and reporting read these instead of
    # the live monitors, which are zero by then.
    last_stimulus_rates: dict[str, float] = field(default_factory=dict)
    last_stimulus_counts: Any = None
    # Trial history restricted to learning trials, for the learning curve.
    training_history: list = field(default_factory=list, repr=False)
    # Per-trial record of how strongly the real reinforcement neurons fired.
    teacher_responses: list = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        import brian2 as b2

        self._b2 = b2
        monitors = {
            name: b2.SpikeMonitor(group.group, record=False)
            for name, group in self.network.stages.items()
        }
        self.network.brian.add(list(monitors.values()))
        self._monitors = monitors
        # Precomputed output index -> (stage, local index) lookup.
        self._output_locations = {
            int(internal): self._locate_output(int(internal))
            for internal in self.network.output_indices
        }

    # -- input ----------------------------------------------------------
    def _apply_stimulus(self, word: str) -> np.ndarray:
        image = render_word(word, self.config)
        darkness = self.mapping.apply(image)
        rates = darkness_to_rate(darkness, self.config)
        currents = rate_to_current(rates, self.config) * self.input_gain
        group = self._photoreceptor_group()
        group.I_syn = currents.tolist()
        return currents

    def _clear_stimulus(self) -> None:
        self._photoreceptor_group().I_syn = 0.0

    def _photoreceptor_group(self):
        """The Brian2 NeuronGroup that receives the visual drive.

        ``network.stages`` maps a role name to a ``StageGroup`` wrapper, not to
        the Brian2 group. Assigning ``I_syn`` on the wrapper would silently
        create a plain Python attribute and leave the simulated neuron
        untouched, so the underlying group must be unwrapped here.
        """
        from .connectome import INPUT_STAGES

        for name in INPUT_STAGES:
            if name in self.network.stages:
                return self.network.stages[name].group
        raise LearningError("network has no photoreceptor group to drive")

    # -- one trial ------------------------------------------------------
    def run_trial(
        self,
        word: str,
        true_label: int,
        trial_index: int,
        rest: bool = True,
        learn: bool = True,
    ) -> TrialRecord:
        """Present a word, read out, deliver dopamine, then rest.

        ``learn=False`` is used for held-out evaluation and for the untrained
        control. It suppresses the weight update entirely, so evaluation can
        never influence the network being measured (no test-label leakage).
        """
        import brian2 as b2

        from .encoding import trial_timing

        timing = trial_timing(self.config)
        if self.stats.updates == 0:
            self.stats.weight_sum_start = float(self.network.plastic.weights.sum())

        clear_monitors(self._monitors.values())
        count_base = self.snapshot_counts()
        self._apply_stimulus(word)
        self.network.brian.run(timing.stimulus_ms * b2.ms)

        window = self._window_counts(count_base)
        counts = np.zeros(len(self.network.output_indices), dtype=np.int64)
        for position, internal in enumerate(self.network.output_indices):
            stage, local = self._output_locations[int(internal)]
            counts[position] = int(window[stage][local])

        # Snapshot per-stage rates while the counts still hold stimulus-time
        # spikes only; the window baseline keeps them from including history.
        stimulus_rate = self.stage_rates_hz(timing.stimulus_ms, count_base)

        result = readout(
            counts, seed=self.seed,
            tie_break=self.tie_break,
            stream=f"readout:trial{trial_index}",
        )
        dopamine, reward_type = dopamine_for(result.predicted, true_label, self.config)

        if reward_type == "reward":
            self.stats.rewards += 1
        elif reward_type == "punishment":
            self.stats.punishments += 1
        else:
            self.stats.no_responses += 1

        teacher_gate = 0.0
        if learn:
            teacher_gate = self._deliver_teaching(dopamine, trial_index)
            dopamine = dopamine * teacher_gate
            apply_dopamine(self.network, self.config, dopamine, self.stats)
        else:
            dopamine = 0.0
            reward_type = "none"

        self.last_stimulus_rates = stimulus_rate
        self.last_stimulus_counts = counts

        record = TrialRecord(
            trial=trial_index,
            word=word,
            true_label=true_label,
            predicted=result.predicted,
            correct=(not result.is_no_response) and result.predicted == true_label,
            dopamine=float(dopamine),
            spike_counts=counts.tolist(),
            no_response=result.is_no_response,
            reward_type=reward_type,
        )
        self.history.append(record)
        if learn:
            self.training_history.append(record)

        self._clear_stimulus()
        if rest:
            clear_monitors(self._monitors.values())
            self.network.brian.run(timing.rest_ms * b2.ms)
            self.reset_state()

        LOGGER.debug(
            "trial %4d %-5s true=%d pred=%2d spikes=%s d=%+.1f %s",
            trial_index, word, true_label, result.predicted,
            counts.tolist(), dopamine, reward_type,
        )
        return record

    def snapshot_counts(self) -> dict[str, np.ndarray]:
        """Copy the monitors' cumulative spike counters.

        ``SpikeMonitor.count`` is cumulative across ``run()`` calls and is
        declared read-only, so ``clear_monitors`` cannot reset it: Brian2
        rebinds ``count`` on every run and the in-place zeroing is discarded.
        The previous code relied on that clear, so the readout summed every
        spike the network had ever produced. Presenting the same word with an
        identical stimulus index returned 4, 8, 13, 16, 21 spikes on
        successive trials, and per-stage rates grew without bound.
        """
        return {
            name: np.asarray(monitor.count, dtype=np.int64).copy()
            for name, monitor in self._monitors.items()
        }

    def _window_counts(
        self, base: dict[str, np.ndarray]
    ) -> dict[str, np.ndarray]:
        """Spikes recorded since ``base``, per stage."""
        return {
            name: np.maximum(
                np.asarray(monitor.count, dtype=np.int64) - base.get(
                    name, np.zeros_like(np.asarray(monitor.count))
                ),
                0,
            )
            for name, monitor in self._monitors.items()
        }

    def stage_rates_hz(
        self, duration_ms: float, base: dict[str, np.ndarray] | None = None
    ) -> dict[str, float]:
        """Mean firing rate per stage over the window just simulated.

        Must be called before the monitors are cleared for the rest period.
        ``base`` is the snapshot taken just before the window, so the rate
        reflects this window only and not the whole session.
        """
        if duration_ms <= 0:
            return {name: 0.0 for name in self.network.stages}
        counts = self._window_counts(base or {})
        return {
            name: float(values.mean()) * 1000.0 / duration_ms
            for name, values in counts.items()
        }

    def _deliver_teaching(self, dopamine: float, trial_index: int = 0) -> float:
        """Drive real reinforcement neurons and return the gating factor.

        The reward value is injected as a current into the real APL/DPM
        reinforcement neurons. Their resulting spike count gates the weight
        update, so plasticity only happens if genuine reinforcement neurons
        actually respond: the three-factor rule is enforced by real FlyWire
        neurons rather than by a bare scalar. Returns a value in [0, 1].
        """
        import brian2 as b2

        teacher = self.network.teacher_stage_indices
        if not teacher or not any(teacher.values()):
            # No reinforcement stage: the reward drives plasticity directly.
            return 1.0 if dopamine != 0.0 else 0.0

        config = self.config
        tau_ms = float(config.get("network.reward_tau_ms"))
        if dopamine > 0:
            current = float(config.get("network.reward_gain_na")) * abs(dopamine)
        elif dopamine < 0:
            current = -float(config.get("network.punishment_gain_na")) * abs(dopamine)
        else:
            return 0.0

        teacher_monitors = {
            name: self._b2.SpikeMonitor(
                self.network.stages[name].group, record=False
            )
            for name, indices in teacher.items() if indices
        }
        self.network.brian.add(list(teacher_monitors.values()))
        for name, indices in teacher.items():
            if not indices:
                continue
            self.network.stages[name].group.I_teacher[:] = current

        clear_monitors(teacher_monitors.values())
        self.network.brian.run(tau_ms * b2.ms)
        for name in teacher:
            if name in teacher:
                self.network.stages[name].group.I_teacher[:] = 0.0

        n_neurons = sum(
            len(indices) for indices in teacher.values() if indices
        )
        spikes = int(sum(
            float(np.asarray(monitor.count, dtype=np.float64).sum())
            for monitor in teacher_monitors.values()
        ))
        expected = max(1.0, n_neurons * tau_ms / 1000.0 / 20.0)
        gate = min(1.0, spikes / expected)
        self.teacher_responses.append({
            "trial": trial_index, "dopamine": float(dopamine),
            "teacher_spikes": spikes, "teacher_neurons": n_neurons,
            "gate": float(gate),
        })
        return gate

    def _apply_teacher(self, value: float) -> None:
        """Drive the real reinforcement neurons with a reward current.

        The reward value is synthetic; the neurons it drives (APL/DPM from the
        MBIN class) and their synapses onto Kenyon cells are real FlyWire
        wiring. The teacher current therefore reaches the plastic pathway only
        through genuine reinforcement connectivity.
        """
        import brian2 as b2

        teacher = self.network.teacher_stage_indices
        if not teacher:
            return
        for name, indices in teacher.items():
            if not indices:
                continue
            group = self.network.stages[name].group
            group.I_teacher[:] = value * float(self.config.get("network.reward_gain_na"))

    def _locate_output(self, internal: int) -> tuple[str, int]:
        for name, group in self.network.stages.items():
            hits = np.flatnonzero(group.indices == internal)
            if hits.size:
                return name, int(hits[0])
        raise LearningError(f"output neuron {internal} is in no stage group")

    # -- reset ----------------------------------------------------------
    def reset_state(self) -> None:
        """Return membrane potentials and eligibility to the documented baseline.

        Membrane potential and input current are reset to the LIF rest values;
        the eligibility trace is zeroed. Both are documented baselines rather
        than carried over between trials.
        """
        from .connectome import OUTPUT_STAGES

        v_rest = float(self.config.get("network.lif.v_rest"))
        for stage in self.network.stages.values():
            # Unwrap to the Brian2 group: ``stage`` is a StageGroup wrapper, so
            # assigning ``.v`` on it would set a Python attribute and never
            # reset the simulated membrane potential.
            group = stage.group
            group.v = v_rest
            group.I_syn = 0.0
        self.network.reserve.v = v_rest
        self.network.reserve.I_syn = 0.0
        plastic = self.network.plastic
        syn = self.network.synaptic_groups[plastic.role_pair]
        syn.elig = 0.0
        plastic.trace = np.zeros_like(plastic.trace)
        # Release probability is a documented per-trial baseline, not carried
        # over: a synapse left depleted from the previous word would bias the
        # next word's response toward whatever it was used for.
        if bool(self.config.get("network.short_term_depression.enabled", True)):
            for group in self.network.synaptic_groups.values():
                if "u" in group.variables:
                    group.u = 1.0

    # -- reporting ------------------------------------------------------
    @property
    def monitors(self) -> dict[str, Any]:
        """Per-stage spike monitors, used for structural activity tracking."""
        return self._monitors

    def accuracy(self, since: int = 0) -> float:
        subset = self.history[since:]
        if not subset:
            return 0.0
        return sum(1 for r in subset if r.correct) / len(subset)

    def learning_curve(self, window: int, history=None) -> list[dict[str, Any]]:
        """Accuracy and no-response rate per window of trials.

        ``history`` defaults to every trial; pass ``runner.training_history`` to
        exclude held-out evaluation trials, which must never appear in the
        training learning curve.
        """
        records = self.history if history is None else history
        curve: list[dict[str, Any]] = []
        for start in range(0, len(records), window):
            chunk = records[start:start + window]
            if not chunk:
                continue
            curve.append({
                "trial_start": chunk[0].trial,
                "trial_end": chunk[-1].trial,
                "n_trials": len(chunk),
                "accuracy": sum(1 for r in chunk if r.correct) / len(chunk),
                "no_response_rate": sum(1 for r in chunk if r.no_response) / len(chunk),
                "mean_dopamine": float(np.mean([r.dopamine for r in chunk])),
            })
        return curve