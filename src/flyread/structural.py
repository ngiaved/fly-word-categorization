"""Exploratory structural plasticity: pruning, silencing, recruitment.

This capability is original design with no published precedent. It is a
modelling mechanism, not a biological claim about neurogenesis.

Brian2 ``NeuronGroup`` objects cannot change size at runtime, so the reserve
pool is pre-allocated at construction with no synapses. Recruitment connects
those neurons; silencing disconnects them; pruning zeroes a weight and removes
the synapse from effect. Every event is logged with its trial number.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .network import PlasticSynapses, SimulationNetwork
from .repro import make_rng

LOGGER = logging.getLogger(__name__)

PRUNE = "prune"
SILENCE = "silence"
RECRUIT = "recruit"


class StructuralError(RuntimeError):
    """Raised when structural plasticity is asked to do something impossible."""


@dataclass
class StructuralEvent:
    """One logged structural change."""

    trial: int
    kind: str
    detail: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(
            {"trial": self.trial, "kind": self.kind, **self.detail},
            sort_keys=True, default=str,
        )


@dataclass
class StructuralStats:
    """Counts and caps, reported in the manifest."""

    intervals: int = 0
    prunes: int = 0
    silences: int = 0
    recruits: int = 0
    pruned_total: int = 0
    # Prunes that landed on recruited-reserve synapses rather than the measured
    # plastic pathway. Kept separate so the recessive pruning budget can be
    # tracked against the number of recruited neurons.
    expansion_pruned_total: int = 0
    # Deletions forced by the retirement rule to track additions even when the
    # weight-threshold gate has nothing below it. A subset of
    # expansion_pruned_total.
    retired_total: int = 0
    silenced_total: int = 0
    reserve_available: int = 0
    reserve_warning_logged: bool = False
    cap_hits_prune: int = 0
    cap_hits_silence: int = 0
    cap_hits_recruit: int = 0
    candidate_totals: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "intervals": self.intervals,
            "prunes": self.prunes,
            "silences": self.silences,
            "recruits": self.recruits,
            "pruned_total": self.pruned_total,
            "expansion_pruned_total": self.expansion_pruned_total,
            "retired_total": self.retired_total,
            "silenced_total": self.silenced_total,
            "reserve_available": self.reserve_available,
            "reserve_warning_logged": self.reserve_warning_logged,
            "cap_hits": {
                "prune": self.cap_hits_prune,
                "silence": self.cap_hits_silence,
                "recruit": self.cap_hits_recruit,
            },
            "candidate_totals": self.candidate_totals,
        }


class StructuralPlasticity:
    """Applies pruning, silencing, and recruitment under explicit rate limits."""

    def __init__(
        self,
        network: SimulationNetwork,
        config,
        seed: int = 0,
    ) -> None:
        self.network = network
        self.config = config
        self.seed = int(seed)
        self.enabled = bool(config.get("structural.enabled"))
        self.events: list[StructuralEvent] = []
        self.stats = StructuralStats()

        self.check_every = int(config.get("structural.check_every_n_trials"))
        self.prune_threshold = float(config.get("structural.prune_weight_threshold"))
        self.prune_checks = int(config.get("structural.prune_consecutive_checks"))
        self.silence_threshold = float(config.get("structural.silence_rate_threshold_hz"))
        self.silence_window = int(config.get("structural.silence_window_trials"))
        self.recruit_every = int(config.get("structural.recruit_every_n_checks"))
        self.recruit_noise = float(config.get("structural.recruit_noise"))
        # Pruning threshold as a fraction of each synapse's own initial weight.
        # Scale-independent, so it bites in the calibrated regime where absolute
        # magnitudes are ~1 rather than ~0.01.
        self.prune_weight_ratio = float(
            config.get("structural.prune_weight_ratio", 0.0)
        )
        # Deletion target: pruned synapses per recruited neuron. 0.5 means
        # deletions track half of additions.
        self.prune_per_recruit = float(
            config.get("structural.prune_per_recruit", 0.0)
        )
        self.max_prunes = int(config.get("structural.max_prunes_per_interval"))
        self.max_silences = int(config.get("structural.max_silences_per_interval"))
        self.max_recruits = int(config.get("structural.max_recruits_per_interval"))
        self.max_fraction_pruned = float(config.get("structural.max_total_fraction_pruned"))
        self.max_fraction_silenced = float(
            config.get("structural.max_total_fraction_silenced")
        )

        rec = config.get("structural.recruitment") or {}
        self.divergence = float(rec.get("divergence", 0.85))
        self.anchor_top_fraction = float(rec.get("anchor_top_fraction", 0.25))
        self.success_window = int(rec.get("success_window", 200))
        self.enforce_unique = bool(rec.get("enforce_unique_inputs", True))
        self.gain_mode = str(rec.get("gain", "average"))
        self.force_retire = bool(rec.get("force_retire", True))
        self.retirement_min_age = int(rec.get("retirement_min_age", 1))

        # Input-set signatures ever used, so "no two neurons look the same".
        self._input_signatures: set[frozenset[int]] = set()
        # Per-synapse intervals since recruitment, so retirement spares brand-
        # new neurons until they have had a training window. One entry per
        # synapse in ``network.expansion_plastic``.
        self._retire_age: np.ndarray = np.zeros(0, dtype=np.int64)

        self.plastic = network.plastic
        self.syn = network.synaptic_groups[self.plastic.role_pair]
        self.total_plastic = int(self.plastic.weights.size)
        self._below_count: np.ndarray | None = None
        self._activity_window: dict[str, list[np.ndarray]] = {}
        self.trial_seconds = float(
            self.config.get("encoding.stimulus_ms")
        ) * 1e-3
        self._silent_neurons: set[int] = set()
        self._recruited = 0
        self.stats.reserve_available = int(network.reserve.N)
        # Per-synapse consecutive-below-threshold counters for the recruited
        # reserve synapse set, kept separate from the measured set's counters.
        self._expansion_below_count: np.ndarray | None = None
        # Readout categories. Recruited reserve neurons join these as extra
        # readout units, assigned the category of their anchor (the most
        # successful output unit), so growth reinforces the winning class
        # instead of injecting round-robin noise.
        self.n_categories = int(len(network.output_indices)) or 1
        # Rolling winner-conditional reward per output unit; drives anchoring.
        self._winner_ema: np.ndarray = np.zeros(self.n_categories, dtype=np.float64)
        self._winner_seen: np.ndarray = np.zeros(self.n_categories, dtype=np.int64)
        # Per-reserve-unit readout category ledger, shared with the readout so
        # it credits a recruited neuron to the correct category. -1 = unborn.
        network.reserve_categories = np.full(
            int(network.reserve.N), -1, dtype=np.int64
        )

        self._reserve_stage = self._make_reserve_stage()
        self._reserve_stage_attached = False
        self._rng = make_rng(self.seed, stream="structural")

    # -- reserve pool ---------------------------------------------------
    def _make_reserve_stage(self):
        """The plastic ``upstream -> reserve`` synapse set for recruited neurons.

        This is the plasticity locus for recruited neurons: it carries the same
        eligibility trace as the measured ``mushroom_body -> output`` pair, so
        dopamine reshapes a new neuron's input weights exactly as it reshapes
        the measured readout. Recruited neurons are counted in the readout, so
        they are full output units rather than inert wiring.
        """
        import brian2 as b2

        source_stage = self._upstream_stage()
        if source_stage is None:
            return None
        source = self.network.stages[source_stage].group

        trace_tau = float(self.config.get("learning.trace_tau_ms"))
        trace_increment = float(self.config.get("learning.trace_increment"))
        std_tau = float(self.config.get("network.short_term_depression.tau_ms", 50.0))
        std_use = float(self.config.get("network.short_term_depression.use", 0.3))

        # Mirror the measured plastic pair exactly: the same eligibility trace
        # and the same short-term depression variable, so a recruited neuron's
        # input drive and plasticity dynamics match the measured readout.
        model = (
            "w : 1\n"
            "delig/dt = -elig/trace_tau : 1 (clock-driven)\n"
            "du/dt = (u_rest - u)/tau_u : 1 (clock-driven)"
        )
        namespace = {
            "trace_tau": trace_tau * b2.ms,
            "trace_inc": trace_increment,
            "tau_u": max(std_tau, 1.0) * b2.ms,
            "u_rest": 1.0,
            "use": std_use,
        }

        syn = b2.Synapses(
            source, self.network.reserve, model=model,
            on_pre="I_syn += w * u\nelig += trace_inc\nu -= use",
            on_post="elig += trace_inc",
            method="euler", namespace={**namespace, "v_rest": 0.0},
        )
        # The reserve pool is born unconnected, and Brian2 refuses to run an
        # empty Synapses object. It is therefore NOT added to the Brian network
        # here; the first recruitment connects it and attaches it (see
        # _recruit). Merely flipping `active` after adding was not enough,
        # because the network had already snapshotted the synapse list.
        syn.active = False

        expansion = PlasticSynapses(
            pre_indices=np.array([], dtype=np.int64),
            post_indices=np.array([], dtype=np.int64),
            role_pair=f"{source_stage}->reserve",
            weights=np.array([], dtype=np.float64),
            initial_weights=np.array([], dtype=np.float64),
            trace=np.array([], dtype=np.float64),
            active=np.array([], dtype=bool),
            n_synapses_each=np.array([], dtype=np.int64),
        )
        self.network.expansion_plastic = expansion
        self.network.expansion_syn = syn
        return syn

    def _upstream_stage(self) -> str | None:
        plastic_pair = self.plastic.role_pair
        upstream = plastic_pair.split("->")[0]
        return upstream if upstream in self.network.stages else None

    @property
    def reserve_available(self) -> int:
        """Reserve neurons not yet recruited.

        Derived from the physical pool size and the recruited count so there is
        exactly one source of truth. Subtracting from the running stat as well
        would count each recruitment twice and report exhaustion early.
        """
        return max(0, int(self.network.reserve.N) - self._recruited)

    # -- observation ----------------------------------------------------
    def set_trial_duration(self, seconds: float) -> None:
        """Duration of one trial, used to convert spike counts to rates."""
        self.trial_seconds = float(seconds)

    def observe(self, stage_rate_hz: dict[str, float]) -> None:
        """Record per-stage mean firing rates for the silencing window.

        Accepts rates in Hz, as snapshotted during the stimulus window by
        ``TrialRunner.run_trial``. A stage mean is the conservative choice here:
        silencing operates on whole neurons, and the spec thresholds on a mean
        rate, so per-neuron detail is not needed to make the decision.
        """
        for stage in self.config.get("structural.plasticity_roles"):
            if stage not in stage_rate_hz:
                continue
            self._activity_window.setdefault(stage, []).append(
                float(stage_rate_hz[stage])
            )

    def observe_trial(self, responder: int | None, reward_type: str | None) -> None:
        """Record one trial's winner-conditional reward for anchoring.

        ``responder`` is the argmax output unit that produced the response and
        ``reward_type`` is the pre-baseline teacher outcome ("reward",
        "punishment", or "none"). A rolling EMA per output unit scores how
        successful that readout unit has been, which recruitment uses to pick
        its anchor. Called once per trial by the evaluation loop.
        """
        if responder is None or responder < 0:
            return
        unit = int(responder)
        if unit >= self.n_categories:
            return
        value = {"reward": 1.0, "punishment": -1.0}.get(reward_type, 0.0)
        alpha = 1.0 / self.success_window if self.success_window > 0 else 0.0
        self._winner_seen[unit] += 1
        self._winner_ema[unit] = (1.0 - alpha) * self._winner_ema[unit] + alpha * value

    # -- the step -------------------------------------------------------
    def maybe_step(self, trial: int) -> list[StructuralEvent]:
        """Run one structural check if this trial is on the schedule."""
        if not self.enabled or self.check_every <= 0:
            return []
        if trial % self.check_every != 0:
            return []
        return self.step(trial)

    def step(self, trial: int) -> list[StructuralEvent]:
        """One structural interval: prune, then silence, then maybe recruit.

        Recruitment runs before the reserve-pruning pass so a neuron recruited
        this interval can have its synapses reclaimed by a later interval; the
        consecutive-check rule keeps it from being pruned on first sight.
        """
        self.stats.intervals += 1
        produced: list[StructuralEvent] = []
        # Age every reserve synapse before this interval's recruitment, so a
        # freshly added neuron must survive a full interval before retirement.
        expansion = getattr(self.network, "expansion_plastic", None)
        if expansion is not None and expansion.active.size:
            if self._retire_age.size != expansion.active.size:
                self._retire_age = np.zeros(
                    expansion.active.size, dtype=np.int64
                )
            size = int(expansion.active.size)
            self._retire_age[:size] += expansion.active[:size]
        produced.extend(self._prune(trial))
        produced.extend(self._silence(trial))
        if self.recruit_every > 0 and self.stats.intervals % self.recruit_every == 0:
            produced.extend(self._recruit(trial))
        produced.extend(self._prune_expansion(trial))
        self.events.extend(produced)
        return produced

    # -- pruning --------------------------------------------------------
    def _prune_candidates(self) -> np.ndarray:
        """Plastic synapses below threshold, respecting the total-change cap."""
        weights = self.plastic.weights
        active = self.plastic.active
        # Keep the boolean mask and the index array separate. ``below`` holds
        # indices, so ``~below`` is a bitwise complement (negative indices),
        # not the set of synapses that are NOT below threshold.
        below_mask = (weights < self.prune_threshold) & active
        below = np.flatnonzero(below_mask)
        self.stats.candidate_totals["prune"] = int(below.size)

        # Consecutive-check state is tracked PER SYNAPSE. A single global
        # counter would let a synapse that only just dropped below threshold be
        # pruned on its first check, which is not what the spec asks for.
        if self._below_count is None or len(self._below_count) != weights.size:
            self._below_count = np.zeros(weights.size, dtype=np.int64)
        self._below_count[below] += 1
        self._below_count[~below_mask] = 0

        if self.prune_checks > 0:
            eligible = np.flatnonzero(
                (self._below_count >= self.prune_checks) & active
            )
        else:
            eligible = below
        if eligible.size == 0:
            return np.array([], dtype=np.int64)

        allowed = int(
            np.floor(self.max_fraction_pruned * self.total_plastic)
        )
        remaining = allowed - self.stats.pruned_total
        if remaining <= 0:
            return np.array([], dtype=np.int64)

        chosen = self._cap(eligible, self.max_prunes, remaining, self.stats, "prune")
        return chosen

    def _prune(self, trial: int) -> list[StructuralEvent]:
        chosen = self._prune_candidates()
        if chosen.size == 0:
            return []
        pre = self.plastic.pre_indices[chosen]
        post = self.plastic.post_indices[chosen]
        weights_before = self.plastic.weights[chosen].copy()
        self.plastic.weights[chosen] = 0.0
        self.plastic.active[chosen] = False
        self.syn.w = self.plastic.weights.tolist()
        self.stats.prunes += int(chosen.size)
        self.stats.pruned_total += int(chosen.size)
        events = []
        for synapse_id, pre_id, post_id, w_before in zip(
            chosen.tolist(), pre.tolist(), post.tolist(), weights_before.tolist()
        ):
            events.append(StructuralEvent(
                trial=trial, kind=PRUNE,
                detail={
                    "synapse_id": int(synapse_id),
                    "pre_index": int(pre_id), "post_index": int(post_id),
                    "weight_before": float(w_before), "weight_after": 0.0,
                    "threshold": self.prune_threshold,
                    "consecutive_checks": self.prune_checks,
                },
            ))
        LOGGER.info("trial %d: pruned %d synapses", trial, chosen.size)
        return events

    # -- recruitment pruning --------------------------------------------
    def _prune_expansion(self, trial: int) -> list[StructuralEvent]:
        """Prune recruited-reserve synapses below a fraction of their own
        initial weight, budgeted so deletions track a fraction of additions.

        The measured plastic pathway is tiny (~200 synapses in the calibrated
        network), so removing "half the added neurons" cannot be expressed as a
        fraction of it. Instead the reserve synapses are prunable and are
        budgeted against the number of recruited neurons: once ``recruits``
        neurons have been added, up to ``prune_per_recruit * recruits`` of
        their input synapses are reclaimed. A recruited neuron whose input
        synapses are all reclaimed is effectively deleted.
        """
        expansion = self.network.expansion_plastic
        if expansion is None or expansion.weights.size == 0:
            return []
        if self.prune_per_recruit <= 0.0 or self.prune_weight_ratio <= 0.0:
            return []

        weights = expansion.weights
        active = expansion.active
        threshold = self.prune_weight_ratio * expansion.initial_weights
        below_mask = (weights < threshold) & active
        below = np.flatnonzero(below_mask)
        self.stats.candidate_totals["prune_reserve"] = int(below.size)

        if self._retire_age.size != weights.size:
            self._retire_age = np.zeros(weights.size, dtype=np.int64)

        if self._expansion_below_count is None or (
            len(self._expansion_below_count) != weights.size
        ):
            self._expansion_below_count = np.zeros(weights.size, dtype=np.int64)
        self._expansion_below_count[below] += 1
        self._expansion_below_count[~below_mask] = 0

        if self.prune_checks > 0:
            eligible = np.flatnonzero(
                (self._expansion_below_count >= self.prune_checks) & active
            )
        else:
            eligible = below
        target = int(np.floor(self.prune_per_recruit * self._recruited))
        remaining = target - self.stats.expansion_pruned_total
        if remaining <= 0:
            return []

        chosen = (
            self._cap(eligible, self.max_prunes, remaining, self.stats, "prune")
            if eligible.size else np.array([], dtype=np.int64)
        )

        # Forced retirement: with pseudorandom average-gain recruits, the
        # weight-threshold gate rarely fires (recruits bootstrap near the
        # population mean), so deletions would stay far below additions. When
        # force_retire is on, top up the interval budget with the weakest
        # active reserve synapses so deletions deterministically track
        # ``prune_per_recruit`` additions.
        forbid = set(int(i) for i in chosen)
        retired = np.array([], dtype=np.int64)
        if self.force_retire and int(chosen.size) < self.max_prunes:
            budget = min(
                self.max_prunes - int(chosen.size),
                max(0, int(remaining) - int(chosen.size)),
            )
            if budget > 0:
                mature = np.flatnonzero(
                    active & (self._retire_age >= self.retirement_min_age)
                )
                candidates = np.array(
                    [i for i in mature if i not in forbid], dtype=np.int64
                )
                if candidates.size:
                    order = np.argsort(weights[candidates], kind="stable")
                    retired = candidates[order][:budget]
                    if retired.size:
                        chosen = np.concatenate([chosen, retired])

        if chosen.size == 0:
            return []

        weights_before = weights[chosen].copy()
        weights[chosen] = 0.0
        active[chosen] = False
        self._reserve_stage.w = weights.tolist()
        self.stats.prunes += int(chosen.size)
        self.stats.pruned_total += int(chosen.size)
        self.stats.expansion_pruned_total += int(chosen.size)
        self.stats.retired_total += int(retired.size)

        retired_set = set(int(i) for i in retired)
        events = []
        for position, synapse_id in enumerate(chosen.tolist()):
            events.append(StructuralEvent(
                trial=trial, kind=PRUNE,
                detail={
                    "synapse_id": int(synapse_id),
                    "set": "reserve",
                    "pre_index": int(expansion.pre_indices[synapse_id]),
                    "post_index": int(expansion.post_indices[synapse_id]),
                    "weight_before": float(weights_before[position]),
                    "weight_after": 0.0,
                    "threshold_ratio": (
                        self.prune_weight_ratio
                        if synapse_id not in retired_set else None
                    ),
                    "consecutive_checks": (
                        self.prune_checks if synapse_id not in retired_set else 0
                    ),
                    "retired": synapse_id in retired_set,
                },
            ))
        LOGGER.info(
            "trial %d: pruned %d reserve synapses (%d by retirement)",
            trial, chosen.size, int(retired.size),
        )
        return events

    # -- silencing ------------------------------------------------------
    def _silence_candidates(self) -> list[tuple[str, int]]:
        """Plastic-pathway neurons whose mean rate is below the threshold.

        Rates are mean spikes per second over the configured trial window, so
        the threshold is comparable to the calibration firing rates.
        """
        candidates: list[tuple[str, int]] = []
        if self.trial_seconds <= 0:
            return candidates
        for stage, samples in self._activity_window.items():
            group = self.network.stages.get(stage)
            if group is None or not samples:
                continue
            # Samples are already stage mean rates in Hz.
            window = samples[-self.silence_window:]
            mean_rate = float(np.mean(window))
            if mean_rate >= self.silence_threshold:
                continue
            for local in range(group.n):
                internal = int(group.indices[local])
                if internal in self._silent_neurons:
                    continue
                candidates.append((stage, internal))
        return candidates

    def _silence(self, trial: int) -> list[StructuralEvent]:
        if self.silence_window <= 0:
            return []
        candidates = self._silence_candidates()
        self.stats.candidate_totals["silence"] = len(candidates)
        if not candidates:
            self._activity_window.clear()
            return []

        total_eligible = sum(
            int(self.network.stages[stage].n)
            for stage in self.config.get("structural.plasticity_roles")
            if stage in self.network.stages
        )
        allowed = int(np.floor(self.max_fraction_silenced * max(1, total_eligible)))
        remaining = allowed - self.stats.silenced_total
        if remaining <= 0:
            self._activity_window.clear()
            return []

        flat = np.array(
            [stage_index * 10_000_000 + internal
             for stage_index, (stage, internal) in enumerate(candidates)],
            dtype=np.int64,
        )
        chosen_flat = self._cap(flat, self.max_silences, remaining, self.stats, "silence")
        self._activity_window.clear()
        if chosen_flat.size == 0:
            return []

        events: list[StructuralEvent] = []
        for code in chosen_flat.tolist():
            stage_index, internal = divmod(int(code), 10_000_000)
            stage, _ = candidates[stage_index]
            touched = self._disable_neuron(internal)
            self._silent_neurons.add(internal)
            self.stats.silences += 1
            self.stats.silenced_total += 1
            events.append(StructuralEvent(
                trial=trial, kind=SILENCE,
                detail={
                    "stage": stage, "neuron_index": internal,
                    "synapses_disabled": touched,
                    "rate_threshold_hz": self.silence_threshold,
                    "window_trials": self.silence_window,
                },
            ))
        LOGGER.info("trial %d: silenced %d neurons", trial, chosen_flat.size)
        return events

    def _disable_neuron(self, internal: int) -> int:
        """Zero every plastic synapse touching a neuron and mark it inactive."""
        pre_match = np.flatnonzero(self.plastic.pre_indices == internal)
        post_match = np.flatnonzero(self.plastic.post_indices == internal)
        targets = np.unique(np.concatenate([pre_match, post_match]))
        if targets.size:
            self.plastic.weights[targets] = 0.0
            self.plastic.active[targets] = False
            self.syn.w = self.plastic.weights.tolist()
        return int(targets.size)

    # -- recruitment ----------------------------------------------------
    def _anchor_category(self) -> int:
        """Index of the most successful readout unit (rolling reward).

        Only outputs that have actually responded are eligible; before any
        reinforceable trials the first output is the deterministic fallback.
        """
        eligible = self._winner_seen > 0
        if not np.any(eligible):
            return 0
        score = np.where(eligible, self._winner_ema, -np.inf)
        return int(np.argmax(score))

    def _winner_sources(self, category: int) -> np.ndarray:
        """Upstream inputs currently driving the anchor readout unit.

        The presynaptic (Kenyon cell) indices of the measured plastic synapses
        that terminate on the anchor output unit. New recruits are drawn to
        overlap at most (1 - divergence) with these.
        """
        plastic = self.plastic
        if plastic.weights.size == 0:
            return np.array([], dtype=np.int64)
        idx = np.flatnonzero(plastic.post_indices == category)
        if idx.size == 0:
            return np.array([], dtype=np.int64)
        return np.unique(plastic.pre_indices[idx])

    def _sample_sources(
        self,
        pool: np.ndarray,
        winner_sources: np.ndarray,
        n_syn: int,
        divergence: float,
    ) -> np.ndarray:
        """Pseudo-random source set adjacent to the anchor's inputs.

        Keeps at most ``floor((1 - divergence) * n_syn)`` of the anchor's own
        inputs and fills the rest from fresh pool draws, so the recruit
        resembles the most successful unit structurally but stays far enough
        from it (> divergence) to be its own neuron.
        """
        if n_syn <= 0:
            return np.array([], dtype=np.int64)
        if winner_sources.size == 0:
            rng = self._rng
            return np.sort(rng.choice(pool, size=n_syn, replace=False))
        max_overlap = int(np.floor((1.0 - divergence) * n_syn))
        max_overlap = min(max_overlap, int(winner_sources.size))
        rng = self._rng
        base = (
            rng.choice(winner_sources, size=max_overlap, replace=False)
            if max_overlap > 0
            else np.array([], dtype=np.int64)
        )
        # The complement excludes ALL anchor inputs, not just the kept ones, so
        # an extra draw can never re-import another anchor input and silently
        # cut the achieved divergence.
        winner_members = set(int(s) for s in winner_sources)
        non = pool[np.isin(pool, list(winner_members), invert=True)]
        n_new = n_syn - base.size
        extra = (
            rng.choice(non, size=min(n_new, non.size), replace=False)
            if n_new > 0 and non.size > 0
            else np.array([], dtype=np.int64)
        )
        return np.sort(np.unique(np.concatenate([base, extra])))

    def _remarkable_source_set(
        self,
        pool: np.ndarray,
        winner_sources: np.ndarray,
        n_syn: int,
        divergence: float,
    ) -> tuple[np.ndarray | None, float]:
        """The ``(sources, achieved_divergence)`` pair for one new neuron.

        Enforces the input-signature ledger so no two recruits ever share an
        identical source set ("no two neurons look the same"). Collisions are
        retried with ever-higher divergence; returns (None, 0.0) if the pool is
        too small to produce a unique set.
        """
        attempts = int(self.config.get("structural.recruit_uniqueness_retries", 8)) if self.enforce_unique else 1
        for attempt in range(attempts):
            div = divergence if attempt == 0 else 1.0
            sources = self._sample_sources(pool, winner_sources, n_syn, div)
            signature = frozenset(int(s) for s in sources)
            if not self.enforce_unique or signature not in self._input_signatures:
                self._input_signatures.add(signature)
                achieved = (
                    0.0
                    if winner_sources.size == 0
                    else 1.0 - len(signature.intersection(int(s) for s in winner_sources)) / n_syn
                )
                return sources, achieved
        return None, 0.0

    def _recruit(self, trial: int) -> list[StructuralEvent]:
        if self._reserve_stage is None:
            LOGGER.warning("recruitment skipped: no upstream stage for the reserve pool")
            return []
        available = self.reserve_available
        if available <= 0:
            if not self.stats.reserve_warning_logged:
                LOGGER.warning(
                    "reserve pool exhausted at trial %d; "
                    "no further recruitment will occur", trial,
                )
                self.stats.reserve_warning_logged = True
            self.stats.candidate_totals["recruit"] = 0
            return []
        self.stats.candidate_totals["recruit"] = int(available)

        # Never recruit past the size of the reserve group.
        # Hard bounds: the per-interval cap, the remaining budget, and the
        # physical size of the reserve group. Whichever is smallest wins.
        headroom = int(self.network.reserve.N) - self._recruited
        n_new = min(self.max_recruits, available, max(0, headroom))
        if n_new <= 0:
            LOGGER.warning(
                "trial %d: reserve pool full (%d/%d recruited); no further "
                "recruitment", trial, self._recruited, self.network.reserve.N,
            )
            return []
        n_new = self._cap_count(n_new, self.stats, "recruit")
        if n_new <= 0:
            return []

        source_stage, pool = self._source_pool()
        if source_stage is None or pool.size == 0:
            LOGGER.info(
                "trial %d: no upstream neurons are available as sources; "
                "cannot recruit", trial,
            )
            return []

        # Population the new neurons are drawn to resemble: the fan-in and the
        # INITIAL synaptic weights of the measured plastic pathway. New neurons
        # grow adjacent to the most successful readout unit (anchor): they
        # inherit its category and a small fraction of its inputs, but draw the
        # rest of their inputs pseudo-randomly (>= divergence) and bootstraps
        # their gain at the population average. The initial weights are used
        # rather than the current ones so recruitment does not inherit a
        # collapsed (all-zero) population and wire up dead neurons.
        population_weights = np.asarray(
            self.plastic.initial_weights, dtype=np.float64
        )
        if population_weights.size == 0:
            population_weights = self.plastic.weights
        if population_weights.size == 0:
            LOGGER.info("trial %d: no plastic weights to match", trial)
            return []
        post_counts = np.bincount(
            self.plastic.post_indices,
            minlength=int(self.plastic.post_indices.max()) + 1
            if self.plastic.post_indices.size else 1,
        )
        post_counts = post_counts[post_counts > 0]
        population_mean = float(population_weights.mean())
        population_std = float(population_weights.std())

        anchor_category = self._anchor_category()
        winner_sources = self._winner_sources(anchor_category)

        events: list[StructuralEvent] = []
        for _offset in range(n_new):
            # _recruited advances once per neuron below, so it is the index
            # directly; adding an offset here as well would skip neurons and
            # eventually index past the end of the reserve group.
            reserve_local = self._recruited
            if not 0 <= reserve_local < int(self.network.reserve.N):
                LOGGER.warning(
                    "trial %d: reserve index %d is outside the reserve pool of "
                    "size %d; skipping recruitment",
                    trial, reserve_local, int(self.network.reserve.N),
                )
                return events

            # Fan-in: draw a synapse count from the population's per-neuron
            # distribution, then pick that many DISTINCT presynaptic sources
            # adjacent to the anchor's own inputs (psuedo-random, >= divergence
            # different) without replacement.
            n_syn = int(self._rng.choice(post_counts)) if post_counts.size else 1
            n_syn = max(1, min(n_syn, pool.size))
            sources, achieved = self._remarkable_source_set(
                pool, winner_sources, n_syn, self.divergence
            )
            if sources is None or sources.size == 0:
                LOGGER.warning(
                    "trial %d: could not find a unique input set for recruit "
                    "%d; skipping", trial, self._recruited,
                )
                continue
            n_syn = int(sources.size)
            # Weights: the recruit starts at the population-average gain with
            # small multiplicative noise, so its initial drive is "average" and
            # tuning (plasticity) does the rest.
            base = float(population_mean)
            noise = 1.0 + self.recruit_noise * self._rng.standard_normal(n_syn)
            new_weights = np.clip(base * noise, 0.0, None)

            self._reserve_stage.connect(
                i=sources.tolist(),
                j=[reserve_local] * n_syn,
            )
            self._reserve_stage.active = True
            if not self._reserve_stage_attached:
                # The reserve synapse is connected for the first time; attach it
                # to the Brian network so it actually runs and accumulates the
                # eligibility trace that dopamine reads.
                self.network.brian.add(self._reserve_stage)
                self._reserve_stage_attached = True
            start = len(self._reserve_stage) - n_syn
            self._reserve_stage.w[start:] = new_weights.tolist()

            # Track the new synapses in the expansion plastic set so dopamine
            # can shape them and pruning can reclaim them.
            expansion = self.network.expansion_plastic
            expansion.pre_indices = np.concatenate(
                [expansion.pre_indices, sources.astype(np.int64)]
            )
            expansion.post_indices = np.concatenate(
                [expansion.post_indices,
                 np.full(n_syn, reserve_local, dtype=np.int64)]
            )
            expansion.weights = np.concatenate(
                [expansion.weights, new_weights.astype(np.float64)]
            )
            expansion.initial_weights = np.concatenate(
                [expansion.initial_weights, new_weights.astype(np.float64)]
            )
            expansion.trace = np.concatenate(
                [expansion.trace, np.zeros(n_syn, dtype=np.float64)]
            )
            expansion.active = np.concatenate(
                [expansion.active, np.ones(n_syn, dtype=bool)]
            )
            expansion.n_synapses_each = np.concatenate(
                [expansion.n_synapses_each,
                 np.full(n_syn, 1, dtype=np.int64)]
            )
            # `_retire_age` mirrors `expansion.active` one-to-one; both grow by
            # exactly `n_syn` here (the active array was already appended above),
            # so append in lockstep rather than resyncing to the grown size.
            self._retire_age = np.concatenate(
                [self._retire_age, np.zeros(n_syn, dtype=np.int64)]
            )

            category = anchor_category
            self.network.reserve_categories[reserve_local] = int(category)
            self._recruited += 1
            events.append(StructuralEvent(
                trial=trial, kind=RECRUIT,
                detail={
                    "source_stage": source_stage,
                    "new_neuron_index": int(reserve_local),
                    "readout_category": category,
                    "anchor_category": anchor_category,
                    "divergence": float(achieved),
                    "n_synapses": int(n_syn),
                    "source_indices": [int(s) for s in sources],
                    "noise": self.recruit_noise,
                    "mean_weight": float(np.mean(new_weights)),
                    "std_weight": float(np.std(new_weights)),
                    "population_mean_weight": population_mean,
                    "population_std_weight": population_std,
                },
            ))
        self.stats.recruits += n_new
        # Keep the stat in step with reality for reporting. The
        # ``reserve_available`` property recomputes from the pool size and the
        # recruited count, so it must not be derived from this field as well.
        self.stats.reserve_available = self.reserve_available
        LOGGER.info("trial %d: recruited %d reserve neurons", trial, n_new)
        return events

    def _source_pool(self) -> tuple[str | None, np.ndarray]:
        """Upstream neurons that can serve as presynaptic sources for recruits.

        The pool is every upstream neuron that already participates in the
        plastic pathway: the presynaptic side of the measured plastic synapses
        plus the presynaptic side of synapses created by earlier recruits. It
        intentionally ignores synapse activity so recruitment survives after
        measured synapses have been pruned away. Returns the stage name and the
        sorted local indices.
        """
        stage = self._upstream_stage()
        if stage is None:
            return None, np.array([], dtype=np.int64)
        parts = [self.plastic.pre_indices]
        expansion = getattr(self.network, "expansion_plastic", None)
        if expansion is not None and expansion.pre_indices.size:
            parts.append(expansion.pre_indices)
        pool = np.unique(np.concatenate(parts))
        n_up = int(self.network.stages[stage].n)
        pool = pool[(pool >= 0) & (pool < n_up)]
        return stage, pool.astype(np.int64)

    # -- helpers --------------------------------------------------------
    def _cap_count(self, count: int, stats: StructuralStats, kind: str) -> int:
        if count <= 0:
            return 0
        attribute = {
            "prune": "cap_hits_prune",
            "silence": "cap_hits_silence",
            "recruit": "cap_hits_recruit",
        }[kind]
        if count >= (
            {"prune": self.max_prunes, "silence": self.max_silences,
             "recruit": self.max_recruits}[kind]
        ):
            setattr(stats, attribute, getattr(stats, attribute) + 1)
        return count

    def _cap(
        self,
        candidates: np.ndarray,
        per_interval: int,
        remaining_total: int,
        stats: StructuralStats,
        kind: str,
    ) -> np.ndarray:
        """Deterministically limit candidates to the interval and total caps."""
        if candidates.size == 0:
            return candidates
        # Sorting makes the choice independent of iteration order.
        ordered = np.sort(candidates)
        if ordered.size > per_interval:
            stats_field = {
                "prune": "cap_hits_prune",
                "silence": "cap_hits_silence",
                "recruit": "cap_hits_recruit",
            }[kind]
            setattr(stats, stats_field, getattr(stats, stats_field) + 1)
            ordered = ordered[:per_interval]
        if remaining_total < ordered.size:
            ordered = ordered[:max(0, remaining_total)]
        return ordered

    # -- output ---------------------------------------------------------
    def write_log(self, path: str | Path) -> Path:
        """Write the complete event log as JSON lines."""
        resolved = Path(path)
        resolved.parent.mkdir(parents=True, exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as handle:
            handle.write(
                json.dumps({
                    "enabled": self.enabled,
                    "seed": self.seed,
                    "role_pair": self.plastic.role_pair,
                    "n_events": len(self.events),
                    "stats": self.stats.as_dict(),
                }, sort_keys=True, default=str) + "\n"
            )
            for event in self.events:
                handle.write(event.to_json() + "\n")
        LOGGER.info("wrote %d structural events to %s", len(self.events), resolved)
        return resolved

    def summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "role_pair": self.plastic.role_pair,
            "n_events": len(self.events),
            "events_by_kind": {
                PRUNE: self.stats.prunes,
                SILENCE: self.stats.silences,
                RECRUIT: self.stats.recruits,
            },
            "stats": self.stats.as_dict(),
            "reserve": {
                "size": int(self.network.reserve.N),
                "recruited": self._recruited,
                "available": self.reserve_available,
                "readout_units": self._recruited,
                "prune_target": int(
                    np.floor(self.prune_per_recruit * self._recruited)
                ),
                "expansion_synapses": int(
                    self.network.expansion_plastic.weights.size
                    if self.network.expansion_plastic is not None else 0
                ),
            },
            "note": (
                "Exploratory mechanism with no published precedent. Recruited "
                "reserve neurons grow adjacent to the most successful readout "
                "unit: they inherit its category and at most (1 - divergence) "
                "of its inputs, draw the rest pseudo-randomly at average gain, "
                "and share no identical input set. Deletions are reserve-"
                "synapse prunes budgeted at prune_per_recruit per recruited "
                "neuron, topped up by retirement of the weakest synapses."
            ),
        }