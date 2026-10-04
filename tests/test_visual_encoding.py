import sys
from pathlib import Path

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
