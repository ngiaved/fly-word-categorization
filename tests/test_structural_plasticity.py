import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import config, connectome, network as net, structural


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


def test_recruit_connects_reserve_with_cloned_weak_weights():
    # Requirement: Neuron recruitment -- weak cloned synapses from a template
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
    for key in ("template_stage", "template_index", "new_neuron_index",
                "n_synapses", "weight_fraction"):
        assert key in detail, f"recruit event must record {key}"
    assert events[0].trial == 10

    assert len(sp._reserve_stage) > 0, "reserve stage must gain synapses"
    # the source index must address the template neuron in the source group
    source_group = built.stages[detail["template_stage"]].group
    for pre in np.asarray(sp._reserve_stage.i[:]).tolist():
        assert 0 <= pre < int(source_group.N), (
            f"presynaptic index {pre} outside source group of size "
            f"{int(source_group.N)}"
        )
    # weights must be weaker than the template they were cloned from
    assert float(np.max(np.asarray(sp._reserve_stage.w[:]))) < float(
        np.max(built.plastic.weights)
    )


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

    # dv/dt = (v - v_rest - I_syn + ...)/tau, so negative I_syn is excitatory.
    built.reserve.I_syn[:] = 5.0
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
        total += len([e for e in sp.step(trial=(step + 1) * 10) if e.kind == "prune"])

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