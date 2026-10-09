import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import config, connectome, learning, network as net, structural


def _config(**overrides):
    base = ["simulation.codegen_target=numpy"]
    base += [f"{k}={v}" for k, v in overrides.items()]
    return config.Config.load("configs/smoke.yaml", overrides=base)


def _built(cfg, weight_scale=0.05):
    sc = connectome.extract_subcircuit(cfg)
    return net.build_network(sc, cfg, weight_scale=weight_scale)


def test_reserve_pool_is_born_inactive_and_silent():
    # Requirement: Fixed-size reserve pool -- reserve inactive
    import brian2 as b2

    cfg = _config()
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=1)

    assert int(built.reserve.N) > 0
    assert len(sp._reserve_stage) == 0, "reserve must start unconnected"
    assert sp.reserve_available == int(built.reserve.N)

    monitor = b2.SpikeMonitor(built.reserve, record=False)
    built.brian.add(monitor)
    built.brian.run(200 * b2.ms)
    assert int(np.sum(monitor.count)) == 0, "reserve neurons must never spike"


def test_prune_requires_consecutive_checks_below_threshold():
    # Requirement: Synaptic pruning -- consecutive-check rule
    cfg = _config(**{"structural.prune_consecutive_checks": "2"})
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=1)

    built.plastic.weights[:] = 0.0
    first = sp.step(trial=10)
    assert len([e for e in first if e.kind == "prune"]) == 0, (
        "must not prune before the required consecutive checks"
    )
    second = sp.step(trial=20)
    pruned = [e for e in second if e.kind == "prune"]
    assert pruned, "must prune once the consecutive-check count is reached"

    detail = pruned[0].detail
    for key in ("synapse_id", "pre_index", "post_index",
                "weight_before", "weight_after", "consecutive_checks"):
        assert key in detail, f"prune event must record {key}"
    assert pruned[0].trial == 20, "event must carry the trial number"
    assert not built.plastic.active[detail["synapse_id"]], (
        "pruned synapse must be marked inactive"
    )


def test_counter_resets_when_synapse_recovers():
    # A synapse that drops back above threshold must not be pruned on the
    # strength of an earlier run of below-threshold checks.
    cfg = _config(**{"structural.prune_consecutive_checks": "2"})
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=1)

    built.plastic.weights[:] = 0.0
    sp.step(trial=10)                      # 1 below-threshold check
    built.plastic.weights[:] = 0.5          # recover above threshold
    sp.step(trial=20)                      # resets the counter
    built.plastic.weights[:] = 0.0
    events = sp.step(trial=30)              # only 1 check since recovery
    assert len([e for e in events if e.kind == "prune"]) == 0, (
        "the consecutive-check counter must reset when a synapse recovers"
    )


def test_silence_disables_synapses_and_logs():
    # Requirement: Neuron silencing
    cfg = _config(**{
        "structural.silence_window_trials": "1",
        "structural.silence_rate_threshold_hz": "1000.0",
    })
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=1)
    sp.set_trial_duration(0.06)
    sp.observe({"mushroom_body": 0.0, "output": 0.0})

    events = [e for e in sp.step(trial=10) if e.kind == "silence"]
    assert events, "silent neurons must be silenced"
    detail = events[0].detail
    for key in ("stage", "neuron_index", "synapses_disabled",
                "rate_threshold_hz", "window_trials"):
        assert key in detail, f"silence event must record {key}"
    assert events[0].trial == 10


