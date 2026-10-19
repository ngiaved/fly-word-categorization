# FlyWord: executive report

**Bottom line.** The *Drosophila* FlyWire (release 783) subcircuit does not learn
the word-classification task at its calibrated operating point — a robust,
well-controlled null. The null is not a dead end: positive controls localise the
failure to a single transform (`ALPN → Kenyon cell`), and *recoding that
transform's gain makes the Kenyon-cell class code decodable*, lifting post-hoc
decoder held-out accuracy above chance to **0.418** (trained) and as high as
**0.983** with extra features, across 10 seeds, with no data-leakage warning.
The growth-matched controls, now run, show that **the circuit does not learn
online**: the MBON-argmax readout stays at chance, the "structural growth"
headline is a decoder-capacity effect (unchanged when label guidance is removed,
beaten by random features with no growth), and the dopamine rule contributes
nothing. See [current-state.md](current-state.md).

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
| `recode-v2-merged` | odor, recode gain 20 | **0.363** (10 seeds, p = 0.0013) | above chance |
| `recode-50-v2-merged` | odor, recode gain 50 | **0.418** (10 seeds, p = 8.5e-5) | above chance |
| `recode-50-controls-merged` | odor, gain 50 + growth controls | **0.418** trained; **0.750** growth; **0.983** random features | decoder capacity, not learning |

The visual task is a documented negative result (0.233, 2 seeds). All odor runs
pass the positive-control gate (input probe 1.000; Kenyon-cell probe 0.95–1.00)
and carry no data-leakage warning. In every run the **online MBON-argmax readout
stays at chance (≈0.25)**, so all reported accuracies are supervised
post-hoc-decoder representation quality, not online learning.

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

Three mechanistic findings:

- **Accuracy tracks structural growth, not dopamine.** With growth on
  (`structural_on`, `dopamine_off`) accuracy far exceeds the growth-off controls
  (`trained`, `structural_off`); turning growth off collapses accuracy toward the
  untrained floor. Turning dopamine off does not (`recode-50-controls-merged`:
  `dopamine_off` 0.828 vs `structural_on` 0.750, p = 0.084).
- **The growth gain is decoder capacity, not a learned prototype.** The
  growth-matched controls (10 seeds) show the gain is statistically unchanged when
  all label guidance is removed (`structural_unguided` 0.785, p = 0.75) or
  category inheritance is broken (`structural_random_category` 0.707, p = 0.61),
  and is exceeded by a no-growth decoder given 300 random Kenyon-cell projections
  (0.983, p = 0.002 vs trained). Growth adds the same number of decoder feature
  dimensions, so it improves access to an already-decodable code rather than
  learning one.
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

1. **Treat the recode as a diagnostic probe, not as evidence of learning.** It
   localises the bottleneck to the `ALPN → Kenyon cell` transform, but every
   score is a post-hoc supervised decoder over the representation, and the online
   MBON-argmax readout never leaves chance.
2. **Do not report the growth numbers as learning.** The growth-matched controls
   show the growth gain is decoder capacity: it survives removal of label
   guidance and is beaten by random features with no growth. Report
   `trained_extra_features` (0.983) alongside any growth number as the
   capacity reference.
3. **Build an online-learning benchmark before claiming the circuit learns.**
   The MBON-argmax readout must beat the untrained floor online; a
   reward-prediction-error baseline would remove the ≈75 % wrong-at-chance
   surplus that swamps the per-class credit rule.
4. **Simulate the real `DAN → mushroom body` edges** instead of the
   global-scalar reward surrogate (backward stage pair currently dropped).
5. **Do not add seeds** to the baseline null — the ≥ 10-seed runs already exclude
   under-powering.

## 6. Reproduce

```bash
python -m pip install -r requirements.lock.txt && python -m pip install -e .
python -m flyread fetch-data
python -m flyread --config configs/odor-recode-50.yaml --run-id recode-50-v2 sweep
python -m flyread --config configs/odor-recode-50-controls.yaml \
    --run-id recode-50-controls sweep
```

Archived: `runs/odor-v1-merged` (baseline null), `runs/recode-v2-merged`
(gain 20), `runs/recode-50-v2-merged` (gain 50), the growth-matched controls
`runs/recode-50-controls-merged`, and the isolation controls
`runs/recode-v2-extra-merged`, `runs/recode-50-extra-merged`. Full detail in
[`docs/final-report.md`](final-report.md) and
[`docs/current-state.md`](current-state.md).
