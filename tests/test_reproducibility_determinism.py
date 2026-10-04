import sys
from pathlib import Path
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import config
from flyread.cli import _prepare
from argparse import Namespace


def test_same_seed_same_result(tmp_path):
    # Scenario: Same seed same result
    cfg = config.Config.load("configs/smoke.yaml", overrides=[
        "subcircuit.bridge.max_sources=8",
        "evaluation.n_seeds=1",
    ])
    args = Namespace(seed=42, dir="data/flywire")
    m1, _, _, _ = _prepare(cfg, args)
    m2, _, _, _ = _prepare(cfg, args)
    assert m1.seed == m2.seed == 42