def test_recruit_connects_reserve_with_population_matched_weights():
    # Requirement: Neuron recruitment -- pseudorandom sources, weights that
    # track the population (not clones of a single template)
    cfg = _config(**{
        "structural.recruit_every_n_checks": "1",
        "structural.max_recruits_per_interval": "1",
    })
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=1)
    sp.set_trial_duration(0.06)

    events = [e for e in sp.step(trial=10) if e.kind == "recruit"]
    assert events, "recruitment must occur when the reserve has capacity"
    detail = events[0].detail
    for key in ("source_stage", "new_neuron_index", "n_synapses",
                "source_indices", "population_mean_weight",
                "population_std_weight"):
        assert key in detail, f"recruit event must record {key}"
    assert events[0].trial == 10

    assert len(sp._reserve_stage) > 0, "reserve stage must gain synapses"
    # every presynaptic source must address the upstream source group
    source_group = built.stages[detail["source_stage"]].group
    source_indices = detail["source_indices"]
    assert len(source_indices) == detail["n_synapses"]
    assert len(set(source_indices)) == len(source_indices), (
        "sources must be distinct"
    )
    for pre in source_indices:
        assert 0 <= pre < int(source_group.N), (
            f"presynaptic index {pre} outside source group of size "
            f"{int(source_group.N)}"
        )
    # weights are bootstrapped from the population, so their mean must be in
    # the same ballpark as the population mean (not scaled down by a fraction)
    population_mean = detail["population_mean_weight"]
    assert float(np.mean(np.asarray(sp._reserve_stage.w[:]))) == detail[
        "mean_weight"
    ]
    assert abs(detail["mean_weight"] - population_mean) <= 3.0 * abs(
        population_mean
    ) + 1e-6


def test_recruited_reserve_neurons_can_spike():
    # Recruited neurons must be functional, not merely wired.
    import brian2 as b2

    cfg = _config(**{
        "structural.recruit_every_n_checks": "1",
        "structural.max_recruits_per_interval": "1",
    })
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=1)
    sp.set_trial_duration(0.06)
    sp.step(trial=10)

    # `I_syn` is a SIGNED current that is ADDED to the membrane equation, so a
    # POSITIVE value is excitatory. With a restoring leak the steady state is
    # `v_rest + I_syn * tau`, so reaching `v_threshold` (1.0) with
    # tau=20 ms needs I_syn > 50; a small current now correctly fails to spike.
    built.reserve.I_syn[:] = 200.0
    monitor = b2.SpikeMonitor(built.reserve, record=False)
    built.brian.add(monitor)
    built.brian.run(300 * b2.ms)
    assert int(np.sum(monitor.count)) > 0, (
        "a recruited reserve neuron must be able to spike once driven"
    )


def test_reserve_exhaustion_warns_once_and_stops():
    # Requirement: Reserve exhausted scenario
    cfg = _config(**{
        "structural.recruit_every_n_checks": "1",
        "structural.max_recruits_per_interval": "4",
        # Keep every plastic synapse above the prune threshold so pruning
        # cannot starve recruitment of template neurons.
        "structural.prune_weight_threshold": "0.0",
    })
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=1)
    sp.set_trial_duration(0.06)

    reserve_size = int(built.reserve.N)
    for step in range(reserve_size + 20):
        sp.step(trial=(step + 1) * 10)
        if sp.reserve_available == 0:
            break
    assert sp.reserve_available == 0, (
        f"reserve not exhausted after {step + 1} steps: "
        f"{sp.stats.recruits} recruited of {reserve_size}"
    )
    assert sp.stats.recruits == reserve_size

    extra = [e for e in sp.step(trial=9999) if e.kind == "recruit"]
    assert not extra, "no recruitment may occur once the reserve is exhausted"
    assert sp.stats.reserve_warning_logged, "exhaustion must be warned about"


def test_per_interval_cap_is_enforced_deterministically():
    # Requirement: Rate limits and safety -- cap enforced, chosen deterministically
    cfg = _config(**{
        "structural.max_prunes_per_interval": "5",
        "structural.max_total_fraction_pruned": "1.0",
    })

    def chosen_synapses():
        built = _built(cfg)
        sp = structural.StructuralPlasticity(built, cfg, seed=3)
        built.plastic.weights[:] = 0.0
        events = [e for e in sp.step(trial=10) if e.kind == "prune"]
        return sorted(e.detail["synapse_id"] for e in events)

    first, second = chosen_synapses(), chosen_synapses()
    assert len(first) == 5, f"per-interval cap of 5 not respected: {len(first)}"
    assert first == second, "the capped choice must be deterministic for a seed"


