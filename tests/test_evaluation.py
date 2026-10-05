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