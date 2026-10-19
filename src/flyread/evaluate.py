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
    # "trained" is the no-growth learning control: structural.disabled so it
    # differs from structural_on only by the growth mechanism (previously both
    # fell back to the config's structural.enabled, making them identical).
    "trained": {"trained": True, "structural": False},
    # Online / RPE condition. Same no-growth pipeline and per-output credit as
    # `trained`, but the per-class valence is gated by the reward prediction
    # error (magnitude = |reward - running expectation|), so expected outcomes
    # stop teaching and only surprising ones move the readout. This is the
    # condition that tests whether the plastic readout itself can learn online
    # (read from the network's own winner-take-most spikes, not a refit decoder).
    "online_rpe": {
        "trained": True,
        "structural": False,
        "overrides": [
            "learning.credit_assignment=per_class",
            "learning.rpe_per_class=true",
        ],
    },
    "shuffled_labels": {"trained": True, "shuffle_labels": True},
    # Isolation control: shuffled labels AND growth off. If the shuffled
    # control sits above the no-learning floor, comparing this against
    # shuffled_labels (growth on) says whether structural recruitment, whose
    # category guidance uses the network's own predictions, is responsible.
    "shuffled_labels_no_growth": {
        "trained": True,
        "shuffle_labels": True,
        "structural": False,
    },
    "dopamine_off": {"trained": True, "dopamine": False},
    # Completes the dopamine x growth 2x2: dopamine off AND growth off, the
    # baseline against which both single ablations are read.
    "dopamine_off_no_growth": {
        "trained": True,
        "dopamine": False,
        "structural": False,
    },
    "shuffled_pixels": {"trained": True, "shuffle_pixels": True},
    "structural_on": {"trained": True, "structural": True},
    "structural_off": {"trained": True, "structural": False},
    # Positive control: the SAME pipeline and plastic readout, but the readout
    # starts from supervised logistic-regression weights instead of noise
    # (learning.pretrain). Dopamine then fine-tunes from an informative start.
    # A no-growth control (structural disabled) so it differs from
    # structural_on/off only by the pretraining.
    "trained_pretrained": {"trained": True, "structural": False, "pretrain": True},
    # -- Growth-matched controls (reviewer-requested ablations) ------------
    # Growth on, but recruits get random categories instead of inheriting the
    # anchor's: breaks label-guided category construction.
    "structural_random_category": {
        "trained": True,
        "structural": True,
        "overrides": ["structural.recruitment.category_guidance=false"],
    },
    # Fully unguided growth: random anchor unit, uniformly random inputs, random
    # categories. Matched to structural_on in recruited-neuron count and fan-in
    # statistics, but carries no reward/label information.
    "structural_unguided": {
        "trained": True,
        "structural": True,
        "overrides": [
            "structural.recruitment.anchor=random",
            "structural.recruitment.input_source=random",
            "structural.recruitment.category_guidance=false",
        ],
    },
    # No growth, but the decoder gets the same number of extra (random) feature
    # dimensions a growth-on condition adds, so a capacity-only explanation for
    # growth's gain is directly testable.
    "trained_extra_features": {
        "trained": True,
        "structural": False,
        "overrides": [
            "evaluation.extra_random_features=300",
            "evaluation.readout=linear",
        ],
    },
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

    # Every seed landed on the same value, so the sample has zero variance and
    # the t statistic is degenerate (0/0 when at chance, or an infinite ratio
    # otherwise, which scipy turns into p = 0). Neither is evidence: with zero
    # variance there is no distribution to infer from, so no significance can
    # be claimed regardless of the mean. p is reported as 1.0 and the report
    # explains the degenerate case instead of emitting a bare nan or a bogus
    # "significant".
    if float(values.std(ddof=1)) == 0.0:
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
            "variance; p is reported as 1.0 and no significance is claimed, "
            "even when the identical value differs from chance"
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
            "extra": self.extra,
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

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "RunResult":
        """Rebuild a result from its serialised form (see ``as_dict``).

        Used to resume a run from the ``seed_conditions.jsonl`` checkpoint:
        every completed (seed, condition) accuracy is replayed instead of being
        recomputed. Per-trial ``records`` are not part of the checkpoint, so the
        rebuilt result has an empty record list; the reported statistics do not
        depend on the raw records.
        """
        counts = np.asarray(payload["confusion_matrix"], dtype=np.int64)
        return cls(
            condition=str(payload["condition"]),
            seed=int(payload["seed"]),
            trained=bool(payload["trained"]),
            train_accuracy=float(payload["train_accuracy"]),
            test_accuracy=float(payload["test_accuracy"]),
            test_no_response_rate=float(payload["test_no_response_rate"]),
            confusion=ConfusionMatrix(
                labels=list(range(counts.shape[0])), counts=counts
            ),
            learning_curve=list(payload.get("learning_curve") or []),
            plasticity=dict(payload.get("plasticity") or {}),
            structural=payload.get("structural"),
            records=[],
            extra=dict(payload.get("extra") or {}),
        )


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
    if settings.get("pretrain"):
        overrides.append("learning.pretrain=true")
    # Condition-specific dotted overrides (e.g. growth-matched controls that
    # flip structural.recruitment flags or add decoder features).
    overrides.extend(settings.get("overrides", []))

    run_config = deepcopy(config.to_dict())
    from .config import Config

    result = Config(run_config, source=config.source)
    if overrides:
        result.apply_overrides(overrides)
    result.validate()
    return result, settings


