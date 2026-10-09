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


def _update_plastic_set(
    plastic,
    syn,
    signs: np.ndarray | None,
    dopamine: float,
    config,
    rate: float,
    relative: bool,
    stats: PlasticityStats | None,
    valence_by_syn: np.ndarray | None = None,
) -> dict[str, Any]:
    """One dopamine-gated update to a single plastic synapse set.

    ``signs`` carries the excitatory/inhibitory character of each synapse; the
    tracked ``plastic.weights`` are always nonnegative magnitudes so
    ``w_min``/``w_max`` clipping can never flip an inhibitory synapse to
    excitatory. Pass ``signs=None`` for a purely excitatory set (the recruited
    reserve input), where the magnitude is the signed current.

    ``valence_by_syn`` optionally supplies one modulation value per synapse
    instead of the single ``dopamine`` scalar. That is what enables per-class
    credit assignment: the wrong output can be punished while the correct one
    is rewarded on the same trial, which a uniform scalar cannot express. When
    it is ``None`` the classic scalar three-factor rule runs unchanged.
    """
    trace = np.asarray(syn.elig[:], dtype=np.float64)
    active = plastic.active
    if valence_by_syn is None:
        mod = float(dopamine)
    else:
        mod = np.asarray(valence_by_syn, dtype=np.float64)
        if mod.shape != trace.shape:
            raise LearningError(
                f"valence_by_syn shape {mod.shape} does not match "
                f"{trace.size} synapses"
            )
    if stats is not None:
        stats.trace_max = max(
            stats.trace_max, float(trace.max()) if trace.size else 0.0
        )

    before = plastic.weights.copy()
    initial = np.asarray(
        getattr(plastic, "initial_weights", plastic.weights), dtype=np.float64
    )

    if relative:
        # Work in DIMENSIONLESS units: divide each synapse's current weight by
        # its own initial weight, apply the additive step there, then scale
        # back. The physical weight is an arbitrary current (measured ~3.5e-3
        # here, but ~500 under other readout ratios), while ``rate * elig`` is a
        # fixed number (~0.02). Applying the step to the raw physical weight let
        # a single punishment (step ~0.02 >> 0.0035) clip every eligible synapse
        # straight to zero, so the readout could only be destroyed, never
        # shaped. In relative units the weight starts at 1.0 and one update
        # moves it by a fixed fraction, which is stable at any physical scale.
        w_min_ratio = float(config.get("learning.w_min_ratio", 0.0))
        w_max_ratio = float(config.get("learning.w_max_ratio", 2.0))
        safe_initial = np.where(initial > 0.0, initial, 1.0)
        normalized = before / safe_initial
        delta = np.zeros_like(trace, dtype=np.float64)
        step = rate * mod * trace
        delta[active] = step[active]
        updated_norm = np.clip(normalized + delta, w_min_ratio, w_max_ratio)
        updated = updated_norm * safe_initial
        w_min = float((w_min_ratio * safe_initial).min())
        w_max = float((w_max_ratio * safe_initial).max())
    else:
        delta = rate * mod * trace * active
        w_min = float(config.get("learning.w_min"))
        w_max = float(config.get("learning.w_max"))
        updated = np.clip(before + delta, w_min, w_max)

    plastic.weights = updated
    plastic.trace = trace
    # Store the signed current on the synapse; keep the nonnegative magnitude in
    # plastic.weights so w_min/w_max, pruning, and reporting stay well defined.
    if signs is None:
        syn.w = updated.tolist()
    else:
        syn.w = (np.asarray(signs, dtype=np.float64) * updated).tolist()

    if stats is not None:
        stats.updates += 1
        if valence_by_syn is None:
            if dopamine > 0:
                stats.positive_updates += 1
            else:
                stats.negative_updates += 1
        else:
            stats.positive_updates += int(np.count_nonzero(delta > 0))
            stats.negative_updates += int(np.count_nonzero(delta < 0))
        stats.clipped_low += int(np.count_nonzero((delta < 0) & (updated <= w_min)))
        stats.clipped_high += int(np.count_nonzero((delta > 0) & (updated >= w_max)))
        stats.weight_sum_end = float(updated.sum())

    return {
        "changed": int(np.count_nonzero(delta)),
        "max_abs_delta": float(np.abs(delta).max()) if delta.size else 0.0,
    }


