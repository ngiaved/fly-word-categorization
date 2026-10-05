"""End-to-end checks that visual drive actually reaches the readout.

Every bug covered here was silent: the code ran, the suite passed, and the
manifest looked well formed, yet the network never received the stimulus or
carried it to the output. They are collected in one file because the failure
they share is a broken signal path rather than a single unit of logic.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import config, connectome, encoding, learning
from flyread import network as net


def _config(**overrides):
    base = ["simulation.codegen_target=numpy"]
    base += [f"{k}={v}" for k, v in overrides.items()]
    return config.Config.load("configs/smoke.yaml", overrides=base)


# Extracting the subcircuit reads the 852 MB FlyWire table, so the fixture is
# module scoped: one extraction shared by every test in this file.
@pytest.fixture(scope="module")
def subcircuit():
    return connectome.extract_subcircuit(_config())


@pytest.fixture(scope="module")
def driven(subcircuit):
    cfg = _config(
        **{
            "calibration.enabled": "false",
            "network.background_dc_na": "0.0",
            "network.noise.weight": "0.0",
        }
    )
    built = net.build_network(subcircuit, cfg, weight_scale=500.0)
    mapping = encoding.make_mapping(cfg, seed=1000)
    runner = learning.TrialRunner(built, cfg, mapping, seed=1000)
    return cfg, built, runner


def test_stimulus_lands_on_the_brian2_group_not_the_stage_wrapper(driven):
    """Regression: the drive was written to the StageGroup dataclass.

    ``network.stages`` maps a role name to a ``StageGroup`` wrapper, which has
    no attribute forwarding to the Brian2 NeuronGroup. Assigning ``I_syn`` on
    the wrapper therefore created a plain Python attribute and the simulated
    photoreceptors stayed at zero current for the whole experiment.
    """
    cfg, built, runner = driven
    currents = runner._apply_stimulus("HAWK")
    stage = built.stages["photoreceptor"]
    assert float(np.max(stage.group.I_syn[:])) > 0.0, (
        "stimulus current must reach the simulated photoreceptor group"
    )
    assert np.isclose(
        float(np.max(stage.group.I_syn[:])), float(np.max(currents))
    ), "delivered current must match the encoding output"
    assert not hasattr(stage, "I_syn"), (
        "no stray I_syn attribute may be created on the StageGroup wrapper"
    )


def test_positive_current_depolarizes(driven):
    """Regression: the LIF subtracted a signed synaptic current.

    ``I_syn`` carries the transmitter sign (+1 excitatory), so it must be
    added. Subtracting it made every excitatory connection hyperpolarizing and
    every inhibitory connection depolarizing, inverting the whole network.
    """
    import brian2 as b2

    cfg, built, _ = driven
    group = built.stages["photoreceptor"].group
    group.I_syn = 0.0
    group.v[:] = 0.0
    group.I_syn = 0.5
    built.brian.run(20 * b2.ms)
    assert float(np.max(group.v[:])) > 0.1, (
        "a positive synaptic current must depolarize the postsynaptic neuron"
    )


def test_background_drive_set_when_noise_disabled(subcircuit):
    """Regression: I_bg was assigned inside the ``noise.enabled`` branch.

    Tying the background drive to the noise toggle made every noise-free run
    perfectly silent, which is what made the stimulus look unable to reach
    threshold during diagnosis.
    """
    cfg = _config(**{"network.noise.enabled": "false", "calibration.enabled": "false"})
    built = net.build_network(subcircuit, cfg, weight_scale=500.0)
    for stage in built.stages.values():
        assert float(np.max(stage.group.I_bg[:])) == pytest.approx(
            float(cfg.get("network.background_dc_na"))
        ), f"{stage.name} must keep its background drive with noise disabled"


def test_visual_chain_is_connected_to_the_output(subcircuit):
    """Regression: the lobula had zero in-edges.

    Ranking each stage by input from the previous one selected medulla neurons
    that projected to none of the top-ranked lobula candidates. The lobula then
    never fired, so the artificial lobula -> Kenyon-cell bridge transmitted no
    visual signal at all. Seeding the chain backward fixed the link.
    """
    summary = subcircuit.summary()
    assert summary["reachability"]["signal_reaches_outputs"], (
        "photoreceptor must reach the output neurons: "
        f"{summary['reachability']}"
    )
    pairs = {row[4] for row in subcircuit.edges}
    for link in ("photoreceptor->lamina", "lamina->medulla", "medulla->lobula"):
        assert link in pairs, f"visual chain is broken: missing {link}"


def test_lobula_receives_real_synapses_from_the_subcircuit(subcircuit):
    """The artificial bridge is only meaningful if the lobula is driven."""
    import collections

    edges = np.asarray(subcircuit.edges, dtype=object)
    in_edges = [row for row in subcircuit.edges if str(row[4]).split("->")[1] == "lobula"]
    assert in_edges, "lobula must have incoming synapses"
    sources = collections.Counter(str(row[4]).split("->")[0] for row in in_edges)
    assert sources.get("medulla", 0) > 0, (
        f"lobula is not driven by the medulla: {dict(sources)}"
    )


def test_output_response_depends_on_the_word(driven):
    """Regression: every word produced an identical readout.

    Output spike counts were exactly equal across all words, so between-word
    variance was 0.0 and the evaluation could only ever measure noise. This is
    the behavioural check that the whole signal path is alive.
    """
    cfg, built, runner = driven
    words = ["HAWK", "LIME", "TOOL", "WAVE"]
    means = []
    for word in words:
        counts = np.zeros(int(built.output_indices.size), dtype=np.float64)
        for repeat in range(6):
            record = runner.run_trial(word, 0, 3000 + repeat, learn=False)
            counts += np.asarray(record.spike_counts, dtype=np.float64) / 6
        means.append(counts)
    means = np.asarray(means)
    assert np.all(means.sum(axis=1) > 0), (
        "output neurons must respond to the stimulus at all"
    )
    spread = means.std(axis=0).max()
    assert spread > 0.0, (
        "output response must vary across words; an identical response for "
        "every word means the stimulus never reached the readout"
    )