def _random_feature_builder(runner, config, seed: int, condition: str):
    """Return a builder for the growth-matched extra decoder features.

    The extras are fixed, seeded random projections of the Kenyon-cell
    spike-count vector, matched in number to the recruited units a growth-on
    condition adds. They let a no-growth decoder use the same extra feature
    budget as growth, so a capacity-only explanation for growth's accuracy can
    be separated from growth itself. Returns ``None`` when disabled.
    """
    n_extra = int(config.get("evaluation.extra_random_features", 0))
    if n_extra <= 0:
        return None
    rng = make_rng(seed, stream=f"extra-random-features:{condition}")
    cache: dict[str, Any] = {"proj": None, "size": None}

    def build() -> np.ndarray:
        window = runner.last_window or {}
        kc = window.get("mushroom_body")
        if kc is None:
            return np.zeros(n_extra, dtype=np.float64)
        kc = np.asarray(kc, dtype=np.float64)
        if cache["proj"] is None or cache["size"] != kc.size:
            cache["proj"] = rng.standard_normal((n_extra, kc.size))
            cache["size"] = int(kc.size)
        return cache["proj"] @ kc

    return build


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
    input_count = len(subcircuit.roles[list(subcircuit.roles)[0]])
    mapping = make_mapping(run_config, seed=seed, n_inputs=input_count, dataset=dataset)
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

    extra_builder = _random_feature_builder(runner, run_config, seed, condition)

    def features_of(record) -> np.ndarray:
        base = np.asarray(record.spike_counts, dtype=np.float64)
        if extra_builder is None:
            return base
        return np.concatenate([base, extra_builder()])

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
    dec_window = int(run_config.get("evaluation.decoder_window", 200))
    order = make_rng(seed, stream="trial-order").permutation(len(train_items))

    pretrain_info: dict[str, Any] = {}
    if settings.get("trained") and bool(run_config.get("learning.pretrain", False)):
        from .learning import pretrain_readout

        pretrain_info = pretrain_readout(
            runner, train_items, labels_for_training, run_config
        )
        LOGGER.info(
            "[%s seed=%d] pretrained readout: %s", condition, seed, pretrain_info
        )

    train_vectors: list[Any] = []
    train_fit_labels: list[int] = []
    if settings.get("trained"):
        for step in range(n_trials):
            position = int(order[step % len(order)])
            record = runner.run_trial(
                train_items[position].word, labels_for_training[position], step
            )
            train_vectors.append(features_of(record))
            train_fit_labels.append(labels_for_training[position])
            # runner.run_trial snapshots per-stage rates during the stimulus
            # window; the live monitors are cleared for the rest period.
            structural.observe_trial(record.predicted, record.reward_type)
            structural.observe(runner.last_stimulus_rates)
            structural.maybe_step(step + 1)
            if (step + 1) % 200 == 0:
                LOGGER.info(
                    "[%s seed=%d] trial %d/%d rolling acc %.3f",
                    condition, seed, step + 1, n_trials,
                    runner.accuracy(max(0, step + 1 - window)),
                )
        train_accuracy = runner.accuracy()
        # Decoder-fit pass on the FINAL representation. Run after the training
        # loop so the fit features live in one fixed feature space even when
        # structural growth changed the readout size mid-training (otherwise the
        # last `decoder_window` training vectors have inhomogeneous lengths and
        # the decoder fit crashes). run_trial(learn=False) is deterministic for
        # a fixed word and weight state, so for conditions without growth these
        # vectors are identical to the tail of the training loop and the numbers
        # are unchanged.
        train_vectors = []
        train_fit_labels = []
        for exp in range(dec_window):
            position = int(order[exp % len(order)])
            rec = runner.run_trial(
                train_items[position].word,
                labels_for_training[position],
                200_000 + exp,
                learn=False,
            )
            train_vectors.append(features_of(rec))
            train_fit_labels.append(labels_for_training[position])
    else:
        train_accuracy = float("nan")
        # The untrained control has no training loop, but the supervised readout
        # still needs training-set readout vectors to fit its decoder; collect
        # them without any weight update so the comparison is decoder-for-
        # decoder across conditions.
        for index, item in enumerate(train_items):
            record = runner.run_trial(
                item.word, item.label, 100_000 + index, learn=False
            )
            train_vectors.append(features_of(record))
            train_fit_labels.append(item.label)
            structural.observe_trial(record.predicted, record.reward_type)

    # Held-out evaluation: words never presented during training.
    repeats = int(run_config.get("evaluation.eval_repeats"))
    true_labels: list[int] = []
    test_vectors: list[np.ndarray] = []
    eval_records = []
    for item in test_items:
        for _ in range(repeats):
            probe = runner.run_trial(
                item.word, item.label, 100_000 + len(eval_records), learn=False
            )
            true_labels.append(item.label)
            test_vectors.append(features_of(probe))
            eval_records.append(probe)

    readout_mode = str(run_config.get("evaluation.readout", "argmax"))
    if readout_mode == "linear" and train_vectors:
        from .learning import decoder_predict, fit_readout_decoder

        dec_window = int(run_config.get("evaluation.decoder_window", 200))
        fit_features = np.asarray(train_vectors[-dec_window:], dtype=np.float64)
        fit_labels = np.asarray(train_fit_labels[-dec_window:], dtype=np.int64)
        if len(np.unique(fit_labels)) >= 2:
            decoder = fit_readout_decoder(
                fit_features, fit_labels, len(dataset.categories), run_config
            )
            predictions = decoder_predict(
                np.asarray(test_vectors, dtype=np.float64), decoder
            ).tolist()
        else:
            predictions = [record.predicted for record in eval_records]
    else:
        predictions = [record.predicted for record in eval_records]

    held_out_accuracy = (
        sum(1 for t, p in zip(true_labels, predictions) if p == t) / len(true_labels)
        if true_labels else 0.0
    )

    # Online MBON-argmax readout on the held-out words: the quantity that tests
    # the plasticity rule itself, before the supervised decoder is layered on.
    # The decoder is a representation-quality probe, not the online learner.
    argmax_predictions = [record.predicted for record in eval_records]
    argmax_test_accuracy = (
        sum(1 for t, p in zip(true_labels, argmax_predictions) if p == t)
        / len(true_labels)
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
            "pretrain": pretrain_info,
            "argmax_test_accuracy": float(argmax_test_accuracy),
            "readout_mode": readout_mode,
        },
    )


