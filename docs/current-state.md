# FlyWord: current state

*As of 19 October 2026, after the growth-matched control sweep
(`runs/recode-50-controls-merged`, 10 seeds) and the manuscript revision
(necessary-not-sufficient reframing, RNG isolation, unified statistics).*

This page is the short, plain-language answer to "where does this project
stand?". The full evidence is in [paper.md](paper.md) (manuscript),
[final-report.md](final-report.md) (negative results and controls) and
[executive-report.md](executive-report.md) (summary). Where this page and an
archived run disagree, the run plus its manifest is authoritative.

## Bottom line

The real FlyWire olfactory subcircuit, driven at its physiological operating
point, does not learn the word-classification task: the class code is destroyed
at the `ALPN → Kenyon cell` transform, upstream of the plastic readout
(positive controls: input probe 1.000, Kenyon-cell probe CV 0.236 / held-out
0.255). Raising that transform's gain (a diagnostic "recode" to 20 or 50, with
the calibration ceiling widened to 100 Hz) restores the Kenyon-cell class code
(probe CV 0.950 / 1.000) and raises the post-hoc supervised decoder.

**The recode is necessary but not sufficient.** The online MBON-argmax readout —
the only readout that can express what the plastic synapses learned — stays at
chance (0.235–0.272) in every condition measured, including at gain 50 where the
Kenyon-cell probe is 1.000. Restoring the code does not restore the learner: the
four-dimensional MBON readout and its credit rule are a second, unresolved
bottleneck. This is the central correction to the earlier draft, which said the
network "then learns".

After correction for multiple comparisons, the plain trained condition is not
significantly above the untrained floor: gain 20 +0.025 (Holm *p* = 1.0), gain 50
+0.088 (Holm *p* = 0.117). The matched controls also show that the headline
growth effect is a **decoder-capacity effect**, not a learned, label-guided class
prototype:

- Structural growth raises the supervised-decoder accuracy from 0.418
  (`trained`) to 0.750 (`structural_on`), a large paired gain (+0.37,
  Holm *p* = 0.018). But that gain survives removal of **all** label guidance
  (`structural_unguided` 0.785, Holm *p* = 1.0) and of category inheritance
  (`structural_random_category` 0.707, Holm *p* = 1.0).
- Giving a **no-growth** decoder the same number of extra feature dimensions
  (300 random Kenyon-cell projections) reaches **0.983**, above every growth
  condition (Holm *p* = 0.018 vs `trained`). The growth benefit is therefore best
  explained as expanded decoder access to the Kenyon-cell representation, not as
  a structure the network learned. Since the KC probe is ≈ 1.000, 0.983 is close
  to the 300-feature linear ceiling; it is a capacity sanity check, not an
  independent discovery.
- Switching dopamine off does not change decoded accuracy detectably (0.828 vs
  0.750, Holm *p* = 0.34). This is a **null result**, not evidence that
  plasticity is absent: the decoder is refit and can undo weight changes, and the
  online readout is the instrument that can see them (it is at chance).

## Control-run results (gain-50 recode, 10 seeds, 600 trials)

`runs/recode-50-controls-merged`; chance 0.25; 95 % percentile-bootstrap CIs over
seeds. `online argmax` is the MBON-argmax readout (mean accuracy over training
presentations), the quantity that would test an online learner.

| condition | decoder acc | 95 % CI | online argmax |
|---|---|---|---|
| untrained | 0.330 | [0.263, 0.392] | 0.250 |
| trained | 0.418 | [0.365, 0.467] | 0.235 |
| structural_off | 0.378 | [0.340, 0.417] | 0.260 |
| **structural_on** | **0.750** | [0.672, 0.828] | 0.250 |
| structural_random_category (control) | 0.707 | [0.603, 0.802] | 0.272 |
| structural_unguided (control) | 0.785 | [0.693, 0.867] | 0.262 |
| dopamine_off | 0.828 | [0.722, 0.907] | 0.250 |
| dopamine_off_no_growth | 0.432 | [0.363, 0.493] | 0.250 |
| trained_extra_features (control) | **0.983** | [0.973, 0.992] | 0.248 |

Paired per-seed contrasts (two-sided Wilcoxon, n = 10, Holm-corrected across the
pre-specified 11-pair family):

