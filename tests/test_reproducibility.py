import sys
from pathlib import Path
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import config
from flyread.cli import _prepare
from argparse import Namespace


def test_run_manifest_present(tmp_path):
    # Scenario: Manifest present
    cfg = config.Config.load("configs/smoke.yaml", overrides=[
        "subcircuit.bridge.max_sources=8",
    ])
    args = Namespace(seed=123, dir="data/flywire")
    manifest, dataset, subc, seed = _prepare(cfg, args)
    manifest.write(tmp_path)
    mf = tmp_path / "manifest.json"
    assert mf.exists()
    data = json.loads(mf.read_text())
    for key in ["config", "seed", "data", "subcircuit", "environment",
                "events", "warnings", "run_id", "seeding"]:
        assert key in data