def representation_probes(
    subcircuit,
    config,
    weight_scale: float,
    dataset: Dataset,
    split: Split,
    seed: int,
) -> dict[str, Any]:
    """Linear decodability of the category label from upstream representations.

    Positive-control ceiling. Fits the same supervised linear decoder the main
    readout uses on the per-trial spike counts of the input stage and of the
    Kenyon-cell (``mushroom_body``) stage, on a fixed untrained network. Neither
    stage is downstream of the plastic KC -> MBON set, so the numbers are a
    property of the stimulus + connectome, not of learning.

    The two rungs answer different questions. If the *input* probe is at chance
    the stimulus does not encode the label and nothing downstream can recover
    it (harness/encoding bug). If the input probe is high but the *Kenyon-cell*
    probe is at chance, the representational signal is lost before the plastic
    readout (upstream bottleneck). Only if both are above chance does a null at
    the output isolate the plastic readout / three-factor learning rule.
    """
    from .learning import decoder_predict, fit_readout_decoder
    from .network import build_network

    network = build_network(subcircuit, config, weight_scale=weight_scale)
    input_count = len(subcircuit.roles[list(subcircuit.roles)[0]])
    mapping = make_mapping(config, seed=seed, n_inputs=input_count, dataset=dataset)
    runner = TrialRunner(network, config, mapping, seed=seed)

    stage_names = list(network.stages)
    stage_for = {
        "input": stage_names[0],
        "mushroom_body": "mushroom_body" if "mushroom_body" in stage_names
        else (stage_names[-1] if stage_names else "mushroom_body"),
    }

    # Mirror the main readout's decoder protocol exactly so the probe is a fair
    # ceiling: fit on ``decoder_window`` cyclic presentations of the training
    # words (not one each), and read out ``eval_repeats`` presentations per
    # held-out word. A decoder fit on 60 samples over 384 Kenyon-cell features
    # memorises the training set and does not generalise, which understates the
    # representation; the cyclic budget matches the budget the plastic readout
    # is actually judged against.
    dec_window = int(config.get("evaluation.decoder_window", 200))
    repeats = int(config.get("evaluation.eval_repeats", 1))
    order = make_rng(seed, stream="probe-train-order").permutation(len(split.train))

    features: dict[str, list[np.ndarray]] = {key: [] for key in stage_for}
    train_labels: list[int] = []
    for exp in range(dec_window):
        item = split.train[int(order[exp % len(order)])]
        runner.run_trial(item.word, item.label, 400_000 + exp, learn=False)
        window = runner.last_window or {}
        for key, stage in stage_for.items():
            vec = window.get(stage)
            features[key].append(
                np.asarray(vec, dtype=np.float64)
                if vec is not None else np.zeros(0, dtype=np.float64)
            )
        train_labels.append(int(item.label))

    test_features: dict[str, list[np.ndarray]] = {key: [] for key in stage_for}
    test_labels: list[int] = []
    for rep in range(max(1, repeats)):
        for index, item in enumerate(split.test):
            runner.run_trial(item.word, item.label, 500_000 + rep * 1000 + index,
                             learn=False)
            window = runner.last_window or {}
            for key, stage in stage_for.items():
                vec = window.get(stage)
                test_features[key].append(
                    np.asarray(vec, dtype=np.float64)
                    if vec is not None else np.zeros(0, dtype=np.float64)
                )
            test_labels.append(int(item.label))

    n_classes = len(dataset.categories)
    train_y = np.asarray(train_labels, dtype=np.int64)
    test_y = np.asarray(test_labels, dtype=np.int64)
    cv_folds = max(2, int(config.get("evaluation.probes.cv_folds", 5)))
    result: dict[str, Any] = {}
    for key in stage_for:
        train_x = np.asarray(features[key], dtype=np.float64)
        test_x = np.asarray(test_features[key], dtype=np.float64)
        entry: dict[str, Any] = {
            "stage": stage_for[key],
            "n_features": int(train_x.shape[1]) if train_x.ndim == 2 else 0,
            "n_train_presentations": int(train_x.shape[0]) if train_x.ndim == 2 else 0,
            "cv_folds": cv_folds,
        }
        if train_x.ndim == 2 and train_x.size and len(np.unique(train_y)) >= 2:
            decoder = fit_readout_decoder(train_x, train_y, n_classes, config)
            entry["train_accuracy"] = float(
                (decoder_predict(train_x, decoder) == train_y).mean()
            )
            entry["test_accuracy"] = float(
                (decoder_predict(test_x, decoder) == test_y).mean()
            )
            # K-fold CV over the training presentations is the decodability
            # estimate the gate uses: a single fit on 384 Kenyon-cell features
            # can memorise the training set, so held-out test accuracy alone
            # understates (or overstates) what the representation carries.
            fold_ids = make_rng(seed, stream=f"probe-cv:{key}").integers(
                0, cv_folds, size=train_y.size
            )
            cv_pred = np.full(train_y.shape, -1, dtype=np.int64)
            for fold in range(cv_folds):
                hold = fold_ids == fold
                fit = ~hold
                if len(np.unique(train_y[fit])) < 2 or not hold.any():
                    continue
                fold_decoder = fit_readout_decoder(
                    train_x[fit], train_y[fit], n_classes, config
                )
                cv_pred[hold] = decoder_predict(train_x[hold], fold_decoder)
            covered = cv_pred >= 0
            entry["cv_accuracy"] = (
                float((cv_pred[covered] == train_y[covered]).mean())
                if covered.any() else float("nan")
            )
        else:
            entry["train_accuracy"] = float("nan")
            entry["test_accuracy"] = float("nan")
            entry["cv_accuracy"] = float("nan")
        result[key] = entry
    return result