def test_total_fraction_cap_is_enforced():
    # Requirement: Rate limits and safety -- total fraction of the pathway
    cfg = _config(**{
        "structural.max_prunes_per_interval": "5",
        "structural.max_total_fraction_pruned": "0.2",
    })
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=3)

    total = 0
    for step in range(4):
        built.plastic.weights[:] = 0.0
        built.plastic.active[:] = True
        # The total-fraction cap governs the measured plastic pathway; reserve
        # retirements are budgeted separately by prune_per_recruit additions,
        # so exclude them from this assertion.
        total += len([
            e for e in sp.step(trial=(step + 1) * 10)
            if e.kind == "prune" and not e.detail.get("retired", False)
        ])

    allowed = int(np.floor(0.2 * sp.total_plastic))
    assert total <= allowed, f"pruned {total}, allowed {allowed}"


def test_ablation_switch_disables_all_structural_change():
    # Requirement: Ablation switch
    cfg = _config(**{"structural.enabled": "false"})
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=1)
    sp.set_trial_duration(0.06)

    built.plastic.weights[:] = 0.0
    sp.observe({"mushroom_body": 0.0, "output": 0.0})
    events = sp.maybe_step(trial=10)

    assert not events, "a disabled structural step must produce no events"
    assert sp.stats.intervals == 0
    assert sp.summary()["n_events"] == 0


def test_recruited_readout_units_have_plastic_input():
    # Recruited reserve neurons must be full readout units: their input is a
    # dopamine-shaped plastic set, not inert wiring.
    cfg = _config(**{
        "structural.recruit_every_n_checks": "1",
        "structural.max_recruits_per_interval": "2",
    })
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=1)
    sp.set_trial_duration(0.06)

    events = [e for e in sp.step(trial=10) if e.kind == "recruit"]
    assert events, "recruitment must occur"
    assert all("readout_category" in e.detail for e in events), (
        "recruit events must record the readout category each neuron joins"
    )

    expansion = built.expansion_plastic
    assert expansion is not None and expansion.weights.size > 0
    assert built.expansion_syn is not None
    assert expansion.role_pair.endswith("->reserve")

    # Dopamine must reshape the recruited input weights.
    built.expansion_syn.elig[:] = 1.0
    before = expansion.weights.copy()
    learning.apply_dopamine(built, cfg, 1.0, None)
    assert not np.allclose(expansion.weights, before), (
        "dopamine must shape the recruited neurons' plastic input"
    )


def test_reserve_pruning_tracks_fraction_of_recruits():
    # Deletions (reserve-synapse prunes) are budgeted at prune_per_recruit per
    # recruited neuron, so they track a fraction of additions.
    cfg = _config(**{
        "structural.recruit_every_n_checks": "1",
        "structural.max_recruits_per_interval": "6",
        "structural.prune_per_recruit": "0.5",
        "structural.prune_weight_ratio": "0.5",
        "structural.prune_consecutive_checks": "1",
    })
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=2)
    sp.set_trial_duration(0.06)

    sp.step(trial=1)
    recruited = sp.stats.recruits
    assert recruited > 0, "must recruit before pruning can track additions"
    target = int(np.floor(0.5 * recruited))

    expansion = built.expansion_plastic
    for trial in range(2, 12):
        expansion.weights[:] = 0.0  # every reserve synapse is now "weak"
        sp.step(trial=trial)

    assert sp.stats.expansion_pruned_total == target, (
        f"pruned {sp.stats.expansion_pruned_total} reserve synapses, "
        f"expected {target} (half of {recruited} recruits)"
    )


def test_on_and_off_differ_only_in_structural_events():
    # Requirement: Ablation switch -- controlled comparison
    def run(enabled):
        cfg = _config(**{
            "structural.enabled": enabled,
            "structural.check_every_n_trials": "5",
            "structural.recruit_every_n_checks": "1",
        })
        built = _built(cfg)
        sp = structural.StructuralPlasticity(built, cfg, seed=11)
        sp.set_trial_duration(0.06)
        produced = []
        for trial in range(1, 31):
            built.plastic.weights[0] = 0.0
            sp.observe({"mushroom_body": 0.0, "output": 0.0})
            produced += sp.maybe_step(trial)
        return produced

    on_events = run("true")
    off_events = run("false")
    assert on_events, "the enabled run must produce structural events"
    assert not off_events, "the disabled run must produce none"
    kinds = {e.kind for e in on_events}
    assert kinds & {"prune", "silence", "recruit"}, (
        f"expected structural events, got {sorted(kinds)}"
    )


