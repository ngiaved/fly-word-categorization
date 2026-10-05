"""Dataset validation, deterministic word rendering, and visual encoding.

Words are rendered to a grayscale image with a font bundled in
``flyread/assets`` (DejaVu Sans Mono, see the bundled license), resampled onto a
photoreceptor grid, and converted to firing rates and then to input currents.

``grid-v1`` is a documented simplification of the ommatidial array: the image is
split into a rectangular grid and each photoreceptor receives the mean darkness
of its patch. It is not a retinotopic model of the real compound eye.
"""

from __future__ import annotations

import csv
import hashlib
import logging
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from .repro import make_rng

LOGGER = logging.getLogger(__name__)

SCHEME_GRID_V1 = "grid-v1"
WORD_LENGTH = 4


class DatasetError(ValueError):
    """Raised when data/words.csv violates the dataset constraints."""


class EncodingError(ValueError):
    """Raised for font, mapping, or conversion problems."""


# --------------------------------------------------------------------------
# Dataset
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class WordItem:
    word: str
    category: str
    label: int


@dataclass
class Dataset:
    items: list[WordItem]
    categories: list[str]
    path: str
    sha256: str

    @property
    def n_items(self) -> int:
        return len(self.items)

    def by_category(self, category: str) -> list[WordItem]:
        return [item for item in self.items if item.category == category]

    def labels(self) -> list[int]:
        return [item.label for item in self.items]

    def words(self) -> list[str]:
        return [item.word for item in self.items]

    def summary(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.category] = counts.get(item.category, 0) + 1
        return {
            "path": self.path,
            "sha256": self.sha256,
            "n_items": self.n_items,
            "categories": self.categories,
            "counts_per_category": counts,
            "balanced": len(set(counts.values())) == 1,
            "word_length": WORD_LENGTH,
        }