def apply_dopamine(
    network: SimulationNetwork,
    config,
    dopamine: float,
    stats: PlasticityStats | None = None,
    valence: np.ndarray | None = None,
) -> dict[str, Any]:
    """Apply one dopamine-gated weight update to every plastic synapse set.

    The measured mushroom_body -> output set and, once structural plasticity
    has recruited reserve neurons, the mushroom_body -> reserve expansion set
    are both shaped by the same three-factor signal. Only synapses that are
    still active (not pruned, not silenced) are updated.

    When ``valence`` is given (length = number of output categories) the update
    becomes class-specific: every synapse is modulated by ``valence[category]``
    of the category its postsynaptic neuron reads out, instead of by the single
    ``dopamine`` scalar. The scalar ``dopamine`` still reports the trial
    outcome for bookkeeping and for the teacher gate; the valence carries the
    credit assignment.
    """
    if dopamine == 0.0 and valence is None:
        return {"changed": 0, "dopamine": 0.0}
    if not config.get("learning.enabled"):
        return {"changed": 0, "dopamine": dopamine, "skipped": "learning disabled"}

    rate = float(config.get("learning.learning_rate"))
    relative = bool(config.get("learning.relative_bounds", True))

    plastic = network.plastic
    syn = network.synaptic_groups[plastic.role_pair]
    signs = network.synapse_signs.get(plastic.role_pair)
    if signs is None:
        raise LearningError(
            f"no sign vector recorded for plastic role pair {plastic.role_pair!r}"
        )
    plastic_valence = None
    if valence is not None:
        plastic_valence = _valence_for_output_synapses(network, valence, plastic)
    info = _update_plastic_set(
        plastic, syn, signs, dopamine, config, rate, relative, stats,
        valence_by_syn=plastic_valence,
    )
    changed = info["changed"]
    max_delta = info["max_abs_delta"]

    expansion = getattr(network, "expansion_plastic", None)
    esyn = getattr(network, "expansion_syn", None)
    if expansion is not None and esyn is not None and expansion.weights.size:
        # Recruited reserve synapses are purely excitatory, so no sign vector is
        # needed. They are updated with the same dopamine but kept out of the
        # measured-pathway stats so existing plasticity counters keep their
        # meaning.
        expansion_valence = None
        if valence is not None:
            expansion_valence = _valence_for_reserve_synapses(
                network, valence, expansion
            )
        expansion_info = _update_plastic_set(
            expansion, esyn, None, dopamine, config, rate, relative, None,
            valence_by_syn=expansion_valence,
        )
        changed += expansion_info["changed"]
        max_delta = max(max_delta, expansion_info["max_abs_delta"])

    return {
        "changed": changed,
        "dopamine": float(dopamine),
        "max_abs_delta": max_delta,
    }


def _valence_for_output_synapses(
    network: SimulationNetwork, valence: np.ndarray, plastic
) -> np.ndarray:
    """Per-synapse modulation for the measured MB -> output plastic set.

    The plastic set's postsynaptic group IS the readout stage, and a readout
    neuron's local index in that group is exactly its readout position, which
    is its category index. (The stored ``post_indices`` are group-local
    [0,1,2,3], NOT the global ids in ``network.output_indices``, so mapping
    through the global ids would miss every synapse and zero the valence.)
    Synapses onto an out-of-range readout are left neutral (0).
    """
    valence = np.asarray(valence, dtype=np.float64)
    post = np.asarray(plastic.post_indices, dtype=np.int64)
    n = valence.size
    in_range = (post >= 0) & (post < n)
    out = np.zeros(post.size, dtype=np.float64)
    out[in_range] = valence[post[in_range]]
    return out


