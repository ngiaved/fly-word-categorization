import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import encoding, config


def _odor_config():
    return config.Config.load("configs/default.yaml", overrides=[
        "evaluation.n_seeds=1",
    ])


def _dataset(cfg):
    return encoding.apply_label_mode(
        encoding.load_dataset(cfg.get("data.words_csv"), cfg.get("encoding.categories")),
        cfg,
        str(cfg.get("encoding.label_mode")),
    )


def test_default_config_uses_odor_scheme():
    cfg = _odor_config()
    assert cfg.get("encoding.scheme_id") == "odor-v1"
    odor = cfg.get("encoding.odor")
    assert int(odor["n_inputs"]) > int(odor["active_from_own"]) + int(odor["active_elsewhere"])
    cfg.validate()


def test_odor_mapping_is_deterministic_per_word_and_sparse():
    cfg = _odor_config()
    dataset = _dataset(cfg)
    mapping = encoding.make_mapping(cfg, seed=1000, n_inputs=673, dataset=dataset)
    first = mapping.stimulus("beer", cfg)
    again = mapping.stimulus("beer", cfg)
    other = mapping.stimulus("bird", cfg)
    assert np.array_equal(first, again)
    assert not np.array_equal(first, other)
    active = first > 0
    expected_active = int(cfg.get("encoding.odor.active_from_own")) + int(
        cfg.get("encoding.odor.active_elsewhere")
    )
    assert int(active.sum()) == expected_active
    assert np.all(first >= 0.0)
    assert np.all(first[active] >= float(cfg.get("encoding.odor.min_drive")))
    assert np.all(first[active] <= float(cfg.get("encoding.odor.max_drive")) + 1e-12)


def test_odor_categories_share_receptor_subsets_and_cross_overlap_is_low():
    # The category structure is the point of the encoding: words of the same
    # category must share far more active receptors than words across
    # categories, otherwise the stimulus itself carries no class signal.
    cfg = _odor_config()
    dataset = _dataset(cfg)
    mapping = encoding.make_mapping(cfg, seed=1000, n_inputs=673, dataset=dataset)
    by_word = {item.word: (mapping.stimulus(item.word, cfg) > 0) for item in dataset.items}
    within = []
    across = []
    for a in dataset.items:
        for b in dataset.items:
            if a.word >= b.word:
                continue
            overlap = float((by_word[a.word] & by_word[b.word]).sum())
            if a.label == b.label:
                within.append(overlap)
            else:
                across.append(overlap)
    own = float(cfg.get("encoding.odor.active_from_own"))
    elsewhere = float(cfg.get("encoding.odor.active_elsewhere"))
    # Words of one category draw from a shared 168-neuron prototype, so pairs
    # within a category share ~|own|^2/168 active receptors, while cross pairs
    # share at most the elsewhere budget. That separation is the point of the
    # encoding: the class structure must exist in the stimulus itself.
    assert np.median(within) >= np.median(across) + 4.0
    assert float(max(across)) <= 2.0 * elsewhere
    assert np.median(within) >= 0.25 * own


def test_odor_mapping_length_override_matches_input_stage():
    # Every mapping input must drive a simulated input neuron: run_condition
    # passes the actually selected stage size, which can differ from the cap
    # when outliers are excluded (673 vs the 685 cap here).
    cfg = _odor_config()
    dataset = _dataset(cfg)
    mapping = encoding.make_mapping(cfg, seed=1000, n_inputs=673, dataset=dataset)
    assert mapping.n_inputs == 673
    assert mapping.stimulus("beer", cfg).size == 673


def test_odor_mapping_manifest_record():
    from flyread.repro import RunManifest

    cfg = _odor_config()
    manifest = RunManifest(run_id="t", seed=1000, config={})
    manifest.encoding["mapping"] = encoding.make_mapping(cfg, seed=1000).describe()
    recorded = manifest.encoding["mapping"]
    assert recorded["scheme_id"] == "odor-v1"
    assert recorded["n_inputs"] == 685
    assert "seed" in recorded