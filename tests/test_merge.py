import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import cli, config, encoding, evaluate
from flyread import report as report_mod


def _dataset():
    cfg = config.Config.load("configs/smoke.yaml")
    ds = encoding.load_dataset(
        cfg.get("data.words_csv"), cfg.get("encoding.categories")
    )
    return cfg, ds


def _seed_result(seed, accuracies):
    nc = 4
    results = {}
    for condition, accuracy in accuracies.items():
        counts = np.zeros((nc, nc), dtype=np.int64)
        counts[0][0] = round(accuracy * 10)
        counts[1][1] = round(accuracy * 10)
        counts[2][2] = round(accuracy * 10)
        counts[3][3] = round(accuracy * 10)
        confusion = evaluate.ConfusionMatrix(
            labels=list(range(nc)), counts=counts
        )
        results[condition] = evaluate.RunResult(
            condition=condition,
            seed=seed,
            trained=True,
            train_accuracy=accuracy,
            test_accuracy=accuracy,
            test_no_response_rate=0.0,
            confusion=confusion,
            learning_curve=[
                {"trial": i, "accuracy": accuracy} for i in range(1, 4)
            ],
            plasticity={},
            structural=None,
        )
    return evaluate.SeedResult(
        seed=seed, results=results, weight_scale=4.0, seconds=42.0
    )


def _write_shard(root, seed, accuracies):
    cfg, ds = _dataset()
    run_dir = root / f"shard-{seed}"
    run_dir.mkdir(parents=True, exist_ok=True)
    seed_result = _seed_result(seed, accuracies)
    report = evaluate.aggregate(
        [seed_result], ds, cfg,
        metadata={
            "weight_scale": 4.0,
            "total_seconds": 42.0,
            "base_seed": seed,
        },
    )
    report_mod.write_json(report, [seed_result], run_dir / "results.json")
    (run_dir / "manifest.json").write_text(
        json.dumps(
            {
                "subcircuit": {"n_neurons": 100, "n_edges": 200},
                "environment": {"codegen_target": "cython"},
            }
        )
    )
    return cfg


def test_merge_reconstructs_multi_seed_report(tmp_path):
    conditions = {
        "untrained": 0.25,
        "trained": 0.25,
        "shuffled_labels": 0.25,
        "dopamine_off": 0.25,
        "structural_on": 0.25,
        "structural_off": 0.25,
    }
    base = _write_shard(tmp_path, 1000, conditions)
    conditions_1001 = dict(conditions, trained=0.35)
    cfg = _write_shard(tmp_path, 1001, conditions_1001)
    cfg.apply_overrides([f"run.output_dir={tmp_path}"])

    rc = cli.merge_runs(cfg, ["shard-1000", "shard-1001"], "merged")
    assert rc == 0

    merged = tmp_path / "merged"
    assert (merged / "report.md").exists()
    assert (merged / "manifest.json").exists()

    payload = json.loads((merged / "results.json").read_text())
    assert payload["n_seeds"] == 2
    assert set(payload["per_seed_test_accuracy"]) == set(conditions)
    assert payload["per_seed_test_accuracy"]["trained"] == [0.25, 0.35]
    assert payload["conditions"]["trained"]["mean_accuracy"] == 0.3
    # Confusion matrices summed across both seeds.
    assert payload["metadata"]["merged_run_ids"] == ["shard-1000", "shard-1001"]
    conditions_meta = (json.loads((merged / "manifest.json").read_text()))["metrics"]
    assert conditions_meta["merged"] is True


def test_merge_refuses_missing_shard(tmp_path):
    cfg, _ = _dataset()
    cfg.apply_overrides([f"run.output_dir={tmp_path}"])
    rc = cli.merge_runs(cfg, ["missing-shard"], "merged")
    assert rc == 2