def _valence_for_reserve_synapses(
    network: SimulationNetwork, valence: np.ndarray, expansion
) -> np.ndarray:
    """Per-synapse modulation for recruited-reserve (MB -> reserve) synapses.

    A recruited neuron inherits its anchor's category, so its incoming reserve
    synapses take the modulation of that category.
    """
    valence = np.asarray(valence, dtype=np.float64)
    categories = getattr(network, "reserve_categories", None)
    post = np.asarray(expansion.post_indices, dtype=np.int64)
    if categories is None:
        return np.zeros(post.size, dtype=np.float64)
    cats = np.asarray(categories, dtype=np.int64)
    idx = np.clip(post, 0, cats.size - 1)
    assigned = cats[idx]
    valid = (assigned >= 0) & (assigned < valence.size) & (post < cats.size)
    out = np.zeros(post.size, dtype=np.float64)
    out[valid] = valence[assigned[valid]]
    return out


def credit_valence(
    predicted: int, true_label: int, n_outputs: int, config
) -> np.ndarray:
    """Per-output credit for one trial, for class-specific learning.

    The scalar three-factor signal assigns ONE global sign per trial, so at
    4-way chance (~75% wrong) punishment depresses everything and never
    reinforces the target the network should have chosen. This returns a
    per-output valence vector so the wrong readout can be punished while the
    correct one is rewarded on the same trial:

    * correct choice: ``+reward`` on the winning (correct) output;
    * wrong choice: ``-punishment`` magnitude on the chosen (wrong) output and
      ``+reward`` on the true one, i.e. a perceptron-style correction;
    * no response: all zeros, so nothing learns from a non-decision.
    """
    reward = float(config.get("learning.reward"))
    punishment = float(config.get("learning.punishment"))
    out = np.zeros(n_outputs, dtype=np.float64)
    if predicted == NO_RESPONSE:
        return out
    if predicted == true_label:
        if 0 <= true_label < n_outputs:
            out[true_label] = reward
    else:
        if 0 <= predicted < n_outputs:
            out[predicted] = punishment
        if 0 <= true_label < n_outputs:
            out[true_label] = reward
    return out


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


