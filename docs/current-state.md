# FlyWord: current state

*As of 19 October 2026, after the growth-matched control sweep
(`runs/recode-50-controls-merged`, 10 seeds).*

This page is the short, plain-language answer to "where does this project
stand?". The full evidence is in [paper.md](paper.md) (manuscript),
[final-report.md](final-report.md) (negative results and controls) and
[executive-report.md](executive-report.md) (summary). Where this page and an
archived run disagree, the run plus its manifest is authoritative.

## Bottom line

The real FlyWire olfactory subcircuit, driven at its physiological operating
point, does not learn the word-classification task: the class code is destroyed
at the `ALPN → Kenyon cell` transform, upstream of the plastic readout
(positive controls: input probe 1.000, Kenyon-cell probe 0.255). Raising that
transform's gain (a diagnostic "recode" to 50, with the calibration ceiling
widened to 100 Hz) restores the Kenyon-cell class code and the post-hoc
supervised decoder recovers the task.

The recode result does **not** show that the circuit learns online, and the
matched controls now show that the headline growth effect is a **decoder-capacity
effect**, not a learned, label-guided class prototype:

- The online MBON-argmax readout stays at chance (0.235–0.272) in every
  condition, with or without growth, dopamine, or training.
- Structural growth raises the supervised-decoder accuracy from 0.418
  (`trained`) to 0.750 (`structural_on`), a large paired gain (+0.37,
  *p* = 0.002). But that gain survives removal of **all** label guidance
  (`structural_unguided` 0.785, indistinguishable from `structural_on`,
  *p* = 0.75) and of category inheritance (`structural_random_category` 0.707,
  *p* = 0.61).
- Giving a **no-growth** decoder the same number of extra feature dimensions
  (300 random Kenyon-cell projections) reaches **0.983**, above every growth
  condition (*p* = 0.002 vs `trained`). The growth benefit is therefore best
  explained as expanded decoder access to the Kenyon-cell representation, not as
  a structure the network learned.
- Switching dopamine off does not hurt (0.828 vs 0.750, *p* = 0.084), so the
  dopamine-gated plasticity rule still contributes nothing measurable.

## Control-run results (gain-50 recode, 10 seeds, 600 trials)

`runs/recode-50-controls-merged`; chance 0.25; 95 % bootstrap CIs over seeds.
`online argmax` is the MBON-argmax readout (mean accuracy over training
presentations), the quantity that would test an online learner.

| condition | decoder acc | 95 % CI | online argmax |
|---|---|---|---|
| untrained | 0.330 | [0.265, 0.390] | 0.250 |
| trained | 0.418 | [0.367, 0.468] | 0.235 |
| structural_off | 0.378 | [0.340, 0.417] | 0.260 |
| **structural_on** | **0.750** | [0.672, 0.827] | 0.250 |
| structural_random_category (control) | 0.707 | [0.602, 0.802] | 0.272 |
| structural_unguided (control) | 0.785 | [0.692, 0.867] | 0.262 |
| dopamine_off | 0.828 | [0.720, 0.907] | 0.250 |
| dopamine_off_no_growth | 0.432 | [0.365, 0.497] | 0.250 |
| trained_extra_features (control) | **0.983** | [0.973, 0.992] | 0.248 |

Paired per-seed contrasts (Wilcoxon signed-rank, n = 10):

| contrast | Δ accuracy | *p* | reading |
|---|---|---|---|
| structural_on − structural_off | +0.372 | 0.002 | growth beats no growth |
| structural_on − trained | +0.332 | 0.002 | growth beats plain training |
| structural_on − structural_unguided | −0.035 | 0.75 | **label guidance not needed** |
| structural_on − structural_random_category | +0.043 | 0.61 | **category inheritance not needed** |
| structural_on − dopamine_off | −0.078 | 0.084 | dopamine off is not worse |
| trained − trained_extra_features | −0.565 | 0.002 | **extra decoder capacity dominates** |
| trained − untrained | +0.088 | 0.023 | small training increment over the floor |
| trained − structural_off | +0.040 | 0.24 | structure without growth ≈ trained |

## What is claimed, and what is not

**Claimed (robust):**

1. At the physiological operating point the class code is lost at the
   `ALPN → Kenyon cell` transform; the plastic readout and its credit rule are
   not the bottleneck (causal localisation with probes and a separable-task
   control).
2. The recode restores a linearly decodable Kenyon-cell class code and the
   post-hoc decoder recovers the task.
3. Structural growth and extra decoder features both improve *decoding*, and the
   improvement does not require dopamine, label guidance, or category
   inheritance.

**Not claimed:**

1. That the circuit learns online — the online argmax is at chance throughout.
2. That structural growth constructs or learns a class prototype — the unguided
   and random-category controls match it, and plain random features beat it.
3. That the recode reflects physiology — it runs at a 100 Hz calibration ceiling,
   far outside the 0.5–15 Hz band, and is a diagnostic intervention.

## Known methodological cautions

- **Run-order RNG sensitivity.** `trained` and `structural_off` share a config
  yet differ slightly (0.418 vs 0.378 here; 0.418 vs 0.435 in the main recode
  run), and `structural_on` is 0.750 here vs 0.833 in `recode-50-v2-merged`.
  Per-condition random state is not perfectly isolated, so only
  **internally-paired** contrasts within a single run are interpreted; absolute
  numbers vary across runs.
- **The `DAN → Kenyon cell` edges are extracted but not simulated** (backward
  stage pair); the reward is a global scalar gated by real DAN spikes.
- **The reported readout is a supervised post-hoc decoder**; the recode was run
  at a single gain (50) with matched controls.

## Reproduce

```bash
python -m pip install -r requirements.lock.txt && python -m pip install -e .
python -m flyread fetch-data
python -m flyread --config configs/odor-recode-50-controls.yaml \
    --run-id recode-50-controls sweep
```

Archived runs: `runs/odor-v1-merged` (physiological null),
`runs/pctrl-v1-merged` / `runs/pctrl-sep-merged` (probes, separable control),
`runs/recode-v2-merged` (gain 20), `runs/recode-50-v2-merged` (gain 50),
`runs/recode-50-controls-merged` (growth-matched controls).
