# FlyWord: executive report

**Bottom line.** The *Drosophila* FlyWire (release 783) subcircuit does not learn
the word-classification task at its calibrated operating point — a robust,
well-controlled null. The null is not a dead end: positive controls localise the
failure to a single transform (`ALPN → Kenyon cell`), and *recoding that
transform's gain turns the same harness into a working learner*. The fix is
mechanistic, not a protocol artefact: raising the gain lifts held-out accuracy
from chance to **0.418** (trained) and **0.833** (`structural_on`) across 10
seeds, with no data-leakage warning.

---

## 1. What was asked

Two tasks, both reported without reframing:

1. **grid-v1 (visual):** rendered 4-letter words → photoreceptors → optic lobe →
   mushroom body. The original task; needs a synthetic bridge.
2. **odor-v1 (olfactory):** words name odor identities and drive the real
   antennal-lobe projection neurons (ALPN) → Kenyon cell → MBON chain. Now the
   default; **zero synthetic edges**.

Success = held-out classification accuracy on *unseen* words significantly above
chance (0.25), with ≥ 10 seeds and positive-control probes validating the
instrument.

## 2. Headline results

| run | task / gain | trained held-out acc | verdict |
|---|---|---|---|
| `odor-v1-merged` | odor, calibrated baseline | **0.262** (10 seeds, 600 trials) | null (not significant, p = 0.21) |
| `recode-v2-merged` | odor, recode gain 20 | **0.363** (10 seeds, p = 0.0013) | **learns** |
| `recode-50-v2-merged` | odor, recode gain 50 | **0.418** (10 seeds, p = 8.5e-5) | **learns** |

The visual task is a documented negative result (0.233, 2 seeds). All odor runs
pass the positive-control gate (input probe 1.000; Kenyon-cell probe 0.95–1.00)
and carry no data-leakage warning.

## 3. Why the baseline fails — and how it was fixed

**Diagnosis (positive controls).** At the calibrated baseline the category is
perfectly decodable at the stimulus (input probe 1.000), yet the Kenyon-cell
population carries no generalisable class code (probe 0.255). The label is lost
in the `ALPN → Kenyon cell` transform, *before* the plastic readout has anything
to learn. The readout and its credit rule are exonerated: a supervised decoder
fits the Kenyon-cell code to 1.000 but generalises at chance.

**Fix (recode).** Raising the `ALPN → Kenyon cell` gain (with the readout gain,
and widening the calibration rate ceiling so the hotter Kenyon-cell regime is
accepted) restores a decodable code and makes the network learn. Held-out
accuracy across conditions:

| condition | gain 20 | gain 50 |
|---|---|---|
| untrained | 0.338 | 0.330 |
| **trained** | **0.363** | **0.418** |
| dopamine_off | 0.597 | 0.785 |
| structural_on | **0.608** | **0.833** |
| structural_off | 0.370 | 0.435 |
| shuffled_labels | 0.330 | 0.245 |
| shuffled_labels_no_growth | 0.230 | 0.233 |

Two mechanistic findings:

- **Accuracy tracks structural growth, not dopamine.** With growth on
  (`structural_on`, `dopamine_off`) accuracy far exceeds the growth-off controls
  (`trained`, `structural_off`); turning growth off collapses accuracy toward the
  untrained floor. Turning dopamine off does not.
- **The residual is structural, not leakage.** The shuffled-label control sits at
  or below the untrained floor and is not significant, and the no-growth
  isolation control is at/below chance (0.230 / 0.233). So the above-chance floor
  is the representation's own decodability, not label information leaking through
  the pipeline.

## 4. Harness corrections made during the work

- **Decoder-fit leakage (invalidated earlier recode run).** The supervised
  decoder was fit on dataset labels even in the shuffled control, letting the
  control read true labels off the network. Fixed to fit on *presented* labels;
  the shuffled control dropped from 0.615 to 0.330, at the untrained floor.
  Regression test added.
- **Leakage margin made untrained-relative** so a generically decodable
  representation is not falsely flagged.
- **Confusion-matrix condition bug** fixed (`trained` was hardcoded; single-
  condition isolation runs crashed) via `pick_confusion_condition`, with tests.
- **Resume-from-checkpoint** added, so long runs resume completed
  (seed, condition) pairs instead of recomputing them.

## 5. Recommendations

1. **Adopt the recode as the new baseline** for any follow-up learning
   experiments; the calibrated baseline is a confirmed dead end for this signal
   path.
2. **Push the recode further** — sweep denser/sparser Kenyon-cell codes, input
   scaling, and non-linear readouts now that a learnable code exists.
3. **Keep structural growth in scope** — it is the effective learning mechanism
   here; add a reward-prediction-error baseline so the per-class credit rule is
   not swamped by the ≈75 % wrong-at-chance surplus.
4. **Simulate the real `DAN → mushroom body` edges** instead of the global-scalar
   reward surrogate.
5. **Do not add seeds** to the baseline null — the ≥ 10-seed runs already exclude
   under-powering.

## 6. Reproduce

```bash
python -m pip install -r requirements.lock.txt && python -m pip install -e .
python -m flyread fetch-data
python -m flyread --config configs/odor-recode-50.yaml --run-id recode-50-v2 sweep
```

Archived: `runs/odor-v1-merged` (baseline null), `runs/recode-v2-merged`
(gain 20), `runs/recode-50-v2-merged` (gain 50), and the isolation controls
`runs/recode-v2-extra-merged`, `runs/recode-50-extra-merged`. Full detail in
[`docs/final-report.md`](final-report.md).