def _fit_logreg(
    X: np.ndarray,
    y: np.ndarray,
    n_classes: int,
    iters: int,
    lr: float,
    l2: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Multinomial logistic regression by gradient descent (numpy only).

    Features are standardised first so the L2 penalty treats every Kenyon cell
    on the same footing regardless of its firing scale. Returns the weights
    (including a bias row), the feature means, and the feature scales.
    """
    mu = X.mean(axis=0)
    sd = X.std(axis=0)
    sd = np.where(sd > 0.0, sd, 1.0)
    Xs = np.hstack([(X - mu) / sd, np.ones((X.shape[0], 1))])
    W = np.zeros((Xs.shape[1], n_classes))
    Y = np.eye(n_classes)[y]
    n = Xs.shape[0]
    for _ in range(max(1, iters)):
        z = Xs @ W
        z -= z.max(axis=1, keepdims=True)
        p = np.exp(z)
        p /= p.sum(axis=1, keepdims=True)
        grad = Xs.T @ (p - Y) / n + l2 * W
        W -= lr * grad
    return W, mu, sd


def pretrain_readout(runner, items, labels, config) -> dict[str, Any]:
    """Supervisedly set the plastic readout weights before dopamine training.

    The three-factor rule alone leaves the readout near chance because the
    Kenyon-cell drive is weak and noise-dominated. This step lets a supervised
    classifier tell the readout which Kenyon cells predict which category, and
    writes those coefficients directly onto the plastic mushroom_body -> output
    synapses (respecting their fixed excitatory/inhibitory sign). Dopamine
    learning then fine-tunes from that informative starting point instead of
    from noise.

    Only the plastic synapse set is touched; every other synapse is unchanged.
    """
    plastic = runner.network.plastic
    if plastic is None:
        return {"pretrained": False, "reason": "no plastic synapse set"}
    pre = np.asarray(plastic.pre_indices, dtype=np.int64)
    if pre.size == 0:
        return {"pretrained": False, "reason": "no plastic synapse set"}
    n_classes = len(runner.network.output_indices)
    if len(items) == 0:
        return {"pretrained": False, "reason": "no training items"}

    features = np.stack(
        [runner.readout_features(item.word, i) for i, item in enumerate(items)]
    )
    y = np.asarray(labels, dtype=np.int64)
    W, mu, sd = _fit_logreg(
        features,
        y,
        n_classes,
        int(config.get("learning.pretrain_iters")),
        float(config.get("learning.pretrain_lr")),
        float(config.get("learning.pretrain_l2")),
    )
    standardized = (features - mu) / sd
    scores = standardized @ W[:-1] + W[-1]
    train_accuracy = float((scores.argmax(axis=1) == y).mean())

    coef = W[:-1]
    out_local = [
        runner._output_locations[int(i)][1] for i in runner.network.output_indices
    ]
    position_of_local = {int(local): pos for pos, local in enumerate(out_local)}
    post = np.asarray(plastic.post_indices, dtype=np.int64)
    classes = np.array(
        [position_of_local.get(int(q), 0) for q in post], dtype=np.int64
    )
    desired = coef[pre, classes]
    signs = np.asarray(
        runner.network.synapse_signs[plastic.role_pair], dtype=np.float64
    )
    magnitude = np.maximum(desired * signs, 0.0)

    base = (
        float(plastic.initial_weights.mean())
        if plastic.initial_weights.size else 1.0
    )
    if magnitude.mean() > 0.0:
        magnitude = magnitude * (base / magnitude.mean())
    magnitude = np.clip(magnitude, 0.0, 10.0 * base)

    plastic.weights = magnitude
    plastic.initial_weights = magnitude.copy()
    plastic.trace = np.zeros_like(magnitude)
    syn = runner.network.synaptic_groups[plastic.role_pair]
    syn.w = (signs * magnitude).tolist()
    return {
        "pretrained": True,
        "n_features": int(features.shape[1]),
        "n_synapses": int(magnitude.size),
        "train_accuracy": train_accuracy,
        "mean_weight": float(magnitude.mean()),
    }


def fit_readout_decoder(features, labels, n_classes: int, config):
    """Fit the supervised linear readout over per-trial readout vectors.

    Returns an opaque decoder (weights, feature mean, feature scale) that
    :func:`decoder_predict` consumes. This is the supervised readout the
    evaluation reports against chance when ``evaluation.readout: linear``; the
    raw argmax over four noise-driven output neurons is too weak to separate
    the classes.
    """
    return _fit_logreg(
        np.asarray(features, dtype=np.float64),
        np.asarray(labels, dtype=np.int64),
        n_classes,
        int(config.get("evaluation.decoder_iters")),
        float(config.get("evaluation.decoder_lr")),
        float(config.get("evaluation.decoder_l2")),
    )


def decoder_predict(features, decoder) -> np.ndarray:
    """Predict classes from readout vectors using a fitted decoder."""
    if decoder is None:
        raise LearningError("cannot decode with no fitted readout decoder")
    weights, mu, sd = decoder
    x = (np.asarray(features, dtype=np.float64) - mu) / sd
    return (x @ weights[:-1] + weights[-1]).argmax(axis=1)


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
    last_window: Any = None
    # Trial history restricted to learning trials, for the learning curve.
    training_history: list = field(default_factory=list, repr=False)
    # Per-trial record of how strongly the real reinforcement neurons fired.
    teacher_responses: list = field(default_factory=list, repr=False)
    # Running average of the discrete reward signal, used as the RPE baseline.
    reward_baseline: float = 0.0
    # Cached fixed image -> mushroom_body projection for direct input.
    _direct_projection: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        import brian2 as b2

        self._b2 = b2
        monitors = {
            name: b2.SpikeMonitor(group.group, record=False)
            for name, group in self.network.stages.items()
        }
        # Reserve neurons are monitored too: recruited ones are readout units.
        monitors["reserve"] = b2.SpikeMonitor(self.network.reserve, record=False)
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
        if bool(self.config.get("network.direct_input.enabled")):
            return self._apply_direct_input(darkness)
        rates = darkness_to_rate(darkness, self.config)
        currents = rate_to_current(rates, self.config) * self.input_gain
        group = self._photoreceptor_group()
        group.I_syn = currents.tolist()
        return currents

    def _apply_direct_input(self, darkness: np.ndarray) -> np.ndarray:
        """Project the rendered word straight onto the Kenyon cells.

        A fixed seeded non-negative random projection turns the ink image into a
        Kenyon-cell current that varies monotonically with overall ink, so the
        classification signal reaches the plastic mushroom_body -> output
        readout without passing through the (measured-degenerate) optic lobe.
        """
        d = np.asarray(darkness, dtype=np.float64).ravel()
        if self._direct_projection is None:
            stage = self.network.stages["mushroom_body"]
            rng = np.random.default_rng(
                int(self.config.get("network.direct_input.seed"))
            )
            self._direct_projection = rng.random((int(stage.n), d.size))
        gain = float(self.config.get("network.direct_input.gain", 1.0))
        currents = self._direct_projection @ d / max(d.size, 1)
        base = float(self.config.get("network.background_dc_na"))
        self.network.stages["mushroom_body"].group.I_bg = base + gain * currents
        return currents

    def _clear_stimulus(self) -> None:
        if bool(self.config.get("network.direct_input.enabled")):
            base = float(self.config.get("network.background_dc_na"))
            self.network.stages["mushroom_body"].group.I_bg = base
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
    def _present(self, word: str) -> tuple[np.ndarray, dict[str, float], np.ndarray]:
        """Drive one stimulus window and return the category counts and rates.

        Split out of :meth:`run_trial` so that the same readout (measured
        outputs plus recruited reserve units) can be measured without applying a
        weight update, which is what supervised readout pretraining needs. The
        full per-stage spike-count window is stashed on ``self.last_window`` for
        callers that need the raw pre-synaptic activity.

        Returns ``(counts, stimulus_rate, reserve_features)`` where ``counts``
        is the folded 4-category readout count used by the argmax readout and
        ``reserve_features`` is the per-recruit spike count (length 0 when no
        reserve neuron has been recruited). The decoder consumes both, so a
        recruited neuron is an extra feature dimension the supervised readout
        can weigh rather than noise folded into a category total.
        """
        from .encoding import trial_timing

        import brian2 as b2

        timing = trial_timing(self.config)
        clear_monitors(self._monitors.values())
        count_base = self.snapshot_counts()
        self._apply_stimulus(word)
        self.network.brian.run(timing.stimulus_ms * b2.ms)

        window = self._window_counts(count_base)
        counts = np.zeros(len(self.network.output_indices), dtype=np.int64)
        for position, internal in enumerate(self.network.output_indices):
            stage, local = self._output_locations[int(internal)]
            counts[position] = int(window[stage][local])

        # Recruited reserve neurons are readout units. Each was grown adjacent to
        # the most successful readout unit and inherited its category, so its
        # spikes add to that category's total and can shift the argmax readout.
        expansion = getattr(self.network, "expansion_plastic", None)
        categories = getattr(self.network, "reserve_categories", None)
        reserve_features = np.zeros(0, dtype=np.int64)
        if expansion is not None and expansion.post_indices.size:
            recruited = int(expansion.post_indices.max()) + 1
            reserve_counts = window.get("reserve")
            if reserve_counts is not None:
                n_categories = counts.size
                reserve_features = np.zeros(recruited, dtype=np.int64)
                for unit in range(min(recruited, int(reserve_counts.size))):
                    category = (
                        int(categories[unit])
                        if categories is not None
                        and int(categories[unit]) >= 0
                        and int(categories[unit]) < n_categories
                        else unit % n_categories
                    )
                    counts[category] += int(reserve_counts[unit])
                    reserve_features[unit] = int(reserve_counts[unit])

        # Snapshot per-stage rates while the counts still hold stimulus-time
        # spikes only; the window baseline keeps them from including history.
        stimulus_rate = self.stage_rates_hz(timing.stimulus_ms, count_base)
        self.last_window = window
        return counts, stimulus_rate, reserve_features

    def readout_features(self, word: str, trial_index: int = 0) -> np.ndarray:
        """Spike count per pre-synaptic (Kenyon cell) input to the plastic set.

        The feature vector is indexed by the plastic set's local pre-neuron
        index, so it lines up one-to-one with ``plastic.pre_indices``.
        """
        self.run_trial(word, 0, trial_index, learn=False)
        plastic = self.network.plastic
        pre_stage = plastic.role_pair.split("->")[0]
        pre = np.asarray(plastic.pre_indices, dtype=np.int64)
        n_pre = int(pre.max()) + 1 if pre.size else 1
        window = self.last_window or {}
        resident = window.get(pre_stage)
        if resident is None or not pre.size:
            return np.zeros(n_pre, dtype=np.float64)
        resident = np.asarray(resident, dtype=np.float64)
        return np.bincount(pre, weights=resident[pre], minlength=n_pre)

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

        counts, stimulus_rate, reserve_features = self._present(word)

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
            credit_mode = str(self.config.get("learning.credit_assignment", "global"))
            if credit_mode == "per_class":
                # Per-output credit: the wrong readout is punished while the
                # correct one is rewarded on the SAME trial. The teacher gate
                # still comes from real reinforcement-neuron spiking, so the
                # update is still gated by a biological three-factor signal.
                teacher_gate = self._deliver_teaching(dopamine, trial_index)
                valence = credit_valence(
                    result.predicted, true_label, len(counts), self.config
                )
                valence = valence * teacher_gate
                apply_dopamine(
                    self.network, self.config,
                    dopamine * teacher_gate, self.stats, valence=valence,
                )
                dopamine = dopamine * teacher_gate
            else:
                delivered = dopamine
                if bool(self.config.get("learning.reward_baseline", True)):
                    delivered = self._prediction_error(dopamine)
                teacher_gate = self._deliver_teaching(delivered, trial_index)
                dopamine = delivered * teacher_gate
                apply_dopamine(self.network, self.config, dopamine, self.stats)
        else:
            dopamine = 0.0
            reward_type = "none"

        self.last_stimulus_rates = stimulus_rate
        self.last_stimulus_counts = counts

        feature_counts = np.concatenate([counts, reserve_features])

        record = TrialRecord(
            trial=trial_index,
            word=word,
            true_label=true_label,
            predicted=result.predicted,
            correct=(not result.is_no_response) and result.predicted == true_label,
            dopamine=float(dopamine),
            spike_counts=feature_counts.tolist(),
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

    def _prediction_error(self, dopamine: float) -> float:
        """Reward prediction error: reward minus a running expected reward.

        The discrete signal is +1 (reward), -1 (punishment) or 0 (no response).
        A running average tracks the expected value, and the error actually
        delivered is ``signal - expected``. Centering the signal is what lets
        punishment decrease weights only when the outcome is worse than
        expected, instead of depressing every synapse on the ~75% of
        chance-level trials the 4-way readout gets wrong.
        """
        rate = float(self.config.get("learning.baseline_rate", 0.02))
        error = dopamine - self.reward_baseline
        self.reward_baseline += rate * (dopamine - self.reward_baseline)
        return error

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
            # Aversive valence lives in the dopamine VALUE (-1), not in the
            # polarity of the injected current. Driving the reinforcement
            # neurons with a negative current hyperpolarizes them below the LIF
            # threshold, producing zero spikes, a gate of 0.0, and hence a
            # zeroed dopamine that `apply_dopamine` early-returns on: the
            # punishment arm of the three-factor rule is then dead and weights
            # can only ever grow. Fire them so the negative dopamine reaches
            # the plastic weights and can decrease them.
            current = float(config.get("network.punishment_gain_na")) * abs(dopamine)
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
        # Recruited reserve neurons carry their own plastic set; reset it too so
        # eligibility does not leak across trials.
        expansion = getattr(self.network, "expansion_plastic", None)
        esyn = getattr(self.network, "expansion_syn", None)
        if expansion is not None and esyn is not None and len(esyn) > 0:
            esyn.elig = 0.0
            expansion.trace = np.zeros_like(expansion.trace)
        # Release probability is a documented per-trial baseline, not carried
        # over: a synapse left depleted from the previous word would bias the
        # next word's response toward whatever it was used for.
        if bool(self.config.get("network.short_term_depression.enabled", True)):
            groups = list(self.network.synaptic_groups.values())
            if esyn is not None and len(esyn) > 0:
                groups.append(esyn)
            for group in groups:
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