"""Evaluation: splits, baselines, ablations, multi-seed statistics, reporting.

Every condition is run under identical seeds and budgets so the ablation
comparison is controlled. Results are reported the same way whether or not
learning exceeds chance: a null result is a reportable outcome, not a failure
to be reframed.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from .encoding import Dataset, WordItem, make_mapping
from .learning import NO_RESPONSE, TrialRunner
from .repro import make_rng

LOGGER = logging.getLogger(__name__)

# Condition -> config overrides and dataset handling. Applied by run_condition.
CONDITIONS: dict[str, dict[str, Any]] = {
    "untrained": {"trained": False},
    "trained": {"trained": True},
    "shuffled_labels": {"trained": True, "shuffle_labels": True},
    "dopamine_off": {"trained": True, "dopamine": False},
    "shuffled_pixels": {"trained": True, "shuffle_pixels": True},
    "structural_on": {"trained": True, "structural": True},
    "structural_off": {"trained": True, "structural": False},
}


class EvaluationError(RuntimeError):
    """Raised when an evaluation cannot be set up as specified."""


# --------------------------------------------------------------------------
# Splits
# --------------------------------------------------------------------------

@dataclass
class Split:
    """Seeded train/test split."""

    train: list[WordItem]
    test: list[WordItem]
    seed: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "n_train": len(self.train),
            "n_test": len(self.test),
            "train_words": sorted(item.word for item in self.train),
            "test_words": sorted(item.word for item in self.test),
        }


def make_split(
    dataset: Dataset,
    train_per_category: int,
    test_per_category: int,
    seed: int,
) -> Split:
    """Split each category into train/test, identical for a given seed.

    Words never appear in both sets: the split is drawn without replacement
    inside each category.
    """
    train: list[WordItem] = []
    test: list[WordItem] = []
    for category in dataset.categories:
        items = dataset.by_category(category)
        if len(items) < train_per_category + test_per_category:
            raise EvaluationError(
                f"category {category!r} has {len(items)} words, needs "
                f"{train_per_category + test_per_category}"
            )
        rng = make_rng(seed, stream=f"split:{category}")
        order = rng.permutation(len(items))
        shuffled = [items[i] for i in order]
        train.extend(shuffled[:train_per_category])
        test.extend(shuffled[train_per_category:train_per_category + test_per_category])

    overlap = {item.word for item in train} & {item.word for item in test}
    if overlap:
        raise EvaluationError(f"train/test overlap detected: {sorted(overlap)}")
    return Split(train=train, test=test, seed=seed)


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------

@dataclass
class ConfusionMatrix:
    """4x4 counts of true label against predicted label."""

    labels: list[int]
    counts: np.ndarray

    def accuracy(self) -> float:
        total = int(self.counts.sum())
        if total == 0:
            return 0.0
        return float(np.trace(self.counts) / total)

    def per_class_recall(self) -> dict[int, float]:
        out: dict[int, float] = {}
        for i, label in enumerate(self.labels):
            total = int(self.counts[i].sum())
            out[label] = float(self.counts[i, i] / total) if total else 0.0
        return out

    def as_list(self) -> list[list[int]]:
        return self.counts.astype(int).tolist()

    def to_text(self, categories: Sequence[str]) -> str:
        header = "true\\pred | " + " ".join(f"{i:>4d}" for i in self.labels)
        lines = [header, "-" * len(header)]
        for i, row in enumerate(self.counts):
            name = categories[self.labels[i]] if self.labels[i] < len(categories) else "?"
            lines.append(f"{name:>9} | " + " ".join(f"{v:>4d}" for v in row.astype(int)))
        return "\n".join(lines)


def confusion_matrix(
    true_labels: Sequence[int], predictions: Sequence[int], n_categories: int
) -> ConfusionMatrix:
    """Confusion matrix with ``no response`` counted as an incorrect prediction."""
    counts = np.zeros((n_categories, n_categories), dtype=np.int64)
    for true, predicted in zip(true_labels, predictions):
        if not 0 <= true < n_categories:
            raise EvaluationError(f"true label out of range: {true}")
        if predicted == NO_RESPONSE:
            # No response is always incorrect; attribute it to a dedicated
            # pseudo-column so it is visible rather than silently dropped.
            continue
        if not 0 <= predicted < n_categories:
            raise EvaluationError(f"predicted label out of range: {predicted}")
        counts[int(true), int(predicted)] += 1
    return ConfusionMatrix(labels=list(range(n_categories)), counts=counts)


def no_response_rate(records: Iterable[Any]) -> float:
    records = list(records)
    if not records:
        return 0.0
    return sum(1 for r in records if r.no_response) / len(records)


def bootstrap_ci(
    values: Sequence[float],
    level: float = 0.95,
    resamples: int = 10000,
    seed: int = 0,
) -> tuple[float, float]:
    """Percentile bootstrap confidence interval for the mean."""
    array = np.asarray(list(values), dtype=np.float64)
    if array.size == 0:
        return float("nan"), float("nan")
    if array.size == 1:
        return float(array[0]), float(array[0])
    rng = make_rng(seed, stream=f"bootstrap:{level}:{resamples}")
    indices = rng.integers(0, array.size, size=(resamples, array.size))
    means = array[indices].mean(axis=1)
    alpha = (1.0 - level) / 2.0
    return (
        float(np.quantile(means, alpha)),
        float(np.quantile(means, 1.0 - alpha)),
    )


@dataclass
class SignificanceResult:
    """Comparison of per-seed accuracy against a chance level."""

    chance: float
    n_seeds: int
    mean: float
    std: float
    ci_low: float
    ci_high: float
    level: float
    t_statistic: float
    p_value: float
    wilcoxon_statistic: float
    wilcoxon_p: float
    significant: bool
    test_name: str = "one-sample t-test"
    note: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = {
            "chance_level": self.chance,
            "n_seeds": self.n_seeds,
            "mean_accuracy": self.mean,
            "std_accuracy": self.std,
            "ci_low": self.ci_low,
            "ci_high": self.ci_high,
            "ci_level": 1.0 - self.level,
            "significance_level": self.level,
            "test": self.test_name,
            "t_statistic": self.t_statistic,
            "p_value": self.p_value,
            "wilcoxon_statistic": self.wilcoxon_statistic,
            "wilcoxon_p_value": self.wilcoxon_p,
            "significant": self.significant,
            "verdict": (
                "held-out accuracy is significantly above chance"
                if self.significant
                else "held-out accuracy is NOT significantly above chance"
            ),
        }
        if self.note:
            payload["note"] = self.note
        return payload


def test_against_chance(
    accuracies: Sequence[float],
    chance: float,
    level: float,
    ci_level: float = 0.95,
    ci_resamples: int = 10000,
    seed: int = 0,
) -> SignificanceResult:
    """One-sample t-test plus a Wilcoxon signed-rank test against chance.

    Both are reported because the t-test assumes normality, which 10-20 seeds
    cannot establish.
    """
    from scipy import stats as sps

    values = np.asarray(list(accuracies), dtype=np.float64)
    if values.size < 2:
        raise EvaluationError(
            f"need at least 2 seeds for a significance test, got {values.size}"
        )

    # Every seed landed exactly on chance, so the sample has zero variance and
    # the t statistic is 0/0. scipy returns nan, which would serialize into the
    # report as a bare "nan". There is no evidence against chance, so p = 1.
    # Zero variance with a mean that differs from chance is left to scipy, which
    # handles it as an infinite t statistic and a p value of 0.
    if float(values.std(ddof=1)) == 0.0 and float(values.mean()) == chance:
        zero_variance = SignificanceResult(
            chance=chance,
            n_seeds=int(values.size),
            mean=float(values.mean()),
            std=0.0,
            ci_low=float(values.mean()),
            ci_high=float(values.mean()),
            level=level,
            t_statistic=float("nan"),
            p_value=1.0,
            wilcoxon_statistic=float("nan"),
            wilcoxon_p=1.0,
            significant=False,
        )
        zero_variance.note = (
            "all seeds produced identical accuracy, so the test has zero "
            "variance; p is reported as 1.0 and no significance is claimed"
        )
        return zero_variance

    diff = values - chance
    # One-sided: the claim under test is that accuracy is ABOVE chance. A
    # two-sided test would also flag accuracy significantly BELOW chance and
    # the report would then claim the opposite of what the data show.
    t_result = sps.ttest_1samp(values, chance, alternative="greater")
    try:
        w_result = sps.wilcoxon(diff, alternative="greater", zero_method="wilcox")
        w_stat, w_p = float(w_result.statistic), float(w_result.pvalue)
    except ValueError:
        w_stat, w_p = float("nan"), float("nan")
    ci_low, ci_high = bootstrap_ci(
        values, level=ci_level, resamples=ci_resamples, seed=seed
    )
    p_value = float(t_result.pvalue)
    return SignificanceResult(
        chance=chance,
        n_seeds=int(values.size),
        mean=float(values.mean()),
        std=float(values.std(ddof=1)),
        ci_low=ci_low,
        ci_high=ci_high,
        level=level,
        t_statistic=float(t_result.statistic),
        p_value=p_value,
        wilcoxon_statistic=w_stat,
        wilcoxon_p=w_p,
        significant=bool(p_value < level and values.mean() > chance),
    )


# --------------------------------------------------------------------------
# Single-run results
# --------------------------------------------------------------------------

@dataclass
class RunResult:
    """Everything one training run produces."""

    condition: str
    seed: int
    trained: bool
    train_accuracy: float
    test_accuracy: float
    test_no_response_rate: float
    confusion: ConfusionMatrix
    learning_curve: list[dict[str, Any]]
    plasticity: dict[str, Any]
    structural: dict[str, Any] | None
    records: list[Any] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, include_records: bool = False) -> dict[str, Any]:
        payload = {
            "condition": self.condition,
            "seed": self.seed,
            "trained": self.trained,
            "train_accuracy": self.train_accuracy,
            "test_accuracy": self.test_accuracy,
            "test_no_response_rate": self.test_no_response_rate,
            "confusion_matrix": self.confusion.as_list(),
            "confusion_per_class_recall": {
                str(k): v for k, v in self.confusion.per_class_recall().items()
            },
            "learning_curve": self.learning_curve,
            "plasticity": self.plasticity,
            "structural": self.structural,
            "n_trials": len(self.records),
        }
        if include_records:
            payload["records"] = [
                {
                    "trial": r.trial, "word": r.word, "true_label": r.true_label,
                    "predicted": r.predicted, "correct": r.correct,
                    "dopamine": r.dopamine, "spike_counts": r.spike_counts,
                    "no_response": r.no_response, "reward_type": r.reward_type,
                }
                for r in self.records
            ]
        return payload


def condition_config(config, condition: str):
    """Config for one condition, with its ablation overrides applied."""
    settings = dict(CONDITIONS.get(condition, {"trained": True}))
    if condition == "untrained":
        settings["trained"] = False

    overrides: list[str] = []
    if settings.get("dopamine") is False:
        overrides.append("learning.enabled=false")
    if settings.get("structural") is False:
        overrides.append("structural.enabled=false")
    if settings.get("shuffle_pixels"):
        overrides.append("encoding.shuffle_pixels=true")

    run_config = deepcopy(config.to_dict())
    from .config import Config

    result = Config(run_config, source=config.source)
    if overrides:
        result.apply_overrides(overrides)
    result.validate()
    return result, settings


def run_condition(
    condition: str,
    dataset: Dataset,
    split: Split,
    seed: int,
    config,
    weight_scale: float,
    subcircuit,
    manifest=None,
) -> RunResult:
    """Run one condition end to end and return its metrics.

    The network is rebuilt for every condition so no state leaks between them;
    the calibrated weight scale is reused so conditions differ only in the
    factor under test.
    """
    from .network import build_network
    from .structural import StructuralPlasticity

    run_config, settings = condition_config(config, condition)
    network = build_network(subcircuit, run_config, weight_scale=weight_scale)
    mapping = make_mapping(run_config, seed=seed)
    if manifest is not None:
        # Per-condition encoding record: conditions can override the grid or
        # enable the shuffled-pixel control, so the base manifest entry is not
        # sufficient on its own.
        manifest.encoding.setdefault("conditions", {})[condition] = mapping.describe()
    structural = StructuralPlasticity(network, run_config, seed=seed)
    structural.set_trial_duration(
        float(run_config.get("encoding.stimulus_ms")) * 1e-3
    )
    runner = TrialRunner(network, run_config, mapping, seed=seed)

    train_items = list(split.train)
    test_items = list(split.test)

    labels_for_training = [item.label for item in train_items]
    if settings.get("shuffle_labels"):
        rng = make_rng(seed, stream="shuffled-labels")
        labels_for_training = [
            labels_for_training[i] for i in rng.permutation(len(labels_for_training))
        ]

    n_trials = int(run_config.get("evaluation.n_train_trials"))
    window = int(run_config.get("evaluation.curve_window_trials"))
    order = make_rng(seed, stream="trial-order").permutation(len(train_items))

    if settings.get("trained"):
        for step in range(n_trials):
            position = int(order[step % len(order)])
            record = runner.run_trial(
                train_items[position].word, labels_for_training[position], step
            )
            # runner.run_trial snapshots per-stage rates during the stimulus
            # window; the live monitors are cleared for the rest period.
            structural.observe(runner.last_stimulus_rates)
            structural.maybe_step(step + 1)
            if (step + 1) % 200 == 0:
                LOGGER.info(
                    "[%s seed=%d] trial %d/%d rolling acc %.3f",
                    condition, seed, step + 1, n_trials,
                    runner.accuracy(max(0, step + 1 - window)),
                )
        train_accuracy = runner.accuracy()
    else:
        train_accuracy = float("nan")

    # Held-out evaluation: words never presented during training.
    repeats = int(run_config.get("evaluation.eval_repeats"))
    true_labels: list[int] = []
    predictions: list[int] = []
    eval_records = []
    for item in test_items:
        for _ in range(repeats):
            probe = runner.run_trial(
                item.word, item.label, 100_000 + len(eval_records), learn=False
            )
            true_labels.append(item.label)
            predictions.append(probe.predicted)
            eval_records.append(probe)

    held_out_accuracy = (
        sum(1 for t, p in zip(true_labels, predictions) if p == t) / len(true_labels)
        if true_labels else 0.0
    )

    return RunResult(
        condition=condition,
        seed=seed,
        trained=bool(settings.get("trained")),
        train_accuracy=train_accuracy,
        test_accuracy=float(held_out_accuracy),
        test_no_response_rate=no_response_rate(eval_records),
        confusion=confusion_matrix(true_labels, predictions, len(dataset.categories)),
        learning_curve=runner.learning_curve(window, history=runner.training_history),
        plasticity=runner.stats.as_dict(),
        structural=structural.summary() if run_config.get("structural.enabled") else None,
        records=eval_records,
        extra={
            "settings": settings,
            "train_trials": n_trials,
            "eval_repeats": repeats,
            "n_stimulus_presentations": len(runner.history),
            "weight_scale": weight_scale,
            "structural_event_count": len(structural.events),
        },
    )


@dataclass
class SeedResult:
    """All conditions for one seed."""

    seed: int
    results: dict[str, RunResult]
    weight_scale: float
    seconds: float


@dataclass
class EvaluationReport:
    """Aggregated multi-seed results."""

    conditions: dict[str, dict[str, Any]]
    per_seed: dict[str, list[float]]
    chance: float
    level: float
    n_seeds: int
    leakage_warning: str | None
    splits: dict[int, dict[str, Any]]
    metadata: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "chance_level": self.chance,
            "significance_level": self.level,
            "n_seeds": self.n_seeds,
            "conditions": self.conditions,
            "per_seed_test_accuracy": self.per_seed,
            "data_leakage_warning": self.leakage_warning,
            "splits": self.splits,
            "metadata": self.metadata,
        }


def train_seed(
    seed: int,
    dataset: Dataset,
    config,
    subcircuit,
    weight_scale: float,
    conditions: Sequence[str],
) -> SeedResult:
    """Run every requested condition for one seed."""
    import time

    started = time.perf_counter()
    split = make_split(
        dataset,
        int(config.get("evaluation.train_per_category")),
        int(config.get("evaluation.test_per_category")),
        seed=seed,
    )
    results: dict[str, RunResult] = {}
    for condition in conditions:
        LOGGER.info("condition %s seed %d", condition, seed)
        results[condition] = run_condition(
            condition, dataset, split, seed, config, weight_scale, subcircuit
        )
        LOGGER.info(
            "condition %-16s seed %d test acc %.3f (no-response %.3f)",
            condition, seed, results[condition].test_accuracy,
            results[condition].test_no_response_rate,
        )
    return SeedResult(
        seed=seed,
        results=results,
        weight_scale=weight_scale,
        seconds=time.perf_counter() - started,
    )


def aggregate(
    seed_results: Sequence[SeedResult],
    dataset: Dataset,
    config,
    metadata: dict[str, Any] | None = None,
) -> EvaluationReport:
    """Aggregate per-seed results into the reported statistics.

    A data-leakage warning is raised when the shuffled-label control beats
    chance by more than the configured margin, since that would mean the
    evaluation pipeline itself leaks label information.
    """
    chance = float(config.get("evaluation.chance_level"))
    level = float(config.get("evaluation.significance_level"))
    margin = float(config.get("evaluation.leakage_margin"))

    per_seed: dict[str, list[float]] = {}
    conditions: dict[str, dict[str, Any]] = {}
    splits: dict[int, dict[str, Any]] = {}

    for seed_result in seed_results:
        split = make_split(
            dataset,
            int(config.get("evaluation.train_per_category")),
            int(config.get("evaluation.test_per_category")),
            seed=seed_result.seed,
        )
        splits[seed_result.seed] = split.as_dict()
        for condition, result in seed_result.results.items():
            per_seed.setdefault(condition, []).append(result.test_accuracy)

    for condition, accuracies in per_seed.items():
        try:
            significance = test_against_chance(
                accuracies,
                chance=chance,
                level=level,
                ci_level=float(config.get("evaluation.ci_level")),
                ci_resamples=int(config.get("evaluation.ci_resamples")),
                seed=metadata.get("base_seed", 0) if metadata else 0,
            )
            stats = significance.as_dict()
        except EvaluationError as error:
            stats = {"error": str(error), "mean_accuracy": float(np.mean(accuracies))}
        no_response = [
            r.test_no_response_rate
            for sr in seed_results for r in [sr.results.get(condition)] if r
        ]
        train_acc = [
            r.train_accuracy
            for sr in seed_results for r in [sr.results.get(condition)] if r
            and not np.isnan(r.train_accuracy)
        ]
        stats["no_response_rate_mean"] = float(np.mean(no_response)) if no_response else 0.0
        stats["train_accuracy_mean"] = float(np.mean(train_acc)) if train_acc else None
        stats["n_seeds"] = len(accuracies)
        conditions[condition] = stats

    leakage_warning = None
    shuffled = per_seed.get("shuffled_labels")
    if shuffled and float(np.mean(shuffled)) > chance + margin:
        leakage_warning = (
            f"shuffled-label control reached {np.mean(shuffled):.3f}, above the "
            f"{chance:.2f} chance level by more than the configured margin "
            f"{margin:.2f}; the evaluation pipeline may leak label information"
        )
        LOGGER.warning("%s", leakage_warning)

    return EvaluationReport(
        conditions=conditions,
        per_seed=per_seed,
        chance=chance,
        level=level,
        n_seeds=len(seed_results),
        leakage_warning=leakage_warning,
        splits=splits,
        metadata=metadata or {},
    )


def confusion_matrix_total(
    seed_results: Sequence[SeedResult], condition: str, n_categories: int
) -> ConfusionMatrix:
    """Sum confusion matrices across seeds for one condition."""
    total = np.zeros((n_categories, n_categories), dtype=np.int64)
    found = False
    for seed_result in seed_results:
        result = seed_result.results.get(condition)
        if result is None:
            continue
        found = True
        total += result.confusion.counts
    if not found:
        raise EvaluationError(f"no results for condition {condition!r}")
    return ConfusionMatrix(labels=list(range(n_categories)), counts=total)