@dataclass
class SeedResult:
    """All conditions for one seed."""

    seed: int
    results: dict[str, RunResult]
    weight_scale: float
    seconds: float
    probes: dict[str, Any] = field(default_factory=dict)


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
    probes: dict[str, Any] = field(default_factory=dict)
    positive_control: dict[str, Any] = field(default_factory=dict)

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
            "probes": self.probes,
            "positive_control": self.positive_control,
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


def _aggregate_probes(
    seed_results: Sequence[SeedResult], chance: float, config
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Mean/CI per probe stage across seeds, plus the positive-control gate.

    Gate definition (configurable): the harness is validated when the input
    probe reaches at least ``evaluation.probes.input_min`` AND the Kenyon-cell
    probe beats chance by ``evaluation.probes.kc_margin``. Failing the first
    means the stimulus does not encode the label (encoding/harness fault);
    failing the second means the MB representation carries no usable signal, so
    the bottleneck is upstream of the plastic readout. Only when the gate passes
    is a null at the output attributable to the readout/learning rule.
    """
    stages: dict[str, list[float]] = {}
    train_by_stage: dict[str, list[float]] = {}
    cv_by_stage: dict[str, list[float]] = {}
    stage_names: dict[str, str] = {}
    n_features_by_stage: dict[str, int] = {}
    for seed_result in seed_results:
        for key, entry in (seed_result.probes or {}).items():
            value = entry.get("test_accuracy")
            if value is None or (isinstance(value, float) and np.isnan(value)):
                continue
            stages.setdefault(key, []).append(float(value))
            train_value = entry.get("train_accuracy")
            if train_value is not None and not (
                isinstance(train_value, float) and np.isnan(train_value)
            ):
                train_by_stage.setdefault(key, []).append(float(train_value))
            cv_value = entry.get("cv_accuracy")
            if cv_value is not None and not (
                isinstance(cv_value, float) and np.isnan(cv_value)
            ):
                cv_by_stage.setdefault(key, []).append(float(cv_value))
            stage_names[key] = str(entry.get("stage", key))
            n_features_by_stage[key] = int(entry.get("n_features", 0))

    aggregated: dict[str, Any] = {}
    for key, values in stages.items():
        ci_low, ci_high = bootstrap_ci(
            values,
            level=float(config.get("evaluation.ci_level")),
            resamples=int(config.get("evaluation.ci_resamples")),
            seed=int((config.get("evaluation.base_seed") or 0)),
        )
        aggregated[key] = {
            "stage": stage_names.get(key, key),
            "n_features": n_features_by_stage.get(key, 0),
            "n_seeds": len(values),
            "mean_test_accuracy": float(np.mean(values)),
            "ci_low": ci_low,
            "ci_high": ci_high,
            "mean_train_accuracy": (
                float(np.mean(train_by_stage[key]))
                if train_by_stage.get(key) else None
            ),
            "mean_cv_accuracy": (
                float(np.mean(cv_by_stage[key]))
                if cv_by_stage.get(key) else None
            ),
            "per_seed_test_accuracy": values,
        }

    enabled = bool(config.get("evaluation.probes.enabled", False))
    input_min = float(config.get("evaluation.probes.input_min", 0.90))
    kc_margin = float(config.get("evaluation.probes.kc_margin", 0.05))
    gate: dict[str, Any] = {
        "enabled": enabled,
        "input_min": input_min,
        "kc_margin": kc_margin,
    }
    if not enabled or not aggregated:
        gate["passed"] = None
        gate["detail"] = "probes were not run (evaluation.probes.enabled is false)"
        return aggregated, gate

    input_acc = aggregated.get("input", {}).get("mean_test_accuracy")
    kc_acc = aggregated.get("mushroom_body", {}).get("mean_test_accuracy")
    gate["input_probe_accuracy"] = input_acc
    gate["kc_probe_accuracy"] = kc_acc
    gate["decision_metric"] = "held-out accuracy on unseen words"
    input_ok = input_acc is not None and input_acc >= input_min
    kc_ok = kc_acc is not None and kc_acc >= chance + kc_margin
    gate["input_ok"] = bool(input_ok)
    gate["kc_ok"] = bool(kc_ok)
    gate["passed"] = bool(input_ok and kc_ok)
    if not input_ok:
        gate["detail"] = (
            f"input probe {_fmt_pct(input_acc)} is below the {input_min:.2f} "
            "floor: the stimulus does not encode the label, so a null at the "
            "output is uninformative (encoding/harness fault)."
        )
    elif not kc_ok:
        gate["detail"] = (
            f"input probe passes but the Kenyon-cell probe "
            f"{_fmt_pct(kc_acc)} is not above chance+{kc_margin:.2f}: the label "
            "signal is lost before the plastic readout (upstream bottleneck)."
        )
    else:
        gate["detail"] = (
            f"input probe {_fmt_pct(input_acc)} and Kenyon-cell probe "
            f"{_fmt_pct(kc_acc)} both exceed their floors, so a null at the "
            "output isolates the plastic readout / learning rule."
        )
    return aggregated, gate


def _fmt_pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"


def aggregate(
    seed_results: Sequence[SeedResult],
    dataset: Dataset,
    config,
    metadata: dict[str, Any] | None = None,
) -> EvaluationReport:
    """Aggregate per-seed results into the reported statistics.

    A data-leakage warning is raised when the shuffled-label control beats
    the higher of chance and the untrained-network floor by more than the
    configured margin, since that would mean the evaluation pipeline itself
    leaks label information. Comparing against the untrained floor matters
    when the representation itself is decodable without any learning: an
    untrained network can sit well above chance, and the shuffled control
    should be no more decodable than that.
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
        # Online MBON-argmax held-out accuracy (pre-decoder). Absent in older
        # archived runs, so it is optional rather than an error.
        argmax_acc = [
            r.extra.get("argmax_test_accuracy")
            for sr in seed_results for r in [sr.results.get(condition)] if r
            and r.extra.get("argmax_test_accuracy") is not None
        ]
        stats["argmax_test_accuracy_mean"] = (
            float(np.mean(argmax_acc)) if argmax_acc else None
        )
        stats["n_seeds"] = len(accuracies)
        conditions[condition] = stats

    leakage_warning = None
    shuffled = per_seed.get("shuffled_labels")
    if shuffled:
        shuffled_mean = float(np.mean(shuffled))
        untrained = per_seed.get("untrained")
        floor = chance
        floor_label = f"{chance:.2f} chance level"
        if untrained:
            untrained_mean = float(np.mean(untrained))
            floor = max(chance, untrained_mean)
            floor_label = (
                f"{chance:.2f} chance level and the {untrained_mean:.3f} "
                f"untrained-network floor"
            )
        if shuffled_mean > floor + margin:
            leakage_warning = (
                f"shuffled-label control reached {shuffled_mean:.3f}, above the "
                f"{floor_label} by more than the configured margin "
                f"{margin:.2f}; the evaluation pipeline may leak label information"
            )
            LOGGER.warning("%s", leakage_warning)

    probes, positive_control = _aggregate_probes(seed_results, chance, config)
    if positive_control.get("passed") is False:
        LOGGER.warning("positive-control gate FAILED: %s", positive_control.get("detail"))

    return EvaluationReport(
        conditions=conditions,
        per_seed=per_seed,
        chance=chance,
        level=level,
        n_seeds=len(seed_results),
        leakage_warning=leakage_warning,
        splits=splits,
        metadata=metadata or {},
        probes=probes,
        positive_control=positive_control,
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


def pick_confusion_condition(
    seed_results: Sequence[SeedResult], conditions: Sequence[str]
) -> str:
    """Choose a condition to render the aggregate confusion matrix for.

    Prefers ``trained`` when it has results, otherwise the first requested
    condition that does. Single-condition isolation controls (e.g. the
    ``shuffled_labels_no_growth`` pass) do not include ``trained``, so the
    matrix must fall back rather than crash.
    """
    available = {name for result in seed_results for name in result.results}
    for candidate in ("trained", *conditions):
        if candidate in available:
            return candidate
    raise EvaluationError("no results for any condition")