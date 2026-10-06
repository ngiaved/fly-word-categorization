import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import config, connectome, network as net


def _config(**overrides):
    base = ["simulation.codegen_target=numpy"]
    base += [f"{k}={v}" for k, v in overrides.items()]
    return config.Config.load("configs/smoke.yaml", overrides=base)


def _subcircuit(cfg):
    return connectome.extract_subcircuit(cfg)


def test_build_has_reserve_pool_and_declared_lif():
    # Requirement: network built from the subcircuit with a reserve pool
    cfg = _config()
    sc = _subcircuit(cfg)
    built = net.build_network(sc, cfg, weight_scale=0.05)

    assert built.reserve.N > 0, "reserve pool must be non-empty"
    assert built.reserve_size if hasattr(built, "reserve_size") else int(built.reserve.N)
    # reserve neurons start silent and are excluded from the readout
    assert built.output_indices.size == int(cfg.get("network.n_outputs"))
    summary = built.summary()
    assert summary["reserve_size"] == int(built.reserve.N)
    assert summary["lif"]["tau_ms"] > 0


def test_calibration_reaches_stable_regime_and_is_recorded():
    # Requirement: Network stability calibration -- stable regime, recorded
    cfg = _config()
    sc = _subcircuit(cfg)
    _built, result = net.calibrate(sc, cfg)

    assert result.accepted
    band = (
        float(cfg.get("calibration.min_mean_rate_hz")),
        float(cfg.get("calibration.max_mean_rate_hz")),
    )
    assert band[0] <= result.mean_rate_hz <= band[1]
    assert result.continuous_fraction <= float(
        cfg.get("calibration.max_continuous_fraction")
    )
    assert result.rate_drift_hz <= float(cfg.get("calibration.max_rate_drift_hz"))

    recorded = result.as_dict()
    for key in (
        "weight_scale", "mean_rate_hz", "continuous_fraction",
        "rate_drift_hz", "accepted", "attempts",
    ):
        assert key in recorded, f"{key} must be recorded in the manifest payload"


def test_saturating_scale_is_rejected():
    # Requirement: Unstable regime -- saturation must be rejected, not accepted
    cfg = _config()
    sc = _subcircuit(cfg)
    built = net.build_network(sc, cfg, weight_scale=200.0)
    stats = net.measure_spontaneous_activity(built, cfg)

    band = (
        float(cfg.get("calibration.min_mean_rate_hz")),
        float(cfg.get("calibration.max_mean_rate_hz"))
    )
    in_band = band[0] <= stats["mean_rate_hz"] <= band[1]
    not_saturated = stats["continuous_fraction"] <= float(
        cfg.get("calibration.max_continuous_fraction")
    )
    stable = stats["rate_drift_hz"] <= float(cfg.get("calibration.max_rate_drift_hz"))
    assert not (in_band and not_saturated and stable), (
        "a scale of 200 must fail at least one stability criterion, "
        f"got mean={stats['mean_rate_hz']:.2f} "
        f"cont={stats['continuous_fraction']:.3f} "
        f"drift={stats['rate_drift_hz']:.2f}"
    )


def test_drift_is_temporal_not_per_neuron_split():
    # The drift metric must compare the SAME neurons across two consecutive
    # time halves of the measurement window. The old implementation split the
    # per-neuron count vector by index, which compared stage populations
    # (photoreceptor vs. output) -- a static stage difference, not drift.
    cfg = _config()
    sc = _subcircuit(cfg)
    built = net.build_network(sc, cfg, weight_scale=10.0)
    stats = net.measure_spontaneous_activity(built, cfg)

    first = stats["first_half_rate_hz"]
    second = stats["second_half_rate_hz"]
    assert np.isfinite(first) and np.isfinite(second)
    assert stats["rate_drift_hz"] == abs(second - first)
    # Both halves count every reported neuron over their own duration, so
    # each half's mean rate is a per-neuron rate in Hz.
    assert first >= 0.0 and second >= 0.0