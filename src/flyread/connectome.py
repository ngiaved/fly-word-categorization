"""Connectome loading, validation, and subcircuit extraction.

Operates on the real FlyWire release-783 files:

* ``Supplemental_file1_neuron_annotations.tsv`` - 139,248 annotated neurons.
  Columns read here: ``root_id, flow, super_class, cell_class,
  cell_sub_class, cell_type, supertype, top_nt, side, status``.
* ``proofread_connections_783.feather`` - one row per connected neuron pair.

Column names are validated, never assumed: a missing required column raises
``SchemaError`` naming the column and the expected schema. Loading makes a
single streaming pass over the connectivity file, keeping only edges whose
both endpoints are annotation-rule candidates for some role, so peak memory
stays bounded well below the file size.
"""

from __future__ import annotations

import csv
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

import numpy as np

from .repro import verify_checksum

LOGGER = logging.getLogger(__name__)

ROLE_PHOTORECEPTOR = "photoreceptor"
ROLE_MUSHROOM_BODY = "mushroom_body"
ROLE_OUTPUT = "output"

# Stage names treated as the visual input layer.
INPUT_STAGES: tuple[str, ...] = (ROLE_PHOTORECEPTOR,)
# Stage names treated as the mushroom body.
MUSHROOM_BODY_STAGES: tuple[str, ...] = ("mushroom_body",)
# Stage names read out as categories.
OUTPUT_STAGES: tuple[str, ...] = (ROLE_OUTPUT,)
# Stage names carrying the teaching/reinforcement signal. In release 783 the
# MBIN class holds 2x APL (GABA) and 2x DPM (dopamine) neurons, which make
# real synapses onto Kenyon cells and are the natural dopamine source.
TEACHER_STAGES: tuple[str, ...] = ("reinforcement",)

# Annotation fields this module can filter on, per _matches().
ANNOTATION_COLUMNS: tuple[str, ...] = (
    "root_id", "flow", "super_class", "cell_class",
    "cell_sub_class", "cell_type", "supertype", "top_nt", "side", "status",
)

csv.field_size_limit(min(sys.maxsize, 2**31 - 1))


class SchemaError(ValueError):
    """Raised when a data file does not match the required schema."""


@dataclass(frozen=True)
class Neuron:
    """One annotated FlyWire neuron, restricted to the fields we use."""

    root_id: int
    flow: str
    super_class: str
    cell_class: str
    cell_sub_class: str
    cell_type: str
    supertype: str
    top_nt: str
    side: str
    status: str

    @property
    def is_outlier(self) -> bool:
        """True for FlyWire ``outlier_seg`` / ``outlier_bio`` flags."""
        return bool(self.status)


@dataclass(frozen=True)
class Edge:
    """One aggregated neuron-pair connection.

    ``neurotransmitter`` is the dominant transmitter for this connection,
    derived from the ``*_avg`` prediction-score columns, and ``sign`` is the
    +1/-1/0 synaptic sign that neurotransmitter maps to.
    """

    pre: int
    post: int
    neuropil: str
    n_synapses: int
    neurotransmitter: str
    sign: int
    artificial: bool = False


@dataclass
class LoadStats:
    """Dropped-edge accounting, recorded in the manifest."""

    neuron_rows: int = 0
    edge_rows_scanned: int = 0
    edges_kept: int = 0
    edges_dropped_unknown_endpoint: int = 0
    edges_dropped_below_threshold: int = 0
    synapses_dropped_unknown_endpoint: int = 0
    edges_dropped_duplicate_pair: int = 0
    edges_dropped_unselected_endpoint: int = 0
    duplicate_root_ids: int = 0
    candidate_neurons: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "neuron_rows": self.neuron_rows,
            "edge_rows_scanned": self.edge_rows_scanned,
            "candidate_neurons": self.candidate_neurons,
            "edges_kept": self.edges_kept,
            "edges_dropped_unknown_endpoint": self.edges_dropped_unknown_endpoint,
            "edges_dropped_below_synapse_threshold": self.edges_dropped_below_threshold,
            "synapses_dropped_unknown_endpoint": self.synapses_dropped_unknown_endpoint,
            "edges_dropped_duplicate_pair": self.edges_dropped_duplicate_pair,
            "edges_dropped_unselected_endpoint": self.edges_dropped_unselected_endpoint,
            "duplicate_root_ids": self.duplicate_root_ids,
        }


