import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import encoding, config


def test_deterministic_rendering():
    cfg = config.Config.load("configs/smoke.yaml")
    ds = encoding.load_dataset(cfg.get("data.words_csv"), cfg.get("encoding.categories"))
    item = ds.items[0]
    grid1 = encoding.render_word(item.word, cfg)
    grid2 = encoding.render_word(item.word, cfg)
    import numpy as np
    assert np.allclose(grid1, grid2)


def test_manifest_records_mapping_scheme_and_shuffle_seed():
    # Requirement: Versioned photoreceptor mapping -- the scheme id must be in
    # the run manifest. Requirement: Shuffled-pixel control -- the permutation
    # seed must be in the manifest too.
    from flyread.repro import RunManifest

    cfg = config.Config.load("configs/smoke.yaml", overrides=[
        "encoding.shuffle_pixels=true",
    ])
    manifest = RunManifest(run_id="t", seed=123, config={})
    manifest.encoding["mapping"] = encoding.make_mapping(cfg, seed=123).describe()

    mapping = manifest.encoding["mapping"]
    assert mapping["scheme_id"] == "grid-v1"
    assert mapping["shuffled"] is True
    assert mapping["shuffle_seed"] == 123
    # and it must survive serialisation
    round_tripped = RunManifest(**{"run_id": "t", "seed": 1, "config": {}})
    round_tripped.encoding = manifest.encoding
    assert round_tripped.to_dict()["encoding"]["mapping"]["shuffle_seed"] == 123


def test_all_white_image_gives_baseline_rate():
    # Scenario: Zero input -- an all-white image must give baseline input,
    # not the peak rate.
    cfg = config.Config.load("configs/smoke.yaml")
    mapping = encoding.make_mapping(cfg, seed=1)
    size = tuple(cfg.get("encoding.image_size_px"))
    white = np.zeros((size[1], size[0]))  # darkness 0 == white paper
    rates = encoding.darkness_to_rate(mapping.apply(white), cfg)
    assert np.allclose(rates, float(cfg.get("encoding.baseline_hz")))


def test_darker_image_gives_greater_or_equal_rate():
    # Scenario: Monotonic response
    cfg = config.Config.load("configs/smoke.yaml")
    mapping = encoding.make_mapping(cfg, seed=1)
    full = encoding.render_word("ANML", cfg)
    half = full * 0.5
    r_full = encoding.darkness_to_rate(mapping.apply(full), cfg)
    r_half = encoding.darkness_to_rate(mapping.apply(half), cfg)
    assert np.all(r_half <= r_full + 1e-12)
