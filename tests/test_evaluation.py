import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import config, encoding, evaluate
from flyread import report as report_mod


def _dataset():
    cfg = config.Config.load("configs/smoke.yaml")
    return cfg, encoding.load_dataset(
        cfg.get("data.words_csv"), cfg.get("encoding.categories")
    )


def _result(condition, seed, accuracy, confusion, curve_len=3):
    return evaluate.RunResult(
        condition=condition,
        seed=seed,
        trained=True,
        train_accuracy=accuracy,
        test_accuracy=accuracy,
        test_no_response_rate=0.0,
        confusion=confusion,
        learning_curve=[
            {"trial": i, "accuracy": accuracy} for i in range(1, curve_len + 1)
        ],
        plasticity={},
        structural=None,
    )


def _seed_results(accuracy_for):
    confusion = evaluate.ConfusionMatrix(
        labels=[0, 1, 2, 3], counts=np.zeros((4, 4), dtype=np.int64)
    )
    results = []
    for offset in range(10):
        seed = 1000 + offset
        per_condition = {
            condition: _result(condition, seed, accuracy_for(condition, offset), confusion)
            for condition in evaluate.CONDITIONS
        }
        results.append(
            evaluate.SeedResult(
                seed=seed,
                results=per_condition,
                weight_scale=0.05,
                seconds=1.0,
            )
        )
    return results


def test_growth_matched_control_conditions_apply_their_overrides():
    # Reviewer-requested controls must resolve to exactly the intended config.
    cfg, _ = _dataset()

    rc, _ = evaluate.condition_config(cfg, "structural_random_category")
    assert rc.get("structural.enabled") is True
    assert rc.get("structural.recruitment.category_guidance") is False

    rc, _ = evaluate.condition_config(cfg, "structural_unguided")
    assert rc.get("structural.enabled") is True
    assert rc.get("structural.recruitment.anchor") == "random"
    assert rc.get("structural.recruitment.input_source") == "random"
    assert rc.get("structural.recruitment.category_guidance") is False

    rc, _ = evaluate.condition_config(cfg, "trained_extra_features")
    assert rc.get("structural.enabled") is False
    assert int(rc.get("evaluation.extra_random_features")) == 300
    assert rc.get("evaluation.readout") == "linear"


def test_random_feature_builder_matches_recruit_dimension():
    # The capacity control must add exactly the configured number of extra
    # features and be deterministic per (condition, seed).
    class _Runner:
        last_window = {"mushroom_body": np.arange(12, dtype=np.float64)}

    cfg, _ = _dataset()
    rc, _ = evaluate.condition_config(cfg, "trained_extra_features")
    builder = evaluate._random_feature_builder(_Runner(), rc, 1000, "trained_extra_features")
    first, second = builder(), builder()
    assert first.shape == (300,)
    assert np.allclose(first, second), "extra features must be deterministic"
    other = evaluate._random_feature_builder(
        _Runner(), rc, 1001, "trained_extra_features"
    )()
    assert not np.allclose(first, other), "extra features must depend on the seed"

    assert evaluate._random_feature_builder(_Runner(), cfg, 1000, "trained") is None


def test_split_is_15_5_per_category_and_deterministic():
    # Requirement: Held-out evaluation -- train/test split
    cfg, dataset = _dataset()
    split = evaluate.make_split(dataset, 15, 5, seed=1000)

    assert len(split.train) == 60
    assert len(split.test) == 20
    for label in range(len(dataset.categories)):
        assert sum(1 for i in split.train if i.label == label) == 15
        assert sum(1 for i in split.test if i.label == label) == 5

    again = evaluate.make_split(dataset, 15, 5, seed=1000)
    assert sorted(i.word for i in split.train) == sorted(i.word for i in again.train)
    assert sorted(i.word for i in split.test) == sorted(i.word for i in again.test)

    other = evaluate.make_split(dataset, 15, 5, seed=1001)
    assert sorted(i.word for i in other.test) != sorted(i.word for i in split.test)

    train_words = {i.word for i in split.train}
    test_words = {i.word for i in split.test}
    assert not train_words & test_words, "no word may appear in both sets"


