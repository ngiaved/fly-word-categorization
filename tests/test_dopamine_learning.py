import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import config, connectome, learning, network as net


def _config(**overrides):
    base = ["simulation.codegen_target=numpy"]
    base += [f"{k}={v}" for k, v in overrides.items()]
    return config.Config.load("configs/smoke.yaml", overrides=base)


def _built(cfg):
    sc = connectome.extract_subcircuit(cfg)
    return net.build_network(sc, cfg, weight_scale=0.05)


def test_plasticity_locus_is_kenyon_to_output_only():
    # Requirement: Plasticity locus -- only mushroom_body->output is plastic
    cfg = _config()
    built = _built(cfg)
    assert built.plastic.role_pair == "mushroom_body->output"

    snapshot = learning.snapshot_fixed_weights(built)
    syn = built.synaptic_groups[built.plastic.role_pair]
    syn.elig[:] = 0.5
    learning.apply_dopamine(built, cfg, +1.0)

    unchanged = learning.fixed_synapses_unchanged(snapshot, built)
    assert unchanged, "fixed synapse set is empty"
    assert all(unchanged.values()), f"non-plastic synapses moved: {unchanged}"
    assert "mushroom_body->output" not in unchanged


def test_reward_increases_and_punishment_decreases_eligible_weights():
    # Requirement: Dopamine-gated weight update -- reward and punishment
    cfg = _config()
    built = _built(cfg)
    syn = built.synaptic_groups[built.plastic.role_pair]

    syn.elig[:] = 0.5
    before = built.plastic.weights.copy()
    result = learning.apply_dopamine(built, cfg, +1.0)
    reward_delta = built.plastic.weights - before
    assert result["changed"] > 0
    assert reward_delta.mean() > 0, "reward must increase eligible weights"

    syn.elig[:] = 0.5
    before = built.plastic.weights.copy()
    result = learning.apply_dopamine(built, cfg, -1.0)
    punish_delta = built.plastic.weights - before
    assert result["changed"] > 0
    assert punish_delta.mean() < 0, "punishment must decrease eligible weights"


def test_weights_are_clipped_to_configured_bounds():
    # Requirement: Weight bounds
    cfg = _config()
    built = _built(cfg)
    syn = built.synaptic_groups[built.plastic.role_pair]
    w_min = float(cfg.get("learning.w_min"))
    w_max = float(cfg.get("learning.w_max"))

    syn.elig[:] = 1.0
    built.plastic.weights = np.full_like(built.plastic.weights, w_max)
    learning.apply_dopamine(built, cfg, +1.0)
    assert float(built.plastic.weights.max()) <= w_max + 1e-12

    syn.elig[:] = 1.0
    built.plastic.weights = np.zeros_like(built.plastic.weights)
    learning.apply_dopamine(built, cfg, -1.0)
    assert float(built.plastic.weights.min()) >= w_min - 1e-12


def test_dopamine_disabled_leaves_weights_unchanged():
    # Requirement: Dopamine-gated weight update / No dopamine scenario
    cfg = _config(**{"learning.enabled": "false"})
    built = _built(cfg)
    syn = built.synaptic_groups[built.plastic.role_pair]
    syn.elig[:] = 0.5
    before = built.plastic.weights.copy()

    result = learning.apply_dopamine(built, cfg, +1.0)
    assert result["changed"] == 0
    assert result.get("skipped") == "learning disabled"
    assert np.array_equal(before, built.plastic.weights)


def test_eligibility_trace_decays_toward_zero():
    # Requirement: Eligibility trace -- decays toward zero with no activity
    import brian2 as b2

    cfg = _config(**{"network.noise.enabled": "false"})
    built = _built(cfg)
    syn = built.synaptic_groups[built.plastic.role_pair]
    tau_ms = float(cfg.get("learning.trace_tau_ms"))

    # Isolate the decay: every pre/post spike ADDS eligibility via the
    # trace_inc increments, so an active network would mask or reverse the
    # exponential decay. Quiesce every population and drop the injected
    # Poisson noise, which spikes independently of I_bg.
    for stage in built.stages.values():
        stage.group.I_bg = 0.0
        stage.group.I_syn = 0.0
        stage.group.I_teacher = 0.0
        stage.group.v[:] = 0.0
    built.reserve.I_bg = 0.0
    built.reserve.I_syn = 0.0
    built.reserve.I_teacher = 0.0
    built.reserve.v[:] = 0.0

    syn.elig[:] = 0.4
    initial = float(np.max(syn.elig[:]))
    built.brian.run(tau_ms * b2.ms)
    after_one_tau = float(np.max(syn.elig[:]))
    assert after_one_tau < initial, "trace must decay"
    built.brian.run(tau_ms * b2.ms)
    after_two_tau = float(np.max(syn.elig[:]))
    assert after_two_tau < after_one_tau, "trace must keep decaying"


def test_readout_seeded_tie_breaking_and_no_response():
    # Requirement: Readout with seeded tie-breaking and no-response handling
    first = learning.readout([3, 3, 1, 0], seed=42)
    again = learning.readout([3, 3, 1, 0], seed=42)
    assert first.predicted == again.predicted, "seeded readout must be repeatable"

    lowest = learning.readout([3, 3, 1, 0], tie_break="lowest_index")
    assert lowest.predicted == 0

    silent = learning.readout([0, 0, 0, 0])
    assert silent.is_no_response
    assert silent.predicted == learning.NO_RESPONSE


def test_dopamine_signal_selection():
    cfg = _config()
    assert learning.dopamine_for(1, 1, cfg) == (1.0, "reward")
    assert learning.dopamine_for(2, 1, cfg) == (-1.0, "punishment")
    assert learning.dopamine_for(learning.NO_RESPONSE, 1, cfg) == (
        0.0,
        "no_response",
    )