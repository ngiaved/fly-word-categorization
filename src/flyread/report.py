"""Report rendering: tables, learning curves, confusion matrices.

Writes machine-readable JSON plus a Markdown report and PNG figures. The
Markdown report states a null result as plainly as a positive one.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .evaluate import ConfusionMatrix, EvaluationReport, SeedResult

LOGGER = logging.getLogger(__name__)

CONDITION_LABELS: dict[str, str] = {
    "untrained": "untrained network",
    "trained": "trained (main result)",
    "shuffled_labels": "shuffled training labels",
    "dopamine_off": "dopamine off",
    "shuffled_pixels": "shuffled pixels",
    "structural_on": "structural plasticity on",
    "structural_off": "structural plasticity off",
}


def _fmt(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float) and np.isnan(value):
        return "n/a"
    return f"{value:.{digits}f}"


def conditions_table(report: EvaluationReport) -> str:
    """Markdown table of mean accuracy and CI per condition."""
    lines = [
        "| condition | mean acc | 95% CI | p vs chance | significant | "
        "no-response | train acc |",
        "|---|---|---|---|---|---|---|",
    ]
    for condition, stats in report.conditions.items():
        label = CONDITION_LABELS.get(condition, condition)
        ci = (
            f"[{_fmt(stats.get('ci_low'))}, {_fmt(stats.get('ci_high'))}]"
            if "ci_low" in stats else "n/a"
        )
        p_value = stats.get("p_value")
        lines.append(
            f"| {label} | {_fmt(stats.get('mean_accuracy'))} | {ci} | "
            f"{_fmt(p_value, 4) if p_value is not None else 'n/a'} | "
            f"{'yes' if stats.get('significant') else 'no'} | "
            f"{_fmt(stats.get('no_response_rate_mean'))} | "
            f"{_fmt(stats.get('train_accuracy_mean'))} |"
        )
    return "\n".join(lines)


def headline(report: EvaluationReport, main_condition: str = "trained") -> str:
    """One paragraph stating the outcome, including null results."""
    stats = report.conditions.get(main_condition)
    if stats is None:
        return "No main-condition result was produced."
    if "error" in stats:
        return (
            f"The main condition could not be evaluated: {stats['error']}. "
            "This is reported as-is."
        )
    mean = stats.get("mean_accuracy", float("nan"))
    ci_low = stats.get("ci_low", float("nan"))
    ci_high = stats.get("ci_high", float("nan"))
    p_value = stats.get("p_value", float("nan"))
    level = report.level
    significant = bool(stats.get("significant"))

    verdict = (
        "significantly above the 25% chance level"
        if significant
        else "NOT significantly above the 25% chance level"
    )
    text = (
        f"Across {report.n_seeds} seeds, held-out accuracy for the main condition "
        f"was {mean:.3f} (95% CI [{ci_low:.3f}, {ci_high:.3f}]), which is "
        f"{verdict} at alpha = {level} "
        f"(one-sample t-test p = {p_value:.4g}; "
        f"Wilcoxon signed-rank p = {_fmt(stats.get('wilcoxon_p_value'), 4)})."
    )
    if not significant:
        text += (
            " This is a negative or null result and is reported without "
            "reframing: the subcircuit as configured did not learn the task "
            "better than chance."
        )
    return text


def structural_growth_section(
    seed_results: Sequence[SeedResult], manifest_summary: dict[str, Any]
) -> str:
    """Markdown section reporting final neuron/synapse counts after growth.

    Aggregates every condition that actually recruited (i.e. performed
    structural growth) across seeds. Returns an empty string when no run grew,
    so reports from non-growth configs are unchanged. ``final neurons`` and
    ``final synapses`` add the base subcircuit counts in ``manifest_summary``
    to the activated reserve neurons and the net reserve synapses.
    """
    base_neurons = manifest_summary.get("n_neurons")
    base_edges = manifest_summary.get("n_edges")
    rows: list[dict[str, Any]] = []
    notes: set[str] = set()
    for seed_result in seed_results:
        for condition, result in seed_result.results.items():
            summary = result.structural
            if not summary:
                continue
            stats = summary.get("stats", {}) or {}
            recruited = int(stats.get("recruits", 0))
            if recruited <= 0:
                continue
            reserve = summary.get("reserve", {}) or {}
            events = summary.get("events_by_kind", {}) or {}
            created = int(reserve.get("expansion_synapses", 0))
            removed = int(
                stats.get("expansion_pruned_total", stats.get("pruned_total", 0))
            )
            remaining = created - removed
            rows.append(
                {
                    "seed": seed_result.seed,
                    "condition": condition,
                    "pool_size": reserve.get("size"),
                    "available": reserve.get("available"),
                    "recruited": recruited,
                    "created": created,
                    "removed": removed,
                    "retired": int(stats.get("retired_total", 0)),
                    "remaining": remaining,
                    "final_neurons": (
                        base_neurons + recruited
                        if isinstance(base_neurons, int)
                        else None
                    ),
                    "final_edges": (
                        base_edges + remaining
                        if isinstance(base_edges, int)
                        else None
                    ),
                    "recruit_events": int(events.get("recruit", recruited)),
                    "prune_events": int(events.get("prune", removed)),
                    "silence_events": int(events.get("silence", 0)),
                }
            )
            note = summary.get("note")
            if note:
                notes.add(str(note))

    if not rows:
        return ""

    lines = [
        "## Structural growth",
        "",
        "Reserve neurons are born silent and unconnected. Growth activates them "
        "and draws new synapses from the real upstream population; deletions "
        "retire the weakest mature reserve synapses so they track a fixed "
        "fraction of the additions (`structural.prune_per_recruit`). "
        "`final neurons` and `final synapses` are the base subcircuit counts "
        "plus the activated reserve neurons and the net new synapses.",
        "",
        "| seed | condition | reserve pool | activated | created syn | "
        "retired syn | net syn | final neurons | final synapses |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        label = CONDITION_LABELS.get(row["condition"], row["condition"])
        pool = row["pool_size"]
        available = row["available"]
        pool_text = (
            f"{pool} ({available} available)"
            if isinstance(pool, int) and isinstance(available, int)
            else "n/a"
        )
        lines.append(
            f"| {row['seed']} | {label} | {pool_text} | {row['recruited']} | "
            f"{row['created']} | {row['retired']} | {row['remaining']} | "
            f"{_fmt_int(row['final_neurons'])} | {_fmt_int(row['final_edges'])} |"
        )
    lines.append("")
    lines.append("Structural events per seed (recruit / prune / silence):")
    lines.append("")
    for row in rows:
        label = CONDITION_LABELS.get(row["condition"], row["condition"])
        lines.append(
            f"- seed {row['seed']} ({label}): {row['recruit_events']} / "
            f"{row['prune_events']} / {row['silence_events']}"
        )
    lines.append("")
    for note in sorted(notes):
        lines.append(f"> Recruitment rule: {note}")
        lines.append("")
    return "\n".join(lines)


def _fmt_int(value: int | None) -> str:
    return str(value) if value is not None else "n/a"


def build_markdown(
    report: EvaluationReport,
    seed_results: Sequence[SeedResult],
    categories: Sequence[str],
    manifest_summary: dict[str, Any],
    confusion: ConfusionMatrix | None = None,
) -> str:
    """Full Markdown report."""
    meta = report.metadata
    lines: list[str] = [
        "# FlyWord results",
        "",
        "Simulation of a Drosophila connectome subcircuit (FlyWire release "
        f"{manifest_summary.get('flywire_release', '783')}) learning to classify "
        "4-letter English words into four categories with dopamine-gated "
        "plasticity.",
        "",
        "## Result",
        "",
        headline(report),
        "",
    ]
    if report.leakage_warning:
        lines += [
            "## Data-leakage warning",
            "",
            report.leakage_warning,
            "",
        ]

    lines += [
        "## Conditions",
        "",
        conditions_table(report),
        "",
        f"Chance level {report.chance:.2f}; significance level "
        f"alpha = {report.level}; {report.n_seeds} seeds; "
        "confidence intervals are percentile bootstrap over seeds.",
        "",
        "## Confusion matrix",
        "",
    ]
    if confusion is not None:
        lines += ["```", confusion.to_text(categories), "```", ""]
        recall = confusion.per_class_recall()
        lines.append("Per-category recall:")
        lines.append("")
        for label, value in sorted(recall.items()):
            name = categories[label] if label < len(categories) else str(label)
            lines.append(f"- {name}: {_fmt(value)}")
        lines.append("")

    lines += [
        "## Setup",
        "",
        f"- subcircuit: {manifest_summary.get('n_neurons', '?')} neurons, "
        f"{manifest_summary.get('n_edges', '?')} simulated synapses",
        f"- calibrated weight scale: {meta.get('weight_scale', '?')}",
        f"- train trials per seed: {meta.get('n_train_trials', '?')}",
        f"- wall-clock for the sweep: {_fmt(meta.get('total_seconds'), 1)} s",
        f"- codegen target: {meta.get('codegen_target', '?')}",
        "",
    ]
    growth = structural_growth_section(seed_results, manifest_summary)
    if growth:
        lines += [growth, ""]
    lines += [
        "## Per-seed held-out accuracy",
        "",
    ]
    header = "| seed | " + " | ".join(report.per_seed) + " |"
    lines += [header, "|" + "---|" * (len(report.per_seed) + 1)]
    seeds = sorted({sr.seed for sr in seed_results})
    for index, seed in enumerate(seeds):
        row = [str(seed)]
        for condition in report.per_seed:
            values = report.per_seed[condition]
            row.append(_fmt(values[index]) if index < len(values) else "n/a")
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines += [
        "## Reproducing",
        "",
        "```bash",
        "python -m pip install -r requirements.lock.txt",
        "python -m pip install -e .",
        "python -m flyread fetch-data",
        "python -m flyread run",
        "```",
        "",
        "Every run writes a manifest with the configuration, seed, data "
        "checksums, subcircuit rules, mapping scheme, dependency versions, "
        "codegen target, hardware summary, and git commit.",
        "",
    ]
    return "\n".join(lines)


def write_json(report: EvaluationReport, seed_results: Sequence[SeedResult], path) -> Path:
    """Machine-readable results, including every per-trial record."""
    payload = report.as_dict()
    payload["runs"] = {
        f"{sr.seed}": {c: r.as_dict(include_records=True) for c, r in sr.results.items()}
        for sr in seed_results
    }
    payload["seed_seconds"] = {str(sr.seed): sr.seconds for sr in seed_results}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    LOGGER.info("wrote results JSON to %s", target)
    return target


def plot_learning_curves(
    seed_results: Sequence[SeedResult], conditions: Sequence[str], path
) -> Path | None:
    """Mean accuracy per trial window, per condition, across seeds."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # pragma: no cover
        LOGGER.warning("matplotlib unavailable; skipping learning-curve plot")
        return None

    figure, axis = plt.subplots(figsize=(8, 5))
    plotted = False
    for condition in conditions:
        curves = []
        for seed_result in seed_results:
            result = seed_result.results.get(condition)
            if result is None or not result.learning_curve:
                continue
            curves.append([p["accuracy"] for p in result.learning_curve])
        if not curves:
            continue
        length = min(len(c) for c in curves)
        if length == 0:
            continue
        stacked = np.vstack([c[:length] for c in curves])
        mean = stacked.mean(axis=0)
        x = np.arange(length)
        axis.plot(x, mean, label=CONDITION_LABELS.get(condition, condition))
        plotted = True

    if not plotted:
        plt.close(figure)
        return None

    axis.axhline(0.25, color="grey", linestyle="--", linewidth=1, label="chance (0.25)")
    axis.set_xlabel("trial window")
    axis.set_ylabel("accuracy")
    axis.set_ylim(-0.02, 1.02)
    axis.set_title("Held-out-format learning curves (mean over seeds)")
    axis.legend(fontsize="small")
    figure.tight_layout()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target, dpi=150)
    plt.close(figure)
    LOGGER.info("wrote learning curves to %s", target)
    return target