def test_split_rejects_category_that_is_too_small():
    cfg, dataset = _dataset()
    try:
        evaluate.make_split(dataset, 15, 6, seed=1000)
    except evaluate.EvaluationError as error:
        assert "needs 21" in str(error)
    else:
        raise AssertionError("an undersized category must be rejected")


def test_significance_reports_mean_ci_level_and_test():
    # Requirement: Chance baseline -- statistical comparison
    cfg, _ = _dataset()
    accuracies = [0.30, 0.35, 0.40, 0.32, 0.38, 0.33, 0.36, 0.31, 0.39, 0.34]
    result = evaluate.test_against_chance(
        accuracies, chance=0.25, level=0.01, seed=1000
    ).as_dict()

    assert result["n_seeds"] == 10
    assert result["chance_level"] == 0.25
    assert result["significance_level"] == 0.01, "the level used must be stated"
    assert result["test"]
    assert abs(result["mean_accuracy"] - float(np.mean(accuracies))) < 1e-12
    assert result["ci_low"] <= result["mean_accuracy"] <= result["ci_high"]
    assert 0.0 <= result["p_value"] <= 1.0
    assert result["significant"] is True


def test_significance_handles_identical_seeds_at_chance():
    # All seeds landing exactly on chance gives a 0/0 t statistic. The report
    # must not contain a bare nan, and must not claim significance.
    cfg, _ = _dataset()
    result = evaluate.test_against_chance([0.25] * 10, chance=0.25, level=0.01)

    assert result.p_value == 1.0
    assert result.significant is False
    payload = result.as_dict()
    assert payload["p_value"] == 1.0
    assert "note" in payload, "the degenerate case must be explained"


def test_significance_zero_variance_off_chance_is_not_significant():
    # Two (or more) seeds landing on the SAME accuracy give an infinite t
    # statistic, not evidence: there is no variance from which to infer. Scipy
    # would return p = 0 and the report would claim the opposite of what the
    # data support, so zero variance must never be reported as significant.
    cfg, _ = _dataset()
    result = evaluate.test_against_chance([0.283] * 2, chance=0.25, level=0.01)

    assert result.significant is False
    assert result.p_value == 1.0
    assert "zero variance" in result.note


def test_significance_requires_at_least_two_seeds():
    cfg, _ = _dataset()
    try:
        evaluate.test_against_chance([0.3], chance=0.25, level=0.01)
    except evaluate.EvaluationError as error:
        assert "at least 2 seeds" in str(error)
    else:
        raise AssertionError("a single seed must not produce a significance test")


def test_every_required_condition_is_reported():
    # Requirement: Required baselines and ablations
    cfg, dataset = _dataset()
    seed_results = _seed_results(lambda condition, offset: 0.30 + 0.001 * offset)
    report = evaluate.aggregate(
        seed_results, dataset, cfg, metadata={"base_seed": 1000}
    )

    for condition in evaluate.CONDITIONS:
        assert condition in report.conditions, f"{condition} missing from report"
        stats = report.conditions[condition]
        assert "mean_accuracy" in stats
        assert "ci_low" in stats and "ci_high" in stats
        assert stats["n_seeds"] == 10


def test_shuffled_labels_above_chance_raises_leakage_warning():
    # Requirement: Required baselines -- data-leakage warning
    cfg, dataset = _dataset()
    seed_results = _seed_results(
        lambda condition, offset: 0.90 if condition == "shuffled_labels" else 0.20
    )
    report = evaluate.aggregate(
        seed_results, dataset, cfg, metadata={"base_seed": 1000}
    )

    assert report.leakage_warning is not None
    assert "shuffled" in report.leakage_warning.lower()

    markdown = report_mod.build_markdown(
        report, seed_results, dataset.categories,
        {"flywire_release": "783", "n_neurons": 176, "n_edges": 552},
    )
    assert "## Data-leakage warning" in markdown


def test_shuffled_labels_at_chance_raises_no_warning():
    cfg, dataset = _dataset()
    seed_results = _seed_results(
        lambda condition, offset: 0.25 if condition == "shuffled_labels" else 0.20
    )
    report = evaluate.aggregate(
        seed_results, dataset, cfg, metadata={"base_seed": 1000}
    )
    assert report.leakage_warning is None