@dataclass
class Subcircuit:
    """An extracted, simulation-ready subcircuit."""

    neurons: list[Neuron]
    index: dict[int, int]
    roles: dict[str, list[int]]
    # (pre_internal, post_internal, n_synapses, sign, "pre_role->post_role",
    #  artificial)
    edges: list[tuple[int, int, int, int, str]] = field(default_factory=list)
    output_neuron_ids: list[int] = field(default_factory=list)
    stats: LoadStats = field(default_factory=LoadStats)
    rules: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    reachability: dict[str, Any] = field(default_factory=dict)
    # Provenance of the synthetic lobula -> Kenyon cell bridge, if enabled.
    reachability_bridge: dict[str, Any] = field(default_factory=dict)

    @property
    def n_neurons(self) -> int:
        return len(self.neurons)

    def counts_per_role(self) -> dict[str, int]:
        return {role: len(ids) for role, ids in self.roles.items()}

    def edges_per_role_pair(self) -> dict[str, int]:
        return dict(sorted(self._pair_counts().items()))

    def _pair_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for *_, role_pair in self.edges:
            counts[role_pair] += 1
        return counts

    def synapses_per_role_pair(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for _, _, n_syn, _, role_pair, _art in self.edges:
            counts[role_pair] += n_syn
        return dict(sorted(counts.items()))

    def summary(self) -> dict[str, Any]:
        artificial = [e for e in self.edges if e[5]]
        return {
            "n_neurons": self.n_neurons,
            "neurons_per_role": self.counts_per_role(),
            "n_edges": len(self.edges),
            "edges_per_role_pair": self.edges_per_role_pair(),
            "synapses_per_role_pair": self.synapses_per_role_pair(),
            "n_synapses_total": sum(e[2] for e in self.edges),
            "real_vs_artificial": {
                "note": (
                    "Edges flagged artificial are SYNTHESISED, not FlyWire "
                    "connectivity. See subcircuit.bridge in the config."
                ),
                "n_real_edges": len(self.edges) - len(artificial),
                "n_artificial_edges": len(artificial),
                "n_real_synapses": sum(e[2] for e in self.edges if not e[5]),
                "n_artificial_synapses": sum(e[2] for e in artificial),
            },
            "output_neuron_ids": self.output_neuron_ids,
            "output_selection_rule": self.reachability.get("output_selection_rule"),
            "annotation_rules": self.rules,
            "dropped_edges": self.stats.as_dict(),
            "reachability": self.reachability,
            "artificial_bridge": self.reachability_bridge,
        }


# --------------------------------------------------------------------------
# Checksums and schema
# --------------------------------------------------------------------------

def verify_files(config, manifest=None) -> dict[str, str]:
    """Verify configured checksums, recording skips as manifest warnings."""
    digests: dict[str, str] = {}
    for label, path_key, md5_key in (
        ("annotations", "annotations_file", "annotations_md5"),
        ("connections", "connections_file", "connections_md5"),
    ):
        path = config.get(f"connectome.{path_key}")
        expected = config.get(f"connectome.{md5_key}")
        if not expected:
            message = (
                f"{label} checksum is null in configuration ({path}); "
                "checksum verification skipped for this run"
            )
            (manifest.warn if manifest else LOGGER.warning)(message)
            continue
        digests[label] = verify_checksum(path, expected, "md5")
        LOGGER.info("checksum ok: %s md5=%s", label, digests[label])
    return digests


def _require_columns(present: Sequence[str], required: Iterable[str], filename: str) -> None:
    missing = [column for column in required if column not in present]
    if missing:
        raise SchemaError(
            f"{filename}: missing required column(s) {missing}\n"
            f"  columns found:    {list(present)}\n"
            f"  columns required: {list(required)}"
        )


def annotation_columns(path: str | Path) -> list[str]:
    """Header of the neuron annotation TSV."""
    with open(path, "r", encoding="utf-8", newline="") as handle:
        return next(csv.reader(handle, delimiter="\t"))


def connection_columns(path: str | Path) -> list[str]:
    """Column names of the feather connectivity file."""
    import pyarrow as pa

    reader = pa.ipc.open_file(pa.OSFile(str(path), "rb"))
    return list(reader.schema.names)


def connection_row_count(path: str | Path) -> int:
    """Total rows in the feather file, for memory planning."""
    import pyarrow as pa

    reader = pa.ipc.open_file(pa.OSFile(str(path), "rb"))
    return sum(reader.get_batch(i).num_rows for i in range(reader.num_record_batches))


def validate_schema(config) -> dict[str, list[str]]:
    """Validate both schemas up front, before anything is built."""
    annotation_path = config.get("connectome.annotations_file")
    connection_path = config.get("connectome.connections_file")
    found = {
        "annotations": annotation_columns(annotation_path),
        "connections": connection_columns(connection_path),
    }
    allow_missing = bool(config.get("connectome.allow_missing_columns"))
    for key, required_key in (
        ("annotations", "required_neuron_columns"),
        ("connections", "required_connection_columns"),
    ):
        required = list(config.get(f"connectome.{required_key}"))
        if allow_missing:
            missing = [c for c in required if c not in found[key]]
            if missing:
                LOGGER.warning(
                    "%s: configured columns absent %s (allow_missing_columns=true)",
                    key, missing,
                )
        else:
            _require_columns(found[key], required, Path(
                annotation_path if key == "annotations" else connection_path
            ).name)
    return found


# --------------------------------------------------------------------------
# Annotation table
# --------------------------------------------------------------------------

def _wanted_annotation_columns(required: Iterable[str]) -> list[str]:
    return sorted(set(required) | set(ANNOTATION_COLUMNS))


def load_neurons(config, manifest=None) -> dict[int, Neuron]:
    """Load the annotation TSV into a root_id -> Neuron map.

    Rows are read in file order; the first occurrence of a root_id wins and
    later duplicates are counted, so the mapping is repeatable.
    """
    path = Path(config.get("connectome.annotations_file"))
    present = annotation_columns(path)
    allow_missing = bool(config.get("connectome.allow_missing_columns"))
    if allow_missing:
        absent = [c for c in _wanted_annotation_columns(
            config.get("connectome.required_neuron_columns")) if c not in present]
        if absent:
            message = (
                f"{path.name}: configured annotation columns absent {absent} "
                "(allow_missing_columns=true; schema not fully validated)"
            )
            (manifest.warn if manifest else LOGGER.warning)(message)
    else:
        _require_columns(
            present,
            _wanted_annotation_columns(config.get("connectome.required_neuron_columns")),
            path.name,
        )

    neurons: dict[int, Neuron] = {}
    duplicates = 0
    with open(path, "r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            try:
                root_id = int(row["root_id"])
            except (TypeError, ValueError, KeyError):
                continue
            if root_id in neurons:
                duplicates += 1
                continue
            neurons[root_id] = Neuron(
                root_id=root_id,
                flow=row.get("flow") or "",
                super_class=row.get("super_class") or "",
                cell_class=row.get("cell_class") or "",
                cell_sub_class=row.get("cell_sub_class") or "",
                cell_type=row.get("cell_type") or "",
                supertype=row.get("supertype") or "",
                top_nt=(row.get("top_nt") or "").strip().lower(),
                side=row.get("side") or "",
                status=row.get("status") or "",
            )
    LOGGER.info("loaded %d annotated neurons (%d duplicate root_ids)", len(neurons), duplicates)
    _LAST_STATS.duplicate_root_ids = duplicates
    return neurons


_LAST_STATS = LoadStats()


# --------------------------------------------------------------------------
# Connectivity
# --------------------------------------------------------------------------

def iter_connections(
    config,
    allowed: set[int] | None = None,
    stats: LoadStats | None = None,
) -> Iterator[Edge]:
    """Stream aggregated edges from the feather file in file order.

    ``allowed`` restricts results to pairs where both endpoints are in the set,
    which is what keeps peak memory bounded during subcircuit extraction.

    The dominant transmitter for each connection is the transmitter with the
    highest ``*_avg`` prediction score, mapped to a sign via
    ``connectome.neurotransmitter_signs``. This is the real per-connection
    signal in proofread_connections_783.feather, so signs are not inherited
    from the presynaptic neuron.
    """
    import pyarrow as pa

    path = Path(config.get("connectome.connections_file"))
    present = connection_columns(path)
    if not config.get("connectome.allow_missing_columns"):
        _require_columns(
            present, config.get("connectome.required_connection_columns"), path.name
        )

    nt_map: dict[str, str] = config.section("connectome.connection_nt_columns")
    sign_map = config.section("connectome.neurotransmitter_signs")
    unknown_sign = int(config.get("connectome.unknown_sign"))
    usable = [(label, column) for label, column in nt_map.items() if column in present]

    projection = ["pre_pt_root_id", "post_pt_root_id", "syn_count", "neuropil"]
    projection += [column for _label, column in usable]
    projection = sorted(set(projection))

    min_synapses = int(config.get("connectome.min_synapses"))
    reader = pa.ipc.open_file(pa.OSFile(str(path), "rb"))
    for batch_no in range(reader.num_record_batches):
        batch = reader.get_batch(batch_no)
        if projection:
            batch = batch.select([batch.schema.get_field_index(c) for c in projection])
        pre = batch.column("pre_pt_root_id").to_numpy(zero_copy_only=False)
        post = batch.column("post_pt_root_id").to_numpy(zero_copy_only=False)
        counts = batch.column("syn_count").to_numpy(zero_copy_only=False)
        neuropils = batch.column("neuropil").to_pylist()
        scores = [
            np.nan_to_num(
                batch.column(column).to_numpy(zero_copy_only=False).astype(np.float64),
                nan=-1.0, posinf=-1.0, neginf=-1.0,
            )
            for _label, column in usable
        ]
        if stats is not None:
            stats.edge_rows_scanned += len(pre)

        for i in range(len(pre)):
            n_syn = int(counts[i])
            if n_syn < min_synapses:
                if stats is not None:
                    stats.edges_dropped_below_threshold += 1
                continue
            pre_id = int(pre[i])
            post_id = int(post[i])
            if allowed is not None and (pre_id not in allowed or post_id not in allowed):
                if stats is not None:
                    stats.edges_dropped_unknown_endpoint += 1
                    stats.synapses_dropped_unknown_endpoint += n_syn
                continue

            if scores:
                best = int(max(range(len(scores)), key=lambda k: scores[k][i]))
                label = usable[best][0]
                sign = int(sign_map.get(label, unknown_sign))
            else:
                label, sign = "", unknown_sign
            yield Edge(
                pre=pre_id,
                post=post_id,
                neuropil=str(neuropils[i]),
                n_synapses=n_syn,
                neurotransmitter=label,
                sign=sign,
            )


def build_index(root_ids: Sequence[int]) -> dict[int, int]:
    """Deterministic contiguous internal index mapping.

    Sorted by FlyWire root_id, so the mapping depends only on the input set and
    not on dictionary iteration or file ordering.
    """
    return {root_id: i for i, root_id in enumerate(sorted(root_ids))}


# --------------------------------------------------------------------------
# Annotation rules
# --------------------------------------------------------------------------

def _matches(neuron: Neuron, rule: dict[str, Any]) -> bool:
    column = rule["column"]
    if column not in ANNOTATION_COLUMNS:
        raise SchemaError(
            f"annotation rule references unknown column {column!r}; "
            f"available columns: {list(ANNOTATION_COLUMNS)}"
        )
    value = getattr(neuron, column)
    if "equals" in rule:
        return value == rule["equals"]
    if "not_equals" in rule:
        return value != rule["not_equals"]
    if "contains" in rule:
        return rule["contains"].lower() in value.lower()
    if "in" in rule:
        return value in rule["in"]
    if "startswith" in rule:
        return value.lower().startswith(rule["startswith"].lower())
    if "empty" in rule:
        return (value == "") is bool(rule["empty"])
    raise SchemaError(
        f"annotation rule has no recognised test (equals/not_equals/contains/"
        f"in/startswith/empty): {rule}"
    )


def select_role(
    neurons: dict[int, Neuron],
    rules: Sequence[dict[str, Any]],
    restrict_side: str = "",
) -> list[int]:
    """Root_ids matching every rule in ``rules`` (rules are ANDed).

    Iterates ``sorted(neurons)`` so the result is order-independent.
    """
    out: list[int] = []
    for root_id in sorted(neurons):
        neuron = neurons[root_id]
        if restrict_side and neuron.side and neuron.side != restrict_side:
            continue
        if all(_matches(neuron, rule) for rule in rules):
            out.append(root_id)
    return out


def assign_signs(
    pre_ids: Sequence[int],
    neurons: dict[int, Neuron],
    sign_map: dict[str, int],
    unknown_sign: int,
    warn: bool,
    manifest=None,
) -> list[int]:
    """Map each presynaptic neuron's ``top_nt`` to +1 / -1 / 0.

    The mapping is per neuron, not per synapse: FlyWire publishes ``top_nt`` as
    a single top prediction per neuron. That is a documented simplification.
    """
    cache: dict[int, int] = {}
    unknown_labels: set[str] = set()

    def sign_for(root_id: int) -> int:
        if root_id not in cache:
            label = neurons[root_id].top_nt
            if label in sign_map:
                cache[root_id] = int(sign_map[label])
            else:
                unknown_labels.add(label or "<empty>")
                cache[root_id] = int(unknown_sign)
        return cache[root_id]

    signs = [sign_for(pre) for pre in pre_ids]
    if unknown_labels and warn:
        head = sorted(unknown_labels)[:10]
        message = (
            f"{len(unknown_labels)} unrecognized neurotransmitter label(s) mapped to "
            f"sign {unknown_sign}: {head}"
            + (" ..." if len(unknown_labels) > 10 else "")
        )
        (manifest.warn if manifest else LOGGER.warning)(message)
    return signs


# --------------------------------------------------------------------------
# Subcircuit extraction
# --------------------------------------------------------------------------

def _plastic_drive(
    targets: set[int],
    sources: set[int],
    edges_pre: np.ndarray,
    edges_post: np.ndarray,
    edges_n: np.ndarray,
) -> dict[int, float]:
    """Relative plastic drive each target receives, at unit ``weight_scale``.

    This mirrors ``network.build_network``'s ``fan_in`` rule, where an edge
    carries ``weight_scale / synapse_count``. Balancing on raw synapse COUNT is
    not enough: with per-edge weights inversely proportional to synapse count,
    a target with 14 edges can still draw several times the current of one with
    3. Unit scale keeps the comparison independent of the calibration search.
    """
    if not targets or not sources:
        return {}
    in_sources = np.isin(edges_pre, list(sources))
    in_targets = np.isin(edges_post, list(targets))
    mask = in_sources & in_targets
    if not mask.any():
        return {}
    totals: dict[int, float] = defaultdict(float)
    for post, n_syn in zip(edges_post[mask].tolist(), edges_n[mask].tolist()):
        if n_syn > 0:
            totals[int(post)] += 1.0 / float(n_syn)
    return totals


def _incoming_strength(
    ids: set[int],
    upstream: set[int],
    edges_pre: np.ndarray,
    edges_post: np.ndarray,
    edges_n: np.ndarray,
) -> dict[int, int]:
    """Total synapses each id receives from ``upstream``."""
    if upstream:
        in_upstream = np.isin(edges_pre, list(upstream))
    else:
        in_upstream = np.ones(len(edges_pre), dtype=bool)
    in_targets = np.isin(edges_post, list(ids))
    mask = in_upstream & in_targets
    if not mask.any():
        return {}
    totals: dict[int, int] = defaultdict(int)
    for post, n_syn in zip(edges_post[mask].tolist(), edges_n[mask].tolist()):
        totals[int(post)] += int(n_syn)
    return totals


def _outgoing_strength(
    ids: set[int],
    downstream: set[int],
    edges_pre: np.ndarray,
    edges_post: np.ndarray,
    edges_n: np.ndarray,
) -> dict[int, int]:
    """Total synapses each id sends to ``downstream``."""
    if downstream:
        in_downstream = np.isin(edges_post, list(downstream))
    else:
        in_downstream = np.ones(len(edges_pre), dtype=bool)
    in_ids = np.isin(edges_pre, list(ids))
    mask = in_ids & in_downstream
    if not mask.any():
        return {}
    totals: dict[int, int] = defaultdict(int)
    for pre, n_syn in zip(edges_pre[mask].tolist(), edges_n[mask].tolist()):
        totals[int(pre)] += int(n_syn)
    return totals


def read_stages(config) -> list[dict[str, Any]]:
    """Ordered stage definitions from configuration, validated."""
    stages = config.sequence("subcircuit.stages")
    if not stages:
        raise SchemaError("subcircuit.stages is empty")
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for position, stage in enumerate(stages):
        if not isinstance(stage, dict):
            raise SchemaError(f"subcircuit.stages[{position}] must be a mapping")
        name = stage.get("name")
        if not name:
            raise SchemaError(f"subcircuit.stages[{position}] has no name")
        if name in seen:
            raise SchemaError(f"duplicate stage name in subcircuit.stages: {name}")
        seen.add(name)
        rules = stage.get("rules")
        if not isinstance(rules, list) or not rules:
            raise SchemaError(f"stage {name!r} has no rules")
        cap = stage.get("cap")
        if not isinstance(cap, int) or cap <= 0:
            raise SchemaError(f"stage {name!r} needs a positive integer cap, got {cap!r}")
        out.append({"name": name, "rules": rules, "cap": cap})
    if INPUT_STAGES[0] not in seen:
        raise SchemaError(
            f"subcircuit.stages must include an input stage named {INPUT_STAGES[0]!r}"
        )
    if not set(MUSHROOM_BODY_STAGES) & seen:
        raise SchemaError(
            "subcircuit.stages must include a mushroom body stage "
            f"(one of {list(MUSHROOM_BODY_STAGES)})"
        )
    if not set(OUTPUT_STAGES) & seen:
        raise SchemaError(
            f"subcircuit.stages must include an output stage named {OUTPUT_STAGES[0]!r}"
        )
    return out


def extract_subcircuit(config, manifest=None) -> Subcircuit:
    """Select stages by annotation, cap populations, and build the edge list.

    Stages are processed in signal order. Each stage's candidates are ranked by
    the synapses they receive from the previously selected stages, so the
    retained neurons are the most strongly driven and no stage collapses to
    nothing. Ties break on ascending root_id, which makes selection
    reproducible across runs.
    """
    neurons = load_neurons(config, manifest=manifest)
    stats = LoadStats(neuron_rows=len(neurons), duplicate_root_ids=_LAST_STATS.duplicate_root_ids)

    if config.get("subcircuit.exclude_outliers"):
        before = len(neurons)
        neurons = {k: v for k, v in neurons.items() if not v.is_outlier}
        LOGGER.info("excluded %d outlier neurons", before - len(neurons))

    stages = read_stages(config)
    restrict_side = config.get("subcircuit.restrict_side") or ""

    candidates: dict[str, list[int]] = {}
    for stage in stages:
        ids = select_role(neurons, stage["rules"], restrict_side=restrict_side)
        candidates[stage["name"]] = ids
        LOGGER.info("stage %-15s %6d candidates", stage["name"], len(ids))
        if not ids and stage["name"] not in INPUT_STAGES:
            raise SchemaError(
                f"no neurons matched subcircuit.stages[{stage['name']}].rules = "
                f"{stage['rules']}; check the annotation rules against the real schema"
            )

    # One streaming pass over the connectivity file, keeping only edges whose
    # endpoints are candidates for some stage.
    union: set[int] = set()
    for ids in candidates.values():
        union.update(ids)
    stats.candidate_neurons = len(union)
    LOGGER.info("scanning connectivity file for %d candidate neurons", len(union))

    kept_pre: list[int] = []
    kept_post: list[int] = []
    kept_n: list[int] = []
    kept_nt: list[str] = []
    kept_sign: list[int] = []
    seen_pairs: dict[tuple[int, int], int] = {}
    unknown_nt: set[str] = set()
    for edge in iter_connections(config, allowed=union, stats=stats):
        key = (edge.pre, edge.post)
        if key in seen_pairs:
            stats.edges_dropped_duplicate_pair += 1
            slot = seen_pairs[key]
            # Same pair, different neuropil: keep both, take the dominant
            # neurotransmitter by synapse count.
            kept_n[slot] += edge.n_synapses
            if edge.neurotransmitter not in unknown_nt:
                unknown_nt.add(edge.neurotransmitter)
            continue
        seen_pairs[key] = len(kept_pre)
        kept_pre.append(edge.pre)
        kept_post.append(edge.post)
        kept_n.append(edge.n_synapses)
        kept_nt.append(edge.neurotransmitter)
        kept_sign.append(edge.sign)
    stats.edges_kept = len(kept_pre)
    LOGGER.info(
        "kept %d edges (%.2fM synapses) between candidates",
        stats.edges_kept, sum(kept_n) / 1e6,
    )

    edges_pre = np.asarray(kept_pre, dtype=np.int64)
    edges_post = np.asarray(kept_post, dtype=np.int64)
    edges_n = np.asarray(kept_n, dtype=np.int64)

    n_outputs = int(config.get("network.n_outputs"))
    selected: dict[str, list[int]] = {}
    selection_detail: dict[str, Any] = {}
    upstream: set[int] = set()

    # Backward seeding of the visual chain. Ranking each stage by the synapses
    # it RECEIVES from the previous stage does not guarantee the chain reaches
    # the end. Measured on release 783: the medulla neurons picked that way
    # project to none of the top-ranked lobula candidates, so the lobula
    # finished with ZERO in-edges and the artificial lobula -> Kenyon-cell
    # bridge carried no visual signal at all (the manifest's own reachability
    # block reported `signal_reaches_outputs: false`).
    #
    # Seeding the whole chain backward fixes it with real connectivity. The
    # last stage is ranked by input from its predecessor's CANDIDATE pool;
    # every earlier stage is then ranked by what it SENDS to the stage already
    # seeded. Ranking one link backward is not enough on its own: preferring
    # only `medulla -> lobula` satisfies that link and silently drops
    # `lamina -> medulla`, which just moves the break upstream.
    backward_chain = [
        str(s) for s in (config.get("subcircuit.selection.backward_chain") or [])
    ]
    balance_outputs = bool(
        config.get("subcircuit.selection.balance_outputs", True)
    )
    # A readout neuron with a single plastic synapse cannot be compared against
    # one with many: one presynaptic spike saturates it, so it either always
    # wins or never fires. Require a workable minimum before balancing.
    min_plastic_input = int(
        config.get("subcircuit.selection.min_plastic_input", 3)
    )
    # Two readouts fed by the same Kenyon cells can never respond differently,
    # so their input overlap is capped. This only chooses among real MBON
    # neurons; no edge is synthesised.
    max_readout_overlap = float(
        config.get("subcircuit.selection.max_readout_overlap", 0.5)
    )
    preselected: dict[str, list[int]] = {}
    seed_rule_detail: dict[str, str] = {}
    if len(backward_chain) >= 2:
        caps = {s["name"]: int(s["cap"]) for s in stages}
        order = [s["name"] for s in stages]
        last = backward_chain[-1]
        prev = order[order.index(last) - 1] if order.index(last) > 0 else ""
        last_pool = list(candidates.get(last) or [])
        prev_pool = set(candidates.get(prev) or [])
        if last_pool and prev_pool:
            strength = _incoming_strength(
                set(last_pool), prev_pool, edges_pre, edges_post, edges_n
            )
            preselected[last] = sorted(
                last_pool, key=lambda r: (-strength.get(r, 0), r)
            )[:caps.get(last, len(last_pool))]
            seed_rule_detail[last] = (
                f"backward seed: top {caps.get(last)} candidates by synapses "
                f"received from the {prev} CANDIDATE pool"
            )
            LOGGER.info(
                "backward seed %s: %d/%d candidates receive from the %s pool",
                last, sum(1 for r in last_pool if strength.get(r, 0) > 0),
                len(last_pool), prev,
            )
        for position in range(len(backward_chain) - 2, -1, -1):
            name = backward_chain[position]
            downstream = backward_chain[position + 1]
            pool = list(candidates.get(name) or [])
            target = set(preselected.get(downstream) or [])
            if not pool or not target:
                continue
            outgoing = _outgoing_strength(
                set(pool), target, edges_pre, edges_post, edges_n
            )
            preselected[name] = sorted(
                pool, key=lambda r: (-outgoing.get(r, 0), r)
            )[:caps.get(name, len(pool))]
            seed_rule_detail[name] = (
                f"backward seed: top {caps.get(name)} candidates by synapses sent "
                f"to the seeded {downstream}"
            )
            LOGGER.info(
                "backward seed %s: %d/%d candidates project to the seeded %s",
                name, sum(1 for r in pool if outgoing.get(r, 0) > 0),
                len(pool), downstream,
            )

    for stage in stages:
        name = stage["name"]
        pool = candidates[name]
        cap = int(stage["cap"])
        if name in OUTPUT_STAGES:
            cap = n_outputs
        if name in preselected:
            chosen = preselected[name]
            rule = seed_rule_detail.get(name, "backward seed") + (
                "; ties broken by ascending root_id"
            )
            # Report connectivity the way the stage was actually ranked, so
            # `driven_selected` in the manifest reflects the seeded linkage
            # rather than the forward rule that was not used here.
            downstream = None
            if name in backward_chain:
                position = backward_chain.index(name)
                if position + 1 < len(backward_chain):
                    downstream = set(
                        preselected.get(backward_chain[position + 1]) or []
                    )
            if downstream:
                strength = _outgoing_strength(
                    set(pool), downstream, edges_pre, edges_post, edges_n
                ) if pool else {}
            else:
                strength = {}
        else:
            strength = _incoming_strength(
                set(pool), upstream, edges_pre, edges_post, edges_n
            ) if pool else {}
            # The readout neurons must be comparable in how much plastic drive
            # they receive. Ranking them purely by input strength picks the
            # most strongly driven MBON neurons, and measured on release 783
            # that gave a 248:1 spread in total mushroom_body -> output drive
            # (1, 19, 2 and 6 synapses). One neuron then won essentially every
            # argmax, so every trial was punished, dopamine decayed all weights
            # uniformly, and there was no category signal left to learn from.
            # Balance is enforced by choosing neurons whose incoming strength
            # is closest to a common target; the edges stay real.
            if balance_outputs and name in OUTPUT_STAGES and pool:
                # Balance on the plastic drive specifically: incoming synapses
                # from the mushroom body. Balancing on total input from every
                # upstream stage would also count reinforcement and optic-lobe
                # edges and can pick a readout with no mushroom-body input at
                # all, which is worse than the asymmetry it replaces.
                mb_pool = set()
                for mb_name in MUSHROOM_BODY_STAGES:
                    mb_pool |= set(selected.get(mb_name) or [])
                plastic_strength = _incoming_strength(
                    set(pool), mb_pool, edges_pre, edges_post, edges_n
                ) if pool and mb_pool else {}
                drive = _plastic_drive(
                    set(pool), mb_pool, edges_pre, edges_post, edges_n
                ) if pool and mb_pool else {}
                # Rank on the drive the neuron will actually receive, and keep
                # only neurons with enough presynaptic contacts to be tunable.
                driven_pool = [
                    r for r in pool
                    if plastic_strength.get(r, 0) >= min_plastic_input
                    and drive.get(r, 0.0) > 0.0
                ]
                if len(driven_pool) >= cap:
                    # Which Kenyon cells feed each candidate, so overlaps can
                    # be compared directly.
                    inputs: dict[int, set[int]] = defaultdict(set)
                    for pre_root, post_root in zip(
                        edges_pre.tolist(), edges_post.tolist()
                    ):
                        if post_root in set(driven_pool) and pre_root in mb_pool:
                            inputs[post_root].add(pre_root)
                    ordered = sorted(
                        driven_pool, key=lambda r: (-drive[r], r)
                    )[: max(cap * 16, cap)]
                    values = sorted(drive[r] for r in ordered)
                    target = values[len(values) // 2]

                    def _overlap(a: int, b: int) -> float:
                        sa, sb = inputs.get(a, set()), inputs.get(b, set())
                        union = sa | sb
                        return len(sa & sb) / len(union) if union else 1.0

                    # Balance drive, but refuse to pick two readouts that share
                    # the same Kenyon-cell input. Balancing alone selected two
                    # MBON neurons with identical input (Jaccard 1.00, 20/20
                    # shared), so a quarter of the readout could never respond
                    # differently and the output was flat for every word.
                    ranked_balanced = sorted(
                        ordered,
                        key=lambda r: (abs(drive[r] - target), -drive[r], r),
                    )
                    chosen = [ranked_balanced[0]]
                    for cand in ranked_balanced[1:]:
                        if len(chosen) >= cap:
                            break
                        if all(
                            _overlap(cand, kept) <= max_readout_overlap
                            for kept in chosen
                        ):
                            chosen.append(cand)
                    # If the overlap limit cannot be met, keep the most
                    # distinct set available rather than dropping to fewer
                    # readouts than the experiment needs.
                    if len(chosen) < cap:
                        for cand in ranked_balanced:
                            if len(chosen) >= cap:
                                break
                            if cand not in chosen:
                                chosen.append(cand)
                    chosen = sorted(chosen, key=lambda r: r)
                    worst = max(
                        (
                            _overlap(chosen[i], chosen[j])
                            for i in range(len(chosen))
                            for j in range(i + 1, len(chosen))
                        ),
                        default=0.0,
                    )
                    spread = (
                        max(drive[r] for r in chosen)
                        / max(min(drive[r] for r in chosen), 1e-12)
                    )
                    rule = (
                        f"balanced readout with distinct inputs: {cap} "
                        f"candidates chosen for near-equal mushroom_body drive "
                        f"(max/min {spread:.1f}x) and pairwise input overlap at "
                        f"most {max_readout_overlap:.2f} (achieved worst "
                        f"{worst:.2f}); at least {min_plastic_input} plastic "
                        f"synapses each"
                    )
                else:
                    ranked = sorted(
                        pool, key=lambda r: (-strength.get(r, 0), r)
                    )
                    chosen = ranked[:cap]
                    rule = (
                        "top N candidates by synapses received from the "
                        "previously selected stages; ties broken by "
                        "ascending root_id"
                    )
            else:
                ranked = sorted(
                    pool, key=lambda r: (-strength.get(r, 0), r)
                ) if pool else []
                chosen = ranked[:cap]
                if upstream:
                    rule = (
                        "top N candidates by synapses received from the previously "
                        "selected stages; ties broken by ascending root_id"
                    )
                else:
                    rule = (
                        "input stage: all candidates in root_id order, truncated to "
                        "the cap"
                    )
        selected[name] = chosen
        selection_detail[name] = {
            "rule": rule,
            "candidates": len(pool),
            "selected": len(chosen),
            "cap": cap,
            "driven_selected": sum(1 for c in chosen if strength.get(c, 0) > 0),
            "driven_candidates": sum(1 for c in pool if strength.get(c, 0) > 0),
        }
        upstream |= set(chosen)

    output_stage = next(s for s in stages if s["name"] in OUTPUT_STAGES)
    output_ids = selected[output_stage["name"]]
    if not output_ids:
        raise SchemaError(
            f"stage {output_stage['name']!r} selected no output neurons; "
            "cannot build a readout"
        )
    output_rule = selection_detail[output_stage["name"]]["rule"]

    # Stages must stay disjoint so internal indices are unambiguous. The first
    # stage in signal order wins when a neuron satisfies several rule sets.
    role_of: dict[int, str] = {}
    deduped: list[int] = []
    for stage in stages:
        for root_id in selected[stage["name"]]:
            if root_id not in role_of:
                role_of[root_id] = stage["name"]
                deduped.append(root_id)

    index = build_index(deduped)

    # Only edges between neurons that actually survived selection are simulated.
    # Everything else was candidate-only and must not enter the network.
    internal_edges: list[tuple[int, int, int, int, str]] = []
    internal_nt: list[str] = []
    dropped_unselected = 0
    for i, (pre, post, n_syn) in enumerate(zip(kept_pre, kept_post, kept_n)):
        pre_i = index.get(pre)
        post_i = index.get(post)
        if pre_i is None or post_i is None:
            dropped_unselected += 1
            continue
        internal_edges.append(
            (pre_i, post_i, n_syn, kept_sign[i], f"{role_of[pre]}->{role_of[post]}", False)
        )
        internal_nt.append(kept_nt[i])
    stats.edges_dropped_unselected_endpoint = dropped_unselected
    LOGGER.info(
        "%d of %d candidate edges retained between selected neurons",
        len(internal_edges), len(kept_pre),
    )

    nt_histogram: dict[str, int] = defaultdict(int)
    for label in internal_nt:
        nt_histogram[label] += 1
    sign_histogram: dict[int, int] = defaultdict(int)
    for _pre, _post, _n, sign, _pair, _artificial in internal_edges:
        sign_histogram[sign] += 1
    if unknown_nt and config.get("connectome.warn_on_unknown_neurotransmitter"):
        LOGGER.warning(
            "neurotransmitters seen in the connectivity file: %s",
            sorted(unknown_nt),
        )

    # Memory budget. Extraction is a single streaming pass, so peak RSS is
    # dominated by the retained candidate-edge arrays rather than the input
    # file. Report it and fail fast if the configured budget is exceeded, as
    # required by the connectome-loading spec.
    try:
        import psutil

        peak_mb = round(psutil.Process().memory_info().rss / 1024**2, 1)
    except ImportError:  # pragma: no cover
        peak_mb = float("nan")
    budget_mb = float(config.get("resources.memory_budget_mb"))
    LOGGER.info(
        "subcircuit peak memory %.1f MB of a %.0f MB budget", peak_mb, budget_mb
    )
    if budget_mb > 0 and peak_mb == peak_mb and peak_mb > budget_mb:
        raise MemoryError(
            f"subcircuit extraction used {peak_mb:.1f} MB, exceeding the "
            f"configured memory budget of {budget_mb:.0f} MB; reduce "
            "subcircuit.candidate or raise resources.memory_budget_mb"
        )

    roles_internal = {
        stage["name"]: sorted(index[r] for r in selected[stage["name"]] if r in index)
        for stage in stages
    }

    # ------------------------------------------------------------------
    # ARTIFICIAL BRIDGE: lobula -> Kenyon cell.
    #
    # Measured on release 783: Kenyon cells receive 1 synapse from lobula and
    # 2 from medulla, i.e. no optic-lobe input at all. The mushroom body is not
    # a visual target in Drosophila, so the stage chain the experiment needs
    # cannot be built from real connectivity. One synthetic synapse set closes
    # the gap. Source cells are picked by REAL upstream strength so the bridge
    # inherits real structure; the synapse counts and signs are invented and
    # every such edge is flagged `artificial`.
    # ------------------------------------------------------------------
    bridge_info: dict[str, Any] = {"enabled": bool(config.get("subcircuit.bridge.enabled"))}
    if config.get("subcircuit.bridge.enabled"):
        lobula_stage = next((s for s in stages if s["name"] == "lobula"), None)
        if lobula_stage is None:
            bridge_info["skipped"] = "no lobula stage configured"
        else:
            per_kc = int(config.get("subcircuit.bridge.synapses_per_kc"))
            max_sources = int(config.get("subcircuit.bridge.max_sources"))
            sign = int(config.get("subcircuit.bridge.sign"))
            n_syn = int(config.get("subcircuit.bridge.weight"))
            seed = int(config.get("subcircuit.bridge.seed", 20240917))

            # Rank lobula sources by synapses received from the stages actually
            # selected upstream of them, measured on real edges.
            upstream_before_lobula: set[int] = set()
            for stage in stages:
                if stage["name"] == "lobula":
                    break
                upstream_before_lobula.update(selected[stage["name"]])
            lobula_strength = _incoming_strength(
                set(selected["lobula"]), upstream_before_lobula,
                edges_pre, edges_post, edges_n,
            )
            sources = sorted(
                selected["lobula"],
                key=lambda r: (-lobula_strength.get(r, 0), r),
            )[:max_sources]
            sources = [r for r in sources if r in index]
            targets = [r for r in selected["mushroom_body"] if r in index]
            if not sources or not targets:
                bridge_info["skipped"] = "no lobula sources or no Kenyon cell targets"
            else:
                per_kc = min(int(config.get("subcircuit.bridge.synapses_per_kc")), len(sources))
                rng = np.random.default_rng(seed)
                patterns: set[tuple[int, ...]] = set()
                for target in targets:
                    # Idiosyncratic sparse projection per Kenyon cell. The
                    # previous round-robin took sources[(i + k) % n_sources], so
                    # with 24 targets and 12 sources the input sets repeated
                    # every n_sources targets and each set was a window of
                    # per_kc consecutive sources. That collapsed 24 Kenyon
                    # cells into 12 heavily overlapping patterns, leaving the
                    # population near-identical and giving reinforcement no
                    # word-specific pattern to strengthen. A seeded draw gives
                    # each cell its own subset and stays reproducible.
                    picked = sorted(
                        sources[p]
                        for p in rng.choice(
                            len(sources), size=per_kc, replace=False
                        )
                    )
                    patterns.add(tuple(picked))
                    for source in picked:
                        internal_edges.append(
                            (
                                index[source],
                                index[target],
                                n_syn,
                                sign,
                                "lobula->mushroom_body",
                                True,
                            )
                        )
                bridge_info.update({
                    "role_pair": "lobula->mushroom_body",
                    "n_sources": len(sources),
                    "n_targets": len(targets),
                    "synapses_per_kc": per_kc,
                    "sign": sign,
                    "synapses_per_pair": n_syn,
                    "n_edges": len(targets) * per_kc,
                    "n_synapses": len(targets) * per_kc * n_syn,
                    "seed": seed,
                    "distinct_input_patterns": len(patterns),
                    "input_pattern_rule": (
                        "seeded sparse random subset of the strength-ranked "
                        "lobula sources, drawn independently per Kenyon cell"
                    ),
                    "source_selection": (
                        "top lobula neurons by synapses received from selected "
                        "upstream stages, ties by ascending root_id"
                    ),
                    "source_has_real_upstream_input": sum(
                        1 for r in sources if lobula_strength.get(r, 0) > 0
                    ),
                })
            LOGGER.info(
                "artificial bridge lobula->Kenyon cells: %s",
                {k: v for k, v in bridge_info.items() if k != "note"},
            )
    subcircuit = Subcircuit(
        neurons=[neurons[r] for r in deduped],
        index=index,
        roles=roles_internal,
        edges=internal_edges,
        output_neuron_ids=output_ids,
        stats=stats,
        rules={stage["name"]: stage["rules"] for stage in stages},
    )
    subcircuit.reachability_bridge = bridge_info
    if manifest is not None:
        manifest.subcircuit["artificial_bridge"] = bridge_info
    subcircuit.reachability = _check_reachability(
        subcircuit, max_hops=int(config.get("subcircuit.max_path_hops")),
        output_rule=output_rule,
    )
    subcircuit.reachability["selection"] = selection_detail
    subcircuit.reachability["neurotransmitter_histogram"] = dict(nt_histogram)
    subcircuit.reachability["sign_histogram"] = {
        ("excitatory" if k > 0 else "inhibitory" if k < 0 else "unknown"): v
        for k, v in sorted(sign_histogram.items())
    }
    if manifest is not None:
        manifest.subcircuit["neurotransmitter_histogram"] = dict(nt_histogram)
        manifest.subcircuit["sign_histogram"] = subcircuit.reachability["sign_histogram"]
    return subcircuit


def _reachable(adjacency: dict[int, set[int]], sources: set[int], max_hops: int) -> set[int]:
    visited = set(sources)
    frontier = set(sources)
    for _hop in range(max_hops):
        nxt: set[int] = set()
        for node in frontier:
            nxt |= adjacency.get(node, set())
        nxt -= visited
        if not nxt:
            break
        visited |= nxt
        frontier = nxt
    return visited


def _check_reachability(
    subcircuit: Subcircuit, max_hops: int, output_rule: str
) -> dict[str, Any]:
    """Verify signal can reach the outputs; record the outcome either way."""
    def adjacency_for(include_artificial: bool) -> dict[int, set[int]]:
        adjacency: dict[int, set[int]] = defaultdict(set)
        for pre, post, _n, sign, _pair, artificial in subcircuit.edges:
            if sign > 0 and (include_artificial or not artificial):
                adjacency[pre].add(post)
        return adjacency

    pr_internal = set(subcircuit.roles.get(ROLE_PHOTORECEPTOR, []))
    out_internal = set(subcircuit.roles.get(ROLE_OUTPUT, []))

    def probe(include_artificial: bool) -> tuple[set[int], int]:
        reached = _reachable(
            adjacency_for(include_artificial), pr_internal, max_hops
        )
        return reached, len(out_internal & reached)

    reached, outputs_reached = probe(True)
    real_reached, real_outputs = probe(False)
    return {
        "max_path_hops": max_hops,
        "excitatory_only": True,
        "reached_neurons": len(reached),
        "by_role": {
            role: {"reached": len(set(ids) & reached), "of": len(ids)}
            for role, ids in subcircuit.roles.items() if ids
        },
        "outputs_reached": outputs_reached,
        "outputs_total": len(out_internal),
        "signal_reaches_outputs": outputs_reached > 0,
        "with_bridge": {
            "outputs_reached": outputs_reached,
            "reached_neurons": len(reached),
            "signal_reaches_outputs": outputs_reached > 0,
        },
        "real_edges_only": {
            "outputs_reached": real_outputs,
            "reached_neurons": len(real_reached),
            "signal_reaches_outputs": real_outputs > 0,
            "note": (
                "Reachability excluding the artificial lobula->Kenyon-cell "
                "bridge. Real FlyWire connectivity does not carry visual signal "
                "into the mushroom body."
            ),
        },
        "output_selection_rule": output_rule,
        "note": (
            "Reachability is computed on excitatory edges only, within "
            f"{max_hops} hops of the photoreceptor population. 'with_bridge' "
            "includes the synthetic bridge; 'real_edges_only' does not."
        ),
    }