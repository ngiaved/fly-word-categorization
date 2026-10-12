import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import config, encoding, evaluate
from flyread import report as report_mod


def _smoke():
    cfg = config.Config.load("configs/smoke.yaml")
    ds = encoding.load_dataset(
        cfg.get("data.words_csv"), cfg.get("encoding.categories")
    )
    return cfg, ds


def _seed_result(seed, probes):
    return evaluate.SeedResult(
        seed=seed, results={}, weight_scale=4.0, seconds=1.0, probes=probes
    )


def _probe(test_acc, stage, train_acc=None, cv_acc=None):
    entry = {
        "stage": stage,
        "n_features": 8,
        "test_accuracy": test_acc,
        "train_accuracy": test_acc if train_acc is None else train_acc,
    }
    if cv_acc is not None:
        entry["cv_accuracy"] = cv_acc
    return entry


def test_trained_pretrained_condition_flips_learning_pretrain():
    cfg, _ = _smoke()
    assert "trained_pretrained" in evaluate.CONDITIONS
    run_config, settings = evaluate.condition_config(cfg, "trained_pretrained")
    assert settings.get("pretrain") is True
    assert bool(run_config.get("learning.pretrain")) is True
    # The plain trained condition must NOT enable pretraining.
    plain, _ = evaluate.condition_config(cfg, "trained")
    assert bool(plain.get("learning.pretrain")) is False


def test_positive_control_gate_passes_when_both_probes_clear():
    cfg, ds = _smoke()
    chance = float(cfg.get("evaluation.chance_level"))
    srs = [
        _seed_result(1000, {"input": _probe(0.99, "olfactory"),
                            "mushroom_body": _probe(0.42, "mushroom_body")}),
        _seed_result(1001, {"input": _probe(0.97, "olfactory"),
                            "mushroom_body": _probe(0.38, "mushroom_body")}),
    ]
    probes, gate = evaluate._aggregate_probes(srs, chance, cfg)
    assert probes["input"]["mean_test_accuracy"] == 0.98
    assert gate["passed"] is True
    assert gate["input_ok"] is True and gate["kc_ok"] is True


def test_positive_control_gate_fails_when_kc_probe_at_chance():
    cfg, ds = _smoke()
    chance = float(cfg.get("evaluation.chance_level"))
    srs = [
        _seed_result(1000, {"input": _probe(0.99, "olfactory"),
                            "mushroom_body": _probe(0.26, "mushroom_body")}),
    ]
    _, gate = evaluate._aggregate_probes(srs, chance, cfg)
    assert gate["passed"] is False
    assert gate["input_ok"] is True and gate["kc_ok"] is False
    assert "upstream bottleneck" in gate["detail"]


def test_positive_control_gate_fails_when_input_probe_broken():
    cfg, ds = _smoke()
    chance = float(cfg.get("evaluation.chance_level"))
    srs = [_seed_result(1000, {"input": _probe(0.5, "olfactory")})]
    _, gate = evaluate._aggregate_probes(srs, chance, cfg)
    assert gate["passed"] is False
    assert gate["input_ok"] is False
    assert "does not encode the label" in gate["detail"]


def test_positive_control_section_renders_gate_into_markdown():
    cfg, ds = _smoke()
    srs = [
        _seed_result(1000, {"input": _probe(0.99, "olfactory"),
                            "mushroom_body": _probe(0.42, "mushroom_body")}),
        _seed_result(1001, {"input": _probe(0.97, "olfactory"),
                            "mushroom_body": _probe(0.38, "mushroom_body")}),
    ]
    report = evaluate.aggregate(srs, ds, cfg, metadata={"base_seed": 1000})
    assert report.probes and report.positive_control["passed"] is True

    markdown = report_mod.build_markdown(
        report, srs, ds.categories,
        {"flywire_release": "783", "n_neurons": 176, "n_edges": 552},
    )
    assert "## Positive controls" in markdown
    assert "Positive-control gate: PASSED" in markdown


def test_gate_decides_on_held_out_and_reports_cv_diagnostic():
    # The gate must key on held-out accuracy (parallel to the main readout).
    # CV-over-presentations is reported but must not decide the gate: a decoder
    # that memorises can show high CV while generalising poorly.
    cfg, ds = _smoke()
    srs = [
        _seed_result(1000, {"input": _probe(1.0, "olfactory", cv_acc=1.0),
                            "mushroom_body": _probe(
                                0.15, "mushroom_body", cv_acc=0.60)}),
    ]
    probes, gate = evaluate._aggregate_probes(srs, 0.25, cfg)
    assert probes["mushroom_body"]["mean_cv_accuracy"] == 0.60
    assert gate["kc_probe_accuracy"] == 0.15
    assert gate["passed"] is False
    assert "held-out" in gate["decision_metric"]


def test_probes_disabled_yields_no_gate():
    cfg, ds = _smoke()
    cfg = cfg.with_overrides({"evaluation.probes.enabled": False})
    srs = [_seed_result(1000, {"input": _probe(0.99, "olfactory")})]
    _, gate = evaluate._aggregate_probes(srs, 0.25, cfg)
    assert gate["passed"] is None
    assert gate["enabled"] is False


def test_separable_config_removes_cross_category_overlap():
    # active_elsewhere=0 makes the classes linearly separable by construction:
    # no two words from different categories share any active receptor.
    cfg = config.Config.load("configs/odor-separable.yaml")
    cfg.validate()
    assert int(cfg.get("encoding.odor.active_elsewhere")) == 0
    ds = encoding.load_dataset(
        cfg.get("data.words_csv"), cfg.get("encoding.categories")
    )
    mapping = encoding.make_mapping(cfg, seed=1000, n_inputs=673, dataset=ds)
    active = {item.word: mapping.stimulus(item.word, cfg) > 0 for item in ds.items}
    for a in ds.items:
        for b in ds.items:
            if a.label == b.label or a.word >= b.word:
                continue
            assert (active[a.word] & active[b.word]).sum() == 0
    # Default config, by contrast, does overlap across categories.
    default = config.Config.load("configs/default.yaml")
    dmapping = encoding.make_mapping(default, seed=1000, n_inputs=673, dataset=ds)
    dactive = {item.word: dmapping.stimulus(item.word, default) > 0 for item in ds.items}
    cross = 0
    for a in ds.items:
        for b in ds.items:
            if a.label != b.label:
                cross += int((dactive[a.word] & dactive[b.word]).sum())
    assert cross > 0