def test_shuffled_labels_above_chance_but_at_untrained_floor_raises_no_warning():
    # The recode raises the representation's own decodability: the untrained
    # network already decodes the label above chance. The shuffled control may
    # sit above chance for the same reason without leaking anything, so the
    # warning must compare against the untrained floor, not chance alone.
    cfg, dataset = _dataset()

    def acc(condition, offset):
        if condition == "shuffled_labels":
            return 0.33
        if condition == "untrained":
            return 0.34
        return 0.30

    report = evaluate.aggregate(
        _seed_results(acc), dataset, cfg, metadata={"base_seed": 1000}
    )
    assert report.leakage_warning is None


def test_shuffled_labels_above_untrained_floor_raises_leakage_warning():
    cfg, dataset = _dataset()

    def acc(condition, offset):
        if condition == "shuffled_labels":
            return 0.55
        if condition == "untrained":
            return 0.30
        return 0.30

    report = evaluate.aggregate(
        _seed_results(acc), dataset, cfg, metadata={"base_seed": 1000}
    )
    assert report.leakage_warning is not None
    assert "untrained" in report.leakage_warning


def test_decoder_is_fit_on_presented_labels_not_dataset_labels(monkeypatch):
    # Regression: the supervised linear decoder must be fit on the labels each
    # training trial was PRESENTED with. Fitting on the dataset labels made the
    # shuffled-labels control recover a mapping the network was never taught,
    # which is what produced the spurious 0.615 "leakage" score.
    from flyread import learning, network as net_mod, structural as structural_mod

    cfg = config.Config.load("configs/smoke.yaml")

    n_classes = 4
    label_of = {}
    train_items, test_items = [], []
    for label in range(n_classes):
        for k in range(8):
            word = f"t-{label}-{k}"
            label_of[word] = label
            train_items.append(encoding.WordItem(word, str(label), label))
        for k in range(4):
            word = f"e-{label}-{k}"
            label_of[word] = label
            test_items.append(encoding.WordItem(word, str(label), label))

    dataset = encoding.Dataset(
        items=train_items + test_items,
        categories=[str(i) for i in range(n_classes)],
        path="synthetic",
        sha256="0",
    )
    split = evaluate.Split(train=list(train_items), test=list(test_items), seed=1000)

    def feature(word):
        vec = np.zeros(n_classes, dtype=np.float64)
        vec[label_of[word]] = 1.0
        return vec

    class _Record:
        def __init__(self, word):
            self.spike_counts = feature(word)
            self.predicted = 0
            self.reward_type = "reward"
            self.correct = False
            self.no_response = False
            self.dopamine = 0.0
            self.trial = 0

    class _Stats:
        def as_dict(self):
            return {}

    class _FakeRunner:
        def __init__(self, *args, **kwargs):
            self.last_stimulus_rates = {}
            self.training_history = []
            self.history = []
            self.stats = _Stats()

        def run_trial(self, word, label, step, learn=True):
            return _Record(word)

        def accuracy(self, since=0):
            return 0.0

        def learning_curve(self, window, history=None):
            return []

    class _FakeStructural:
        def __init__(self, *args, **kwargs):
            self.events = []

        def set_trial_duration(self, *args, **kwargs):
            pass

        def observe_trial(self, *args, **kwargs):
            pass

        def observe(self, *args, **kwargs):
            pass

        def maybe_step(self, *args, **kwargs):
            pass

        def summary(self):
            return {}

    # Nearest-centroid decoder: generalises from the fit labels, so a correct
    # label mapping yields perfect held-out accuracy and a shuffled one does
    # not. Reading dataset labels on the shuffled run would restore perfection.
    def fake_fit(features, labels, n, run_config):
        x = np.asarray(features, dtype=np.float64)
        y = np.asarray(labels, dtype=np.int64)
        return {
            "centroids": np.stack(
                [x[y == c].mean(axis=0) if np.any(y == c) else np.zeros(x.shape[1])
                 for c in range(n)]
            )
        }

    def fake_predict(features, decoder):
        x = np.asarray(features, dtype=np.float64)
        c = decoder["centroids"]
        return ((x[:, None, :] - c[None, :, :]) ** 2).sum(axis=2).argmin(axis=1)

    class _FakeRng:
        def __init__(self, reverse):
            self.reverse = reverse

        def permutation(self, n):
            indices = np.arange(n)
            return indices[::-1] if self.reverse else indices

    def fake_make_rng(seed, stream, **kwargs):
        return _FakeRng(reverse=(stream == "shuffled-labels"))

    monkeypatch.setattr(evaluate, "TrialRunner", _FakeRunner)
    monkeypatch.setattr(evaluate, "make_mapping", lambda *a, **k: object())
    monkeypatch.setattr(evaluate, "make_rng", fake_make_rng)
    monkeypatch.setattr(net_mod, "build_network", lambda *a, **k: object())
    monkeypatch.setattr(structural_mod, "StructuralPlasticity", _FakeStructural)
    monkeypatch.setattr(learning, "fit_readout_decoder", fake_fit)
    monkeypatch.setattr(learning, "decoder_predict", fake_predict)

    class _Subcircuit:
        roles = {"input": [0, 1, 2, 3]}

    trained = evaluate.run_condition(
        "trained", dataset, split, 1000, cfg, 1.0, _Subcircuit()
    )
    shuffled = evaluate.run_condition(
        "shuffled_labels", dataset, split, 1000, cfg, 1.0, _Subcircuit()
    )

    assert trained.test_accuracy > 0.9
    assert shuffled.test_accuracy < 0.5


