import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import connectome, config


def test_subcircuit_extraction_produces_manifest_data():
    # Scenario: Manifest produced
    cfg = config.Config.load("configs/smoke.yaml", overrides=[
        "subcircuit.bridge.max_sources=8",
    ])
    sc = connectome.extract_subcircuit(cfg)
    assert sc.n_neurons > 0
    assert len(sc.roles) > 0


def test_memory_budget_fails_fast():
    # Scenario: Budget check -- the run must fail fast if the budget is exceeded
    import pytest
    from flyread import config
    from flyread.connectome import extract_subcircuit

    cfg = config.Config.load("configs/smoke.yaml", overrides=[
        "subcircuit.bridge.max_sources=8",
        "resources.memory_budget_mb=1",
    ])
    with pytest.raises(MemoryError):
        extract_subcircuit(cfg)
