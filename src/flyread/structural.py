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

from .network import SimulationNetwork
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
        self.recruit_fraction = float(config.get("structural.recruit_weight_fraction"))
        self.recruit_noise = float(config.get("structural.recruit_noise"))
        self.max_prunes = int(config.get("structural.max_prunes_per_interval"))
        self.max_silences = int(config.get("structural.max_silences_per_interval"))
        self.max_recruits = int(config.get("structural.max_recruits_per_interval"))
        self.max_fraction_pruned = float(config.get("structural.max_total_fraction_pruned"))
        self.max_fraction_silenced = float(
            config.get("structural.max_total_fraction_silenced")
        )

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

        self._reserve_stage = self._make_reserve_stage()
        self._rng = make_rng(self.seed, stream="structural")

    # -- reserve pool ---------------------------------------------------
    def _make_reserve_stage(self):
        """A Synapses object the reserve neurons can be connected through later."""
        import brian2 as b2

        source_stage = self._upstream_stage()
        if source_stage is None:
            return None
        source = self.network.stages[source_stage].group
        syn = b2.Synapses(
            source, self.network.reserve,
            model="w : 1", on_pre="I_syn += w", method="euler",
            namespace={"v_rest": 0.0},
        )
        # The reserve pool is born unconnected. Brian2 refuses to run a
        # Synapses object with no connections, so it starts inactive and is
        # enabled by the first recruitment that connects it.
        syn.active = False
        self.network.brian.add(syn)
        return syn

    def _upstream_stage(self) -> str | None:
        plastic_pair = self.plastic.role_pair
        upstream = plastic_pair.split("->")[0]
        return upstream if upstream in self.network.stages else None

    @property
    def reserve_available(self) -> int:
        recruited_synapses = (
            len(self._reserve_stage) if self._reserve_stage else 0
        )
        return max(0, self.stats.reserve_available - self._recruited)

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

    # -- the step -------------------------------------------------------
    def maybe_step(self, trial: int) -> list[StructuralEvent]:
        """Run one structural check if this trial is on the schedule."""
        if not self.enabled or self.check_every <= 0:
            return []
        if trial % self.check_every != 0:
            return []
        return self.step(trial)

    def step(self, trial: int) -> list[StructuralEvent]:
        """One structural interval: prune, then silence, then maybe recruit."""
        self.stats.intervals += 1
        produced: list[StructuralEvent] = []
        produced.extend(self._prune(trial))
        produced.extend(self._silence(trial))
        if self.recruit_every > 0 and self.stats.intervals % self.recruit_every == 0:
            produced.extend(self._recruit(trial))
        self.events.extend(produced)
        return produced

    # -- pruning --------------------------------------------------------
    def _prune_candidates(self) -> np.ndarray:
        """Plastic synapses below threshold, respecting the total-change cap."""
        weights = self.plastic.weights
        active = self.plastic.active
        below = np.flatnonzero((weights < self.prune_threshold) & active)
        self.stats.candidate_totals["prune"] = int(below.size)

        # Consecutive-check state is tracked PER SYNAPSE. A single global
        # counter would let a synapse that only just dropped below threshold be
        # pruned on its first check, which is not what the spec asks for.
        if self._below_count is None or len(self._below_count) != weights.size:
            self._below_count = np.zeros(weights.size, dtype=np.int64)
        self._below_count[below] += 1
        self._below_count[~below] = 0

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

        template = self._pick_template()
        if template is None:
            return []
        template_stage, template_local = template
        pre_local = self._synapse_rows_for_pre(template_local)

        if pre_local.size == 0:
            LOGGER.info("trial %d: template neuron has no plastic inputs to clone", trial)
            return []

        n_syn = min(len(pre_local), 8)
        pre_local = pre_local[:n_syn]
        template_weights = self.plastic.weights[pre_local]

        events: list[StructuralEvent] = []
        for _offset in range(n_new):
            # _recruited advances once per neuron below, so it is the index
            # directly; adding an offset here as well would skip neurons and
            # eventually index past the end of the reserve group.
            reserve_local = self._recruited
            noise = 1.0 + self.recruit_noise * self._rng.standard_normal(pre_local.size)
            new_weights = template_weights * self.recruit_fraction * noise
            self._reserve_stage.connect(
                i=pre_local.tolist(),
                j=[reserve_local] * len(pre_local),
            )
            self._reserve_stage.active = True
            start = len(self._reserve_stage) - len(pre_local)
            self._reserve_stage.w[start:] = np.clip(new_weights, 0.0, None).tolist()
            self._recruited += 1
            events.append(StructuralEvent(
                trial=trial, kind=RECRUIT,
                detail={
                    "template_stage": template_stage,
                    "template_index": int(template_local),
                    "new_neuron_index": int(reserve_local),
                    "n_synapses": int(len(pre_local)),
                    "weight_fraction": self.recruit_fraction,
                    "noise": self.recruit_noise,
                    "mean_weight": float(np.mean(new_weights)),
                },
            ))
        self.stats.recruits += n_new
        self.stats.reserve_available = max(0, self.stats.reserve_available - n_new)
        LOGGER.info("trial %d: recruited %d reserve neurons", trial, n_new)
        return events

    def _synapse_rows_for_pre(self, local_index: int) -> np.ndarray:
        """Plastic synapse rows whose presynaptic neuron is ``local_index``.

        ``plastic.pre_indices`` are subcircuit indices; the upstream stage maps
        them to a dense local axis.
        """
        stage = self._upstream_stage()
        if stage is None:
            return np.array([], dtype=np.int64)
        group = self.network.stages[stage]
        hits = np.flatnonzero(group.indices == local_index)
        if hits.size == 0:
            return np.array([], dtype=np.int64)
        internal = int(hits[0])
        return np.flatnonzero(self.plastic.pre_indices == internal)

    def _pick_template(self) -> tuple[str, int] | None:
        stage = self._upstream_stage()
        if stage is None:
            return None
        group = self.network.stages[stage]
        driven: list[int] = []
        for local in range(group.n):
            internal = int(group.indices[local])
            if internal in self._silent_neurons:
                continue
            rows = self._synapse_rows_for_pre(local)
            if rows.size and self.plastic.active[rows].any():
                driven.append(local)
        if not driven:
            return None
        choice = driven[int(self._rng.integers(0, len(driven)))]
        return stage, choice

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
            },
            "note": (
                "Exploratory mechanism with no published precedent. Recruited "
                "reserve neurons receive weak cloned synapses but are not part "
                "of the plastic output synapse set, so they cannot change the "
                "readout until a later change extends the plasticity rules."
            ),
        }