def test_confusion_matrix_is_four_by_four_with_recall():
    # Requirement: Confusion matrix and no-response rate
    matrix = evaluate.ConfusionMatrix(
        labels=[0, 1, 2, 3],
        counts=np.array([[3, 1, 0, 0], [0, 4, 0, 0], [1, 0, 3, 0], [0, 0, 1, 2]]),
    )
    assert matrix.counts.shape == (4, 4)
    recall = matrix.per_class_recall()
    assert set(recall) == {0, 1, 2, 3}
    assert abs(recall[1] - 1.0) < 1e-12

    total = evaluate.confusion_matrix_total(
        _seed_results(lambda c, o: 0.3), "trained", 4
    )
    assert total.counts.shape == (4, 4)
    assert total.counts.sum() == 0, "synthetic runs have no held-out records"


def _seed_results_with(conditions):
    confusion = evaluate.ConfusionMatrix(
        labels=[0, 1, 2, 3], counts=np.zeros((4, 4), dtype=np.int64)
    )
    return [
        evaluate.SeedResult(
            seed=1000 + offset,
            results={
                condition: _result(condition, 1000 + offset, 0.3, confusion)
                for condition in conditions
            },
            weight_scale=0.05,
            seconds=1.0,
        )
        for offset in range(2)
    ]


def test_pick_confusion_condition_prefers_trained_when_present():
    seed_results = _seed_results_with(["untrained", "trained", "structural_on"])
    chosen = evaluate.pick_confusion_condition(
        seed_results, ["untrained", "trained", "structural_on"]
    )
    assert chosen == "trained"


def test_pick_confusion_condition_falls_back_for_single_condition_run():
    # Requirement: isolation controls run one condition and omit 'trained'.
    conditions = ["shuffled_labels_no_growth"]
    seed_results = _seed_results_with(conditions)
    chosen = evaluate.pick_confusion_condition(seed_results, conditions)
    assert chosen == "shuffled_labels_no_growth"


def test_pick_confusion_condition_raises_when_nothing_ran():
    seed_results = _seed_results_with([])
    try:
        evaluate.pick_confusion_condition(seed_results, ["trained"])
    except evaluate.EvaluationError as error:
        assert "any condition" in str(error)
    else:
        raise AssertionError("an empty run must be rejected")


def test_run_result_round_trips_through_checkpoint_dict():
    # Requirement: resume a killed run from seed_conditions.jsonl
    confusion = evaluate.ConfusionMatrix(
        labels=[0, 1, 2, 3],
        counts=np.array([[3, 1, 0, 0], [0, 4, 0, 0], [1, 0, 3, 0], [0, 0, 1, 2]]),
    )
    original = _result("shuffled_labels_no_growth", 1002, 0.31, confusion)
    payload = original.as_dict(include_records=False)
    restored = evaluate.RunResult.from_dict(payload)
    assert restored.condition == original.condition
    assert restored.seed == original.seed
    assert restored.trained == original.trained
    assert abs(restored.test_accuracy - original.test_accuracy) < 1e-12
    assert abs(restored.train_accuracy - original.train_accuracy) < 1e-12
    assert restored.learning_curve == original.learning_curve
    assert np.array_equal(restored.confusion.counts, original.confusion.counts)


