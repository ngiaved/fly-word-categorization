"""Command line entry points.

Subcommands: ``fetch-data``, ``inspect``, ``calibrate``, ``benchmark``,
``train``, ``evaluate``, ``run``, ``selftest``.

Every subcommand writes a run manifest. ``run`` is the documented single
command that reproduces the report from a clean checkout.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Sequence

from .config import Config, ConfigError
from .repro import (
    RunManifest,
    file_digest,
    hardware_summary,
    make_rng,
    new_run_id,
    seed_everything,
)

LOGGER = logging.getLogger("flyread")


# --------------------------------------------------------------------------
# Data acquisition
# --------------------------------------------------------------------------

# Real FlyWire release 783 artifacts. Annotations come from the public
# flywire_annotations repository; connectivity comes from the Zenodo record.
# FlyWire data is CC-BY-NC 4.0 and is never committed to this repository.
DATA_SOURCES = {
    "annotations": {
        "url": "https://raw.githubusercontent.com/flyconnectome/"
               "flywire_annotations/main/supplemental_files/"
               "Supplemental_file1_neuron_annotations.tsv",
        "filename": "Supplemental_file1_neuron_annotations.tsv",
        "bytes": 31_720_298,
    },
    "connections": {
        "url": "https://zenodo.org/records/10676866/files/"
               "proofread_connections_783.feather?download=1",
        "filename": "proofread_connections_783.feather",
        "bytes": 852_022_274,
    },
}


def cmd_fetch_data(args, config: Config) -> int:
    """Download the pinned FlyWire files and verify their checksums."""
    import urllib.request

    from .connectome import verify_files

    target = Path(args.dir or config.get("data.flywire_dir"))
    target.mkdir(parents=True, exist_ok=True)

    for name, source in DATA_SOURCES.items():
        destination = target / source["filename"]
        if destination.exists() and destination.stat().st_size == source["bytes"]:
            print(f"{name}: already present ({destination.stat().st_size} bytes)")
            continue
        print(f"{name}: downloading {source['bytes'] / 1e6:.0f} MB -> {destination}")
        print(f"  {source['url']}")
        with urllib.request.urlopen(source["url"]) as response, open(
            destination.with_suffix(destination.suffix + ".partial"), "wb"
        ) as handle:
            total = 0
            while chunk := response.read(1 << 20):
                handle.write(chunk)
                total += len(chunk)
                print(f"\r  {total / 1e6:.0f} MB", end="", flush=True)
        print()
        destination.with_suffix(destination.suffix + ".partial").replace(destination)

    config_overrides = []
    for name in DATA_SOURCES:
        destination = target / DATA_SOURCES[name]["filename"]
        digest = file_digest(destination, "md5")
        LOGGER.info("%s md5=%s", name, digest)

    print("verifying checksums against configuration")
    digests = verify_files(config)
    for name, digest in digests.items():
        print(f"  {name}: {digest} ok")
    return 0


# --------------------------------------------------------------------------
# Shared setup
# --------------------------------------------------------------------------

def _prepare(config: Config, args, run_id: str | None = None):
    """Seed, load the dataset, verify data, and extract the subcircuit."""
    from .connectome import extract_subcircuit, validate_schema, verify_files
    from .encoding import (
        apply_label_mode,
        ink_density_map,
        load_dataset,
        make_mapping,
        verify_font,
    )

    seed = int(args.seed if args.seed is not None else config.get("evaluation.base_seed"))
    seed_record = seed_everything(seed)

    manifest = RunManifest(
        run_id=run_id or new_run_id(),
        seed=seed,
        config=config.to_dict(),
        seed_record=seed_record,
    )

    schema = validate_schema(config)
    manifest.data["schema"] = schema
    digests = verify_files(config, manifest=manifest)

    dataset = apply_label_mode(
        load_dataset(config.get("data.words_csv"), config.get("encoding.categories")),
        config,
        str(config.get("encoding.label_mode")),
    )
    manifest.data["dataset"] = dataset.summary()
    manifest.data["label_mode"] = str(config.get("encoding.label_mode"))
    if str(config.get("encoding.label_mode")) == "ink_quartile":
        # Record the exact ink value at every label boundary so the learning
        # target is auditable from the manifest alone.
        manifest.data["ink_density"] = {
            word: round(value, 6)
            for word, value in sorted(
                ink_density_map(dataset, config).items(),
                key=lambda item: (item[1], item[0]),
            )
        }
    manifest.data["flywire_release"] = config.get("data.release")
    manifest.data["checksums"] = digests
    manifest.encoding["font"] = verify_font(config)
    # The mapping scheme id (and, for the shuffled-pixel control, the
    # permutation seed) must be in every run manifest per the visual-encoding
    # spec, because the encoding is what defines the input to the network.
    manifest.encoding["mapping"] = make_mapping(config, seed=seed).describe()
    manifest.encoding["timing"] = {
        "stimulus_ms": float(config.get("encoding.stimulus_ms")),
        "rest_ms": float(config.get("encoding.rest_ms")),
        "dt_ms": float(config.get("simulation.dt")),
    }
    manifest.encoding["rate_to_current"] = (
        "derived by inverting the LIF rate-current relation; no fixed nA/Hz gain"
    )
    manifest.encoding["lif_drive"] = {
        "tau_ms": float(config.get("network.lif.tau_ms")),
        "v_threshold": float(config.get("network.lif.v_threshold")),
        "v_rest": float(config.get("network.lif.v_rest")),
    }
    manifest.encoding["baseline_hz"] = float(config.get("encoding.baseline_hz"))
    manifest.encoding["max_hz"] = float(config.get("encoding.max_hz"))

    subcircuit = extract_subcircuit(config, manifest=manifest)
    manifest.subcircuit = subcircuit.summary()

    from .repro import file_digest as _digest

    manifest.data["words_csv_sha256"] = _digest(config.get("data.words_csv"), "sha256")
    return manifest, dataset, subcircuit, seed


def _run_dir(config: Config, manifest: RunManifest) -> Path:
    base = Path(config.get("run.output_dir")) / manifest.run_id
    base.mkdir(parents=True, exist_ok=True)
    return base


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------

def cmd_inspect(args, config: Config) -> int:
    """Print the real schema and the subcircuit that extraction selects."""
    from .connectome import connection_columns, connection_row_count, validate_schema

    schema = validate_schema(config)
    print("annotation columns:")
    for name in schema["annotations"]:
        print(f"  {name}")
    print("\nconnection columns:")
    for name in schema["connections"]:
        print(f"  {name}")
    rows = connection_row_count(config.get("connectome.connections_file"))
    print(f"\nconnection rows: {rows:,}")
    print(f"flywire release: {config.get('data.release')}")
    print(f"annotations file: {config.get('connectome.annotations_file')}")
    print(f"connections file: {config.get('connectome.connections_file')}")
    print(f"annotations md5: {config.get('connectome.annotations_md5')}")
    print(f"connections md5: {config.get('connectome.connections_md5')}")

    if args.subcircuit:
        manifest, _dataset, subcircuit, _seed = _prepare(config, args)
        print("\nsubcircuit:")
        print(json.dumps(subcircuit.summary(), indent=2, default=str))
        manifest.write(_run_dir(config, manifest))
    return 0


def cmd_calibrate(args, config: Config) -> int:
    """Run the no-stimulus weight-scale calibration."""
    import brian2 as b2

    from .network import calibrate

    manifest, _dataset, subcircuit, _seed = _prepare(config, args)
    run_dir = _run_dir(config, manifest)

    network, result = calibrate(subcircuit, config, manifest=manifest)
    manifest.calibration = result.as_dict()
    manifest.subcircuit["network"] = network.summary()
    manifest.write(run_dir)
    print(json.dumps(result.as_dict(), indent=2, default=str))
    _reset_brian()
    return 0


def cmd_benchmark(args, config: Config) -> int:
    """Run the CPU feasibility gate (Task 0)."""
    import brian2 as b2

    from .benchmark import run_gate
    from .network import calibrate

    manifest, _dataset, subcircuit, _seed = _prepare(config, args)
    run_dir = _run_dir(config, manifest)

    _network, calibration = calibrate(subcircuit, config, manifest=manifest)
    manifest.calibration = calibration.as_dict()

    gate = run_gate(subcircuit, config, weight_scale=calibration.weight_scale)
    manifest.benchmark = gate
    manifest.write(run_dir)
    print(json.dumps(gate, indent=2, default=str))

    if gate["verdict"] == "fail":
        print(
            "\nGATE FAILED: the planned trial budget does not fit the configured "
            "wall-clock limit, or memory is over budget.\nPer tasks.md Task 0.6, "
            "STOP and revise scope rather than relaxing the spec."
        )
    _reset_brian()
    return 0 if gate["verdict"] == "pass" else 2


def cmd_train(args, config: Config) -> int:
    """Train one seed with one condition (default: the main condition)."""
    import brian2 as b2

    from .evaluate import make_split, run_condition
    from .network import calibrate

    manifest, dataset, subcircuit, seed = _prepare(config, args)
    run_dir = _run_dir(config, manifest)

    _network, calibration = calibrate(subcircuit, config, manifest=manifest)
    manifest.calibration = calibration.as_dict()
    run_config = calibration.apply_drive(config)

    split = make_split(
        dataset,
        int(config.get("evaluation.train_per_category")),
        int(config.get("evaluation.test_per_category")),
        seed=seed,
    )
    condition = args.condition
    result = run_condition(
        condition, dataset, split, seed, run_config,
        calibration.weight_scale, subcircuit, manifest=manifest,
    )
    manifest.metrics = {"condition": condition, **result.as_dict(include_records=True)}
    manifest.write(run_dir)
    print(json.dumps(result.as_dict(), indent=2, default=str))
    _reset_brian()
    return 0


def cmd_evaluate(args, config: Config) -> int:
    """Run every condition over every seed and write the report."""
    import brian2 as b2

    from .evaluate import (
        aggregate,
        confusion_matrix_total,
        make_split,
        run_condition,
    )
    from .network import calibrate
    from .report import (
        build_markdown,
        plot_confusion,
        plot_learning_curves,
        write_json,
        write_markdown,
    )

    manifest, dataset, subcircuit, seed = _prepare(config, args)
    run_dir = _run_dir(config, manifest)

    _network, calibration = calibrate(subcircuit, config, manifest=manifest)
    manifest.calibration = calibration.as_dict()
    weight_scale = calibration.weight_scale
    run_config = calibration.apply_drive(config)

    conditions = list(config.get("evaluation.conditions"))
    n_seeds = int(getattr(args, "n_seeds", None) or config.get("evaluation.n_seeds"))
    if n_seeds < 10:
        manifest.warn(
            f"evaluation.n_seeds = {n_seeds} is below the spec minimum of 10; "
            "the report will state this reduced power explicitly"
        )
    base_seed = int(config.get("evaluation.base_seed"))
    seeds = [base_seed + offset for offset in range(n_seeds)]

    started = time.perf_counter()
    all_results = []
    # Checkpoint as conditions finish so a killed run still leaves every
    # completed (seed, condition) accuracy on disk, and the live log shows
    # numbers instead of only "starting condition X" lines.
    checkpoint = run_dir / "seed_conditions.jsonl"
    for index, run_seed in enumerate(seeds, start=1):
        seed_everything(run_seed)
        split = make_split(
            dataset,
            int(config.get("evaluation.train_per_category")),
            int(config.get("evaluation.test_per_category")),
            seed=run_seed,
        )
        per_condition = {}
        for condition in conditions:
            LOGGER.info("[%d/%d] seed %d condition %s", index, n_seeds, run_seed, condition)
            per_condition[condition] = run_condition(
                condition, dataset, split, run_seed, run_config, weight_scale,
                subcircuit, manifest=manifest,
            )
            finished = per_condition[condition]
            LOGGER.info(
                "[%d/%d] seed %d condition %-14s test acc %.3f "
                "(no-response %.3f)",
                index, n_seeds, run_seed, condition,
                finished.test_accuracy, finished.test_no_response_rate,
            )
            with checkpoint.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps(
                        {"seed": run_seed, **finished.as_dict(include_records=False)},
                        sort_keys=True,
                    )
                    + "\n"
                )
        from .evaluate import SeedResult

        all_results.append(
            SeedResult(
                seed=run_seed, results=per_condition, weight_scale=weight_scale,
                seconds=time.perf_counter() - started,
            )
        )
    total_seconds = time.perf_counter() - started

    import brian2

    report = aggregate(
        all_results,
        dataset,
        config,
        metadata={
            "weight_scale": weight_scale,
            "n_train_trials": int(config.get("evaluation.n_train_trials")),
            "codegen_target": str(brian2.prefs.codegen.target),
            "total_seconds": total_seconds,
            "base_seed": base_seed,
            "seeds": seeds,
        },
    )

    confusion = confusion_matrix_total(
        all_results, "trained", len(dataset.categories)
    )
    manifest.metrics = {
        "conditions": report.conditions,
        "per_seed": report.per_seed,
        "data_leakage_warning": report.leakage_warning,
        "confusion_matrix_total": confusion.as_list(),
        "total_seconds": total_seconds,
    }
    manifest.write(run_dir)

    write_json(report, all_results, run_dir / "results.json")
    plot_learning_curves(
        all_results, conditions, run_dir / "learning_curves.png"
    )
    plot_confusion(confusion, dataset.categories, run_dir / "confusion_matrix.png")
    write_markdown(
        build_markdown(
            report, all_results, dataset.categories, manifest.subcircuit,
            confusion=confusion,
        ),
        run_dir / "report.md",
    )
    print(f"\nwrote run outputs to {run_dir}")
    from .report import headline

    print()
    print(headline(report))
    _reset_brian()
    return 0


def cmd_run(args, config: Config) -> int:
    """The documented reproduction path: gate then full evaluation."""
    print("== step 1/2: CPU feasibility gate ==")
    code = cmd_benchmark(args, config)
    if code != 0:
        print("\nGate failed. Stopping before the experiment, per tasks.md Task 0.6.")
        return code
    print("\n== step 2/2: full evaluation ==")
    return cmd_evaluate(args, config)


def cmd_selftest(args, config: Config) -> int:
    """Fast end-to-end check on a tiny subcircuit, for smoke testing."""
    import brian2 as b2

    from .evaluate import aggregate, make_split, run_condition
    from .evaluate import SeedResult
    from .network import build_network, measure_spontaneous_activity

    manifest, dataset, subcircuit, seed = _prepare(config, args)
    run_dir = _run_dir(config, manifest)

    scales = [float(x) for x in config.sequence("calibration.scale_grid")]
    scale = scales[len(scales) // 2]
    network = build_network(subcircuit, config, weight_scale=scale)
    stats = measure_spontaneous_activity(network, config)
    manifest.calibration = {
        "weight_scale": scale, "accepted": None,
        "note": "selftest uses a mid-grid scale without searching",
        "measured": stats,
    }
    manifest.subcircuit["network"] = network.summary()

    split = make_split(
        dataset,
        int(config.get("evaluation.train_per_category")),
        int(config.get("evaluation.test_per_category")),
        seed=seed,
    )
    results = {}
    for condition in ("trained", "structural_on", "structural_off"):
        results[condition] = run_condition(
            condition, dataset, split, seed, config, scale, subcircuit,
            manifest=manifest,
        )
    seed_result = SeedResult(seed=seed, results=results, weight_scale=scale,
                             seconds=0.0)
    report = aggregate(
        [seed_result], dataset, config,
        metadata={"weight_scale": scale, "note": "selftest, single seed"},
    )
    manifest.metrics = {"selftest": True, "conditions": report.conditions}
    manifest.write(run_dir)
    print(json.dumps({"calibration": stats, "network": network.summary(),
                      "conditions": report.conditions}, indent=2, default=str))
    _reset_brian()
    return 0


# --------------------------------------------------------------------------
# Argument parsing
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="flyread",
        description="FlyWire connectome subcircuit + dopamine learning for 4-letter words",
    )
    parser.add_argument("--config", help="path to a YAML config file")
    parser.add_argument(
        "--set", dest="overrides", action="append", default=[],
        metavar="KEY=VALUE", help="override a config value, e.g. evaluation.n_seeds=10",
    )
    parser.add_argument("--seed", type=int, help="override the run seed")
    parser.add_argument(
        "--log-level", default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fetch = subparsers.add_parser("fetch-data", help="download the pinned FlyWire files")
    fetch.add_argument("--dir", help="destination directory")

    inspect_parser = subparsers.add_parser(
        "inspect", help="print real schemas and optionally the selected subcircuit"
    )
    inspect_parser.add_argument(
        "--subcircuit", action="store_true", help="also extract and print the subcircuit"
    )

    subparsers.add_parser("calibrate", help="no-stimulus weight-scale calibration")
    subparsers.add_parser("benchmark", help="CPU feasibility gate")
    subparsers.add_parser("evaluate", help="all conditions x seeds, plus the report")

    train = subparsers.add_parser("train", help="train a single seed")
    train.add_argument("--condition", default="trained", help="condition name")

    run = subparsers.add_parser("run", help="gate then full evaluation")
    run.add_argument("--dir", help=argparse.SUPPRESS)
    run.add_argument("--condition", default="trained", help=argparse.SUPPRESS)
    run.add_argument("--subcircuit", action="store_true", help=argparse.SUPPRESS)
    run.add_argument("--n-seeds", type=int, help="override the number of seeds")

    selftest = subparsers.add_parser("selftest", help="fast end-to-end smoke check")
    selftest.add_argument("--dir", help=argparse.SUPPRESS)
    selftest.add_argument("--condition", default="trained", help=argparse.SUPPRESS)
    selftest.add_argument("--subcircuit", action="store_true", help=argparse.SUPPRESS)
    selftest.add_argument("--n-seeds", type=int, help=argparse.SUPPRESS)

    return parser


def _reset_brian() -> None:
    """Drop Brian2 magic-network state between runs."""
    import brian2 as b2

    b2.stop()


COMMANDS = {
    "fetch-data": cmd_fetch_data,
    "inspect": cmd_inspect,
    "calibrate": cmd_calibrate,
    "benchmark": cmd_benchmark,
    "train": cmd_train,
    "evaluate": cmd_evaluate,
    "run": cmd_run,
    "selftest": cmd_selftest,
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    level = args.log_level
    logging.basicConfig(
        level=getattr(logging, level) if level else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Brian2 is chatty at INFO; keep it at WARNING unless debugging.
    logging.getLogger("brian2").setLevel(
        getattr(logging, args.log_level) if args.log_level == "DEBUG" else logging.WARNING
    )

    try:
        if args.command == "fetch-data":
            config = Config.load(args.config, overrides=args.overrides)
        else:
            config = Config.load(args.config, overrides=args.overrides)
        config.validate()
    except ConfigError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        return 3

    handler = COMMANDS[args.command]
    try:
        return handler(args, config)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except Exception as error:  # surfaced clearly rather than as a traceback wall
        LOGGER.error("%s failed: %s", args.command, error, exc_info=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())