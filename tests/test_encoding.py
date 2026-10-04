import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from flyread import encoding, config


def test_dataset_validator():
    # Scenario: dataset validator (4 letters, no duplicates, balanced categories)
    cfg = config.Config.load("configs/smoke.yaml")
    ds = encoding.load_dataset(cfg.get("data.words_csv"), cfg.get("encoding.categories"))
    s = ds.summary()
    assert len(s["categories"]) == 4
    assert all(len(w) == s["word_length"] for w in ds.words())