def test_load_checkpoint_ignores_blank_and_duplicate_lines(tmp_path):
    from flyread import cli

    path = tmp_path / "seed_conditions.jsonl"
    path.write_text(
        '{"seed": 1002, "condition": "untrained", "test_accuracy": 0.2}\n'
        "\n"
        '{"seed": 1002, "condition": "untrained", "test_accuracy": 0.217}\n'
        '{"seed": 1003, "condition": "trained", "test_accuracy": 0.3}\n'
    )
    completed = cli._load_checkpoint(path)
    assert completed[(1002, "untrained")]["test_accuracy"] == 0.217
    assert (1003, "trained") in completed
    assert cli._load_checkpoint(tmp_path / "missing.jsonl") == {}


def test_learning_curve_is_recorded_per_window():
    # Requirement: Learning curves
    cfg, dataset = _dataset()
    seed_results = _seed_results(lambda c, o: 0.4)
    result = seed_results[0].results["trained"]
    assert len(result.learning_curve) == 3
    for point in result.learning_curve:
        assert "trial" in point and "accuracy" in point

    report = evaluate.aggregate(
        seed_results, dataset, cfg, metadata={"base_seed": 1000}
    )
    assert "trained" in report.per_seed
    assert len(report.per_seed["trained"]) == 10


def test_negative_result_is_stated_explicitly():
    # Requirement: Negative results are reportable
    cfg, dataset = _dataset()
    seed_results = _seed_results(lambda c, o: 0.20)
    report = evaluate.aggregate(
        seed_results, dataset, cfg, metadata={"base_seed": 1000}
    )
    headline = report_mod.headline(report)

    assert "NOT significantly above" in headline
    assert "negative or null result" in headline
    assert report.conditions["trained"]["significant"] is False


def _growth_summary(recruits=200, created=11000, removed=100):
    return {
        "enabled": True,
        "events_by_kind": {"recruit": recruits, "prune": removed, "silence": 0},
        "stats": {
            "recruits": recruits,
            "expansion_pruned_total": removed,
            "retired_total": removed,
        },
        "reserve": {
            "size": 1000,
            "recruited": recruits,
            "available": 1000 - recruits,
            "expansion_synapses": created,
            "readout_units": recruits,
            "prune_target": removed,
        },
        "note": "recruitment rule",
    }


def test_structural_growth_section_reports_final_counts():
    # Requirement: report final neuron/synapse counts after growth
    seed_results = _seed_results(lambda c, o: 0.30)
    grown = seed_results[0].results["structural_on"]
    grown.structural = _growth_summary()

    section = report_mod.structural_growth_section(
        seed_results, {"n_neurons": 1928, "n_edges": 21233}
    )

    assert "## Structural growth" in section
    # base 1928 + 200 recruits; base 21233 + (11000 - 100) net synapses
    assert "2128" in section  # final neurons
    assert "10900" in section  # net new synapses
    assert "32133" in section  # 21233 + 10900 final synapses
    assert "recruit" in section.lower()


def test_structural_growth_section_empty_without_growth():
    seed_results = _seed_results(lambda c, o: 0.30)
    section = report_mod.structural_growth_section(
        seed_results, {"n_neurons": 1928, "n_edges": 21233}
    )
    assert section == ""


def test_report_markdown_includes_growth_when_present():
    cfg, dataset = _dataset()
    seed_results = _seed_results(lambda c, o: 0.30)
    seed_results[0].results["structural_on"].structural = _growth_summary()
    report = evaluate.aggregate(
        seed_results, dataset, cfg, metadata={"base_seed": 1000}
    )

    markdown = report_mod.build_markdown(
        report, seed_results, dataset.categories,
        {"flywire_release": "783", "n_neurons": 1928, "n_edges": 21233},
    )
    assert "## Structural growth" in markdown