def plot_confusion(matrix: ConfusionMatrix, categories: Sequence[str], path) -> Path | None:
    """Confusion matrix heatmap."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:  # pragma: no cover
        return None

    counts = matrix.counts.astype(float)
    normalized = counts / np.clip(counts.sum(axis=1, keepdims=True), 1, None)
    figure, axis = plt.subplots(figsize=(5.5, 5))
    image = axis.imshow(normalized, cmap="Blues", vmin=0.0, vmax=1.0)
    names = [categories[i] if i < len(categories) else str(i) for i in matrix.labels]
    axis.set_xticks(range(len(names)), names, rotation=45, ha="right")
    axis.set_yticks(range(len(names)), names)
    axis.set_xlabel("predicted")
    axis.set_ylabel("true")
    axis.set_title("Held-out confusion matrix (row-normalised)")
    for i in range(counts.shape[0]):
        for j in range(counts.shape[1]):
            axis.text(j, i, int(counts[i, j]), ha="center", va="center", fontsize=9)
    figure.colorbar(image, ax=axis, label="row-normalised rate")
    figure.tight_layout()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(target, dpi=150)
    plt.close(figure)
    LOGGER.info("wrote confusion matrix to %s", target)
    return target


def write_markdown(text: str, path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    LOGGER.info("wrote report to %s", target)
    return target