| contrast | Δ accuracy | *p* (Holm) | reading |
|---|---|---|---|
| structural_on − structural_off | +0.372 | 0.018 | growth beats no growth |
| structural_on − trained | +0.332 | 0.018 | growth beats plain training |
| structural_on − structural_unguided | −0.035 | 1.0 | **label guidance not needed** |
| structural_on − structural_random_category | +0.043 | 1.0 | **category inheritance not needed** |
| structural_on − dopamine_off | −0.078 | 0.34 | dopamine off is not worse |
| trained − trained_extra_features | −0.565 | 0.018 | **extra decoder capacity dominates** |
| trained − untrained | +0.088 | 0.117 | no significant training increment |
| trained − structural_off | +0.040 | 0.71 | identical configs differ by RNG order |
| dopamine_off − dopamine_off_no_growth | +0.397 | 0.023 | growth effect within dopamine-off |

## What is claimed, and what is not

**Claimed (robust):**

1. At the physiological operating point the class code is lost at the
   `ALPN → Kenyon cell` transform in this calibrated regime. The plastic readout
   and its credit rule are **not** shown to be the only bottleneck: restoring the
   transform leaves the online readout at chance, so the readout is a second
   bottleneck.
2. The recode restores a linearly decodable Kenyon-cell class code (probe
   0.950–1.000) and raises the post-hoc supervised decoder.
3. Structural growth and extra decoder features both improve *decoding*, and the
   improvement does not require dopamine, label guidance, or category
   inheritance; it is a decoder-capacity effect.
4. The shuffled-label control is judged by a sign-flip permutation test against
   chance, not against a moved untrained floor. At gain 20 the growth-on shuffled
   control is above chance (0.330, *p* = 0.020) while the no-growth control is not
   (0.230, *p* = 0.76); this is a residual anomaly consistent with growth adding
   generically decodable features, not exculpated.

**Not claimed:**

1. That the circuit learns online — the online argmax is at chance throughout.
2. That the recode is sufficient — it is necessary, not sufficient.
3. That structural growth constructs or learns a class prototype — the unguided
   and random-category controls match it, and plain random features beat it.
4. That the recode reflects physiology — it runs at a 100 Hz calibration ceiling,
   far outside the 0.5–15 Hz band, and is a diagnostic intervention.
5. That the transform is *the* mechanism rather than a gain/SNR confound — the
   separating diagnostics (spontaneous vs evoked KC rates, sparseness) are
   implemented but not in the archived runs.

## Known methodological cautions

- **Archived numbers predate the RNG-isolation fix.** `trained` and
  `structural_off` share a config yet differ in the archives (0.418 vs 0.378
  here; 0.418 vs 0.435 in the main recode run), and `structural_on` is 0.750 here
  vs 0.833 in `recode-50-v2-merged`. The fix seeds each condition and probe pass
  independently and was validated on a smoke run (`trained` == `structural_off`
  exactly). Only **internally-paired** contrasts are interpreted; absolute
  numbers are provisional until the re-run.
- **Statistics.** One α = 0.05 plan: one-sided Wilcoxon vs chance with Holm
  across conditions; two-sided paired Wilcoxon contrasts with Holm across a
  pre-specified 11-pair family; sign-flip permutation for leakage. At n = 10 the
  exact two-sided Wilcoxon floor is 0.00195. There are 20 unique held-out tokens,
  so accuracy steps are 0.05.
- **The shuffled-control rule changed.** The earlier draft raised the leakage
  threshold to the untrained floor after the gain-20 control failed. That
  exemption was removed as a goalpost move; the control is now tested against
  chance directly.
- **The `DAN → Kenyon cell` edges are extracted but not simulated** (backward
  stage pair); the reward is a global scalar gated by real DAN spikes.
- **The reported readout is a supervised post-hoc decoder.** The online readout
  is the learning instrument and is at chance. The decoder refit can hide weight
  changes, so "no dopamine effect" means no detectable effect on decoded
  accuracy.
- **The `trained_pretrained` anomaly is unexplained** (0.512 at gain 20, 0.340 at
  gain 50). The previously reported 0.351 online argmax for this condition is not
  in any archived result and is withdrawn.

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
`runs/recode-50-controls-merged` (growth-matched controls),
`runs/recode-v2-extra-merged` / `runs/recode-50-extra-merged` (shuffled no-growth
isolation controls). These directories are gitignored and await Zenodo deposit;
see `CITATION.cff`.