def load_dataset(path: str | Path, categories: Sequence[str] | None = None) -> Dataset:
    """Load and validate the word dataset.

    Raises ``DatasetError`` listing every offending row when constraints are
    violated, rather than failing on the first problem.
    """
    resolved = Path(path)
    if not resolved.exists():
        raise DatasetError(f"word dataset not found: {resolved}")

    with open(resolved, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()

    rows: list[tuple[int, str, str]] = []
    with open(resolved, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in ("word", "category") if c not in (reader.fieldnames or [])]
        if missing:
            raise DatasetError(
                f"{resolved}: missing required column(s) {missing}; "
                f"found {reader.fieldnames}"
            )
        for line_number, row in enumerate(reader, start=2):
            rows.append((line_number, (row.get("word") or "").strip(),
                         (row.get("category") or "").strip()))

    problems: list[str] = []
    normalized: list[tuple[int, str, str]] = []
    seen: dict[str, int] = {}
    for line_number, word, category in rows:
        upper = word.upper()
        if len(upper) != WORD_LENGTH:
            problems.append(f"line {line_number}: {word!r} is not {WORD_LENGTH} letters")
            continue
        if not upper.isalpha():
            problems.append(f"line {line_number}: {word!r} contains non-letters")
            continue
        if not upper.isascii():
            problems.append(f"line {line_number}: {word!r} is not ASCII")
            continue
        if upper in seen:
            problems.append(
                f"line {line_number}: {upper!r} duplicates line {seen[upper]}"
            )
            continue
        seen[upper] = line_number
        normalized.append((line_number, upper, category))

    if problems:
        listing = "\n  ".join(problems)
        raise DatasetError(
            f"{resolved}: dataset violates the word constraints\n  {listing}"
        )

    # Category order follows first appearance in the file, not alphabetical
    # order: label -> output-neuron indices depend on it.
    labels: list[str] = []
    for _, _, category in normalized:
        if category not in labels:
            labels.append(category)
    if categories is not None and list(labels) != list(categories):
        raise DatasetError(
            f"{resolved}: categories {labels} do not match encoding.categories "
            f"{list(categories)} (file order vs configured order); the "
            "label->output-neuron mapping requires a fixed, ordered category list"
        )
    if len(labels) != 4:
        raise DatasetError(
            f"{resolved}: expected exactly 4 categories, found {labels}"
        )

    counts: dict[str, int] = {}
    for _, _, category in normalized:
        counts[category] = counts.get(category, 0) + 1
    if len(set(counts.values())) != 1:
        raise DatasetError(
            f"{resolved}: categories are unbalanced, counts are {counts}"
        )

    index = {category: i for i, category in enumerate(labels)}
    items = [
        WordItem(word=word, category=category, label=index[category])
        for _, word, category in normalized
    ]
    return Dataset(
        items=items,
        categories=labels,
        path=str(resolved),
        sha256=digest,
    )


def curate_ambiguities(dataset: Dataset) -> list[str]:
    """Flag words whose category is arguably wrong, for manual review.

    Reports rather than edits: the dataset is a curated artifact and changing
    labels silently would break reproducibility of earlier runs.
    """
    notes: list[str] = []
    for item in dataset.items:
        if item.category == "food" and item.word in {"BEAR", "WOLF", "DEER", "FROG", "SNAK"}:
            notes.append(
                f"{item.word}: assigned to 'food' but is an animal; check the intent"
            )
    return notes


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

@lru_cache(maxsize=4)
def _font(name: str, size: int):
    from PIL import ImageFont

    with resources.as_file(
        resources.files("flyread").joinpath(f"assets/{name}")
    ) as font_path:
        return ImageFont.truetype(str(font_path), size)


def verify_font(config) -> dict[str, Any]:
    """Check the bundled font matches the pinned sha256."""
    name = config.get("encoding.font_file")
    with resources.as_file(
        resources.files("flyread").joinpath(f"assets/{name}")
    ) as font_path:
        if not font_path.exists():
            raise EncodingError(f"bundled font not found: {font_path}")
        data = font_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    expected = config.get("encoding.font_sha256")
    if config.get("encoding.verify_font") and expected and digest != expected:
        raise EncodingError(
            f"bundled font {name} sha256 mismatch\n"
            f"  expected: {expected}\n  actual:   {digest}"
        )
    return {"font_file": name, "sha256": digest, "bytes": len(data)}


@lru_cache(maxsize=8)
def _render_cached(
    word: str, size: tuple[int, int], font_name: str, font_size: int,
    spacing: int, invert: bool,
) -> np.ndarray:
    from PIL import Image, ImageDraw

    width, height = size
    image = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(image)
    font = _font(font_name, font_size)
    spacing = int(spacing)

    cell = (width - spacing * (len(word) + 1)) / len(word)
    if cell <= 0:
        raise EncodingError(
            f"layout does not fit: width={width}, spacing={spacing}, "
            f"letters={len(word)}"
        )
    for index, letter in enumerate(word):
        x0 = spacing + index * (cell + spacing)
        draw.text((x0, spacing), letter, fill=0, font=font)

    array = np.asarray(image, dtype=np.float64) / 255.0
    # array is 1.0 for white paper and 0.0 for black ink. Darkness is the
    # complement: 1 = black ink, 0 = white background.
    darkness = array if invert else 1.0 - array
    return np.ascontiguousarray(np.clip(darkness, 0.0, 1.0))


# Label axes for the learning target. The original `category` axis is
# semantic (animal / food / tool / place) and was measured to be unlearnable
# from rendered words: a supervised logistic regression trained on 60 words
# reached only 0.20-0.35 on 20 held-out words (chance 0.25) at 6x6, 16x16 and
# 32x32 resolution, and nearest-centroid accuracy was 0.100. Words within a
# category are no more visually similar than words across categories, so no
# learning rule can extract the structure because it is not in the stimulus.
#
# `ink_quartile` is a visually grounded replacement: label 0 is the lightest
# quarter of the dataset by total ink and label 3 the darkest. It is a global
# property, so classifying it requires spatial integration across the retina
# rather than reading a single photoreceptor. The same regression reaches
# 0.50-0.70 held-out (chance 0.25) at every resolution tried.
INK_QUARTILE_LABELS: tuple[str, ...] = ("ink_q1", "ink_q2", "ink_q3", "ink_q4")
LABEL_MODES: tuple[str, ...] = ("category", "ink_quartile")


def ink_density_map(dataset: Dataset, config) -> dict[str, float]:
    """Mean ink coverage per word, measured on the rendered image.

    Deliberately computed from the full-resolution image rather than from the
    pooled photoreceptor grid, so the label is a property of the stimulus alone
    and does not change when the simulation resolution does.
    """
    return {
        item.word: float(render_word(item.word, config).mean())
        for item in dataset.items
    }


def apply_label_mode(dataset: Dataset, config, mode: str = "category") -> Dataset:
    """Return `dataset` relabelled onto the requested visual label axis.

    Words, path, and digest are preserved so file verification and the
    train/test split continue to key off the same identity. Only the category
    name and integer label change.
    """
    if mode == "category":
        return dataset
    if mode != "ink_quartile":
        raise DatasetError(
            f"unknown encoding.label_mode {mode!r}; expected one of {list(LABEL_MODES)}"
        )
    density = ink_density_map(dataset, config)
    total = len(dataset.items)
    if total % len(INK_QUARTILE_LABELS) != 0:
        raise DatasetError(
            f"ink_quartile needs the word count divisible by "
            f"{len(INK_QUARTILE_LABELS)} to keep classes balanced, got {total}"
        )
    per_class = total // len(INK_QUARTILE_LABELS)
    # Ties broken by word so the labelling is deterministic and independent of
    # file order or dict iteration.
    order = sorted(density, key=lambda word: (density[word], word))
    label_of = {word: i // per_class for i, word in enumerate(order)}
    items = [
        WordItem(
            word=item.word,
            category=INK_QUARTILE_LABELS[label_of[item.word]],
            label=label_of[item.word],
        )
        for item in dataset.items
    ]
    return Dataset(
        items=items,
        categories=list(INK_QUARTILE_LABELS),
        path=dataset.path,
        sha256=dataset.sha256,
    )


def render_word(word: str, config) -> np.ndarray:
    """Render a word to a darkness image in [0, 1], shape (H, W).

    Deterministic: same word and configuration give pixel-identical output on
    every run, because the font is bundled, the layout is fixed arithmetic, and
    no platform text shaping is involved.
    """
    normalized = word.strip().upper()
    if len(normalized) != WORD_LENGTH:
        raise EncodingError(
            f"expected {WORD_LENGTH} letters, got {word!r}"
        )
    size = tuple(config.get("encoding.image_size_px"))
    return _render_cached(
        normalized,
        (int(size[0]), int(size[1])),
        config.get("encoding.font_file"),
        int(config.get("encoding.font_size_px")),
        int(config.get("encoding.letter_spacing_px")),
        False,
    )


# --------------------------------------------------------------------------
# grid-v1 mapping
# --------------------------------------------------------------------------

@dataclass
class GridMapping:
    """Pixel-to-photoreceptor assignment for scheme ``grid-v1``."""

    scheme_id: str
    rows: int
    cols: int
    image_height: int
    image_width: int
    permutation: np.ndarray | None = None
    seed: int | None = None

    @property
    def n_photoreceptors(self) -> int:
        return self.rows * self.cols

    def describe(self) -> dict[str, Any]:
        return {
            "scheme_id": self.scheme_id,
            "rows": self.rows,
            "cols": self.cols,
            "n_photoreceptors": self.n_photoreceptors,
            "image_size_px": [self.image_width, self.image_height],
            "shuffled": self.permutation is not None,
            "shuffle_seed": self.seed,
            "description": (
                "grid-v1: the rendered image is split into a rows x cols grid; "
                "each photoreceptor receives the mean darkness of its patch. "
                "This is a documented simplification of the ommatidial array, "
                "not a retinotopic model."
            ),
        }

    def _expected_shape(self) -> tuple[int, int]:
        return (self.image_height, self.image_width)

    def apply(self, darkness: np.ndarray) -> np.ndarray:
        """Mean darkness per photoreceptor, in row-major grid order."""
        expected = self._expected_shape()
        if darkness.shape != expected:
            raise EncodingError(
                f"image shape {darkness.shape} does not match the expected "
                f"{expected} for a {self.rows}x{self.cols} grid"
            )
        blocks = _block_means(darkness, self.rows, self.cols)
        flat = blocks.reshape(-1)
        if self.permutation is not None:
            flat = flat[self.permutation]
        return flat


def _block_means(darkness: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """Mean of each non-overlapping patch in a rows x cols grid.

    Uses area averaging rather than nearest-neighbour sampling so the encoding
    is continuous in darkness, which the monotonic-response scenario requires.
    """
    height, width = darkness.shape
    row_edges = np.linspace(0, height, rows + 1).astype(int)
    col_edges = np.linspace(0, width, cols + 1).astype(int)
    out = np.empty((rows, cols), dtype=np.float64)
    for r in range(rows):
        r0, r1 = row_edges[r], row_edges[r + 1]
        if r1 <= r0:
            r1 = r0 + 1
        for c in range(cols):
            c0, c1 = col_edges[c], col_edges[c + 1]
            if c1 <= c0:
                c1 = c0 + 1
            out[r, c] = darkness[r0:r1, c0:c1].mean()
    return out


def make_mapping(config, seed: int | None = None) -> GridMapping:
    """Build the photoreceptor mapping, optionally shuffled (control)."""
    rows = int(config.get("encoding.grid_rows"))
    cols = int(config.get("encoding.grid_cols"))
    size = config.get("encoding.image_size_px")
    mapping = GridMapping(
        scheme_id=config.get("encoding.scheme_id"),
        rows=rows,
        cols=cols,
        image_height=int(size[1]),
        image_width=int(size[0]),
    )
    if mapping.scheme_id != SCHEME_GRID_V1:
        raise EncodingError(
            f"unsupported mapping scheme {mapping.scheme_id!r}; "
            f"this version implements only {SCHEME_GRID_V1!r}"
        )
    if config.get("encoding.shuffle_pixels"):
        shuffle_seed = config.get("encoding.shuffle_seed")
        effective = int(seed if shuffle_seed is None else shuffle_seed)
        mapping.permutation = make_rng(
            effective, stream=f"shuffle-pixels:{rows}x{cols}"
        ).permutation(mapping.n_photoreceptors)
        mapping.seed = effective
        LOGGER.info("shuffled-pixel control enabled with seed %d", effective)
    return mapping


# --------------------------------------------------------------------------
# Rate and current conversion
# --------------------------------------------------------------------------

def darkness_to_rate(darkness: np.ndarray, config) -> np.ndarray:
    """Darkness in [0, 1] to firing rate in Hz.

    rate_hz = baseline_hz + (max_hz - baseline_hz) * darkness

    All-white input (darkness 0) yields exactly ``baseline_hz``; fully black
    yields ``max_hz``.
    """
    baseline = float(config.get("encoding.baseline_hz"))
    peak = float(config.get("encoding.max_hz"))
    darkness = np.clip(np.asarray(darkness, dtype=np.float64), 0.0, 1.0)
    return baseline + (peak - baseline) * darkness


def rate_to_current(rates_hz: np.ndarray, config) -> np.ndarray:
    """Firing rate in Hz to photoreceptor input current in nA.

    The current is derived by inverting the leaky integrate-and-fire
    rate-current relation for the configured cell, so the encoded rate is the
    rate the neuron actually reaches. For a constant current ``I`` the
    membrane settles at ``v_inf = I * tau`` and fires at

        f = 1 / (tau * ln(v_inf / (v_inf - v_threshold)))

    so inverting gives ``v_inf = e^k * v_threshold / (e^k - 1)`` with
    ``k = 1 / (f * tau)``, and ``I = (v_inf - v_rest) / tau``.

    A single scalar gain cannot do this. The earlier fixed gain of
    0.002 nA/Hz put ``v_inf`` near 0.003 against a threshold of 1.0, roughly
    360x too weak for a stimulated photoreceptor to reach threshold, so every
    stage fired at spontaneous-noise rates and the output collected 0-2 spikes
    per stimulus window. Any output then could not depend on the word, and no
    reward signal could exist to learn from.
    """
    rates = np.asarray(rates_hz, dtype=np.float64)
    tau_s = float(config.get("network.lif.tau_ms")) * 1e-3
    v_threshold = float(config.get("network.lif.v_threshold"))
    v_rest = float(config.get("network.lif.v_rest"))
    if tau_s <= 0.0:
        raise EncodingError(f"LIF tau must be positive, got {tau_s}")

    # Zero rate means no drive at all rather than an infinite negative current.
    positive = rates > 0.0
    out = np.zeros_like(rates)
    if not positive.any():
        return out
    # k diverges as the rate approaches zero, but v_inf -> v_threshold there,
    # so the current tends to the finite threshold current.
    k = 1.0 / np.maximum(rates[positive] * tau_s, 1e-12)
    exp_k = np.exp(np.minimum(k, 700.0))
    v_inf = exp_k * v_threshold / np.maximum(exp_k - 1.0, 1e-12)
    out[positive] = (v_inf - v_rest) / tau_s
    return out


def encode_word(
    word: str, mapping: GridMapping, config
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Encode a word into (darkness per PR, rate in Hz, current in nA)."""
    image = render_word(word, config)
    darkness = mapping.apply(image)
    rates = darkness_to_rate(darkness, config)
    currents = rate_to_current(rates, config)
    return darkness, rates, currents


# --------------------------------------------------------------------------
# Presentation timing
# --------------------------------------------------------------------------

@dataclass
class TrialTiming:
    """Presentation schedule for one trial, in ms."""

    stimulus_ms: float
    rest_ms: float
    dt_ms: float

    @property
    def trial_ms(self) -> float:
        return self.stimulus_ms + self.rest_ms

    def stimulus_steps(self) -> int:
        return max(1, int(round(self.stimulus_ms / self.dt_ms)))

    def rest_steps(self) -> int:
        return max(1, int(round(self.rest_ms / self.dt_ms)))

    def describe(self) -> dict[str, float]:
        return {
            "stimulus_ms": self.stimulus_ms,
            "rest_ms": self.rest_ms,
            "trial_ms": self.trial_ms,
            "dt_ms": self.dt_ms,
        }


def trial_timing(config) -> TrialTiming:
    return TrialTiming(
        stimulus_ms=float(config.get("encoding.stimulus_ms")),
        rest_ms=float(config.get("encoding.rest_ms")),
        dt_ms=float(config.get("simulation.dt")),
    )