def test_recruit_input_sets_are_unique():
    # Requirement: no two neurons look the same -- distinct input sets only.
    cfg = _config(**{
        "structural.recruit_every_n_checks": "1",
        "structural.max_recruits_per_interval": "1",
        "structural.prune_per_recruit": "0",
    })
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=1)

    for trial in range(1, 7):  # reserve pool holds 6 neurons
        recruits = [e for e in sp.step(trial=trial) if e.kind == "recruit"]
        assert len(recruits) <= 1, "per-interval cap must hold"
    recruited = sp.stats.recruits
    assert recruited > 1, "must recruit more than one neuron to test uniqueness"

    signatures = {
        frozenset(e.detail["source_indices"])
        for e in sp.events if e.kind == "recruit"
    }
    assert len(signatures) == recruited, (
        f"{recruited} recruits but only {len(signatures)} unique input sets"
    )


def test_recruit_starts_at_average_gain():
    # New neurons bootstrap at the population-average gain (not the anchor's),
    # then tune from there.
    cfg = _config(**{
        "structural.recruit_every_n_checks": "1",
        "structural.max_recruits_per_interval": "1",
        "structural.prune_per_recruit": "0",
        "structural.recruit_noise": "0",
    })
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=1)
    recruit = [e for e in sp.step(trial=10) if e.kind == "recruit"]
    assert recruit
    mean = float(np.mean(np.asarray(built.expansion_plastic.weights)))
    population_mean = recruit[0].detail["population_mean_weight"]
    assert np.isclose(mean, population_mean), (
        f"recruit gain {mean} != population average {population_mean}"
    )


def test_recruit_divergence_from_anchor_at_least_0_8():
    # Growth must be "adjacent to the most successful ones": a recruit keeps at
    # most (1 - divergence) of the anchor's inputs (default divergence 0.85).
    cfg = _config(**{
        "structural.recruit_every_n_checks": "1",
        "structural.max_recruits_per_interval": "1",
        "structural.prune_per_recruit": "0",
    })
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=1)
    # Make category 0 the winner so the anchor's inputs are well defined.
    for _ in range(50):
        sp.observe_trial(0, "reward")
    winner_sources = sp._winner_sources(0)
    assert winner_sources.size > 0, "test requires the anchor to have inputs"

    for trial in range(1, 5):
        for e in sp.step(trial=trial):
            if e.kind != "recruit":
                continue
            assert e.detail["readout_category"] == 0, (
                "recruit must join the most successful unit's category"
            )
            assert e.detail["divergence"] >= 0.8, (
                f"divergence {e.detail['divergence']:.3f} < 0.8 from anchor"
            )
            unit = e.detail["new_neuron_index"]
            # The readout credits this neuron to the anchor's category via the
            # shared ledger, not round-robin.
            assert int(built.reserve_categories[unit]) == 0, (
                "reserve_categories ledger must record the anchor category"
            )


def test_retirement_deletions_track_additions():
    # With the weight-threshold gate silent, forced retirement must still
    # deliver prune_per_recruit deletions per recruited neuron, after giving
    # each recruit one full interval to mature.
    cfg = _config(**{
        "structural.recruit_every_n_checks": "1",
        "structural.max_recruits_per_interval": "1",
        "structural.prune_per_recruit": "0.5",
        "structural.prune_weight_ratio": "0.0001",
        "structural.prune_consecutive_checks": "2",
        "structural.recruitment.force_retire": "true",
        "structural.recruitment.retirement_min_age": "1",
    })
    built = _built(cfg)
    sp = structural.StructuralPlasticity(built, cfg, seed=2)

    for trial in range(1, 9):
        sp.step(trial=trial)

    recruited = sp.stats.recruits
    assert recruited >= 6, f"expected the reserve to fill, got {recruited}"
    target = int(np.floor(0.5 * recruited))
    assert sp.stats.expansion_pruned_total == target, (
        f"pruned {sp.stats.expansion_pruned_total} reserve synapses, "
        f"expected {target} (prune_per_recruit of {recruited} recruits)"
    )
    assert sp.stats.retired_total >= target - 1, (
        "retirement must deliver the deletions, not the weight threshold"
    )