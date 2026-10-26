# FlyWord: current state

*As of 26 October 2026. The RNG-isolated recode and growth-control sweeps are
complete; the physiological-baseline re-run is paused and resumable. This page
is the short, plain-language answer to "where does this project stand?". The
full evidence is in [paper.md](paper.md), [final-report.md](final-report.md)
(negative results and controls) and [executive-report.md](executive-report.md)
(summary). Where this page and an archived run disagree, the run plus its
manifest is authoritative.*

## Bottom line

The real FlyWire olfactory subcircuit, driven at its physiological operating
point, does not learn the word-classification task: the class code is destroyed
at the `ALPN → Kenyon cell` transform, upstream of the plastic readout (input
probe 1.000; Kenyon-cell probe CV 0.236 / held-out 0.255). Raising that
transform's gain (a diagnostic "recode" to 20 or 50, with the calibration
ceiling widened to 100 Hz) restores the Kenyon-cell class code (probe CV 0.956
at gain 20, 1.000 at gain 50) and raises the post-hoc supervised decoder.

**The recode is necessary but not sufficient.** The online MBON-argmax readout
— the network's own class decision on held-out words, with no supervised
decoder — stays at chance (0.232–0.250 in the main recode runs; 0.245–0.268
across the nine growth-control conditions), including at gain 50 where the
Kenyon-cell probe is 1.000. Restoring the code does not restore the learner: the
four-dimensional MBON readout and its credit rule are a second, unresolved
bottleneck. A fast, underpowered reward-prediction-error diagnostic also stayed
at chance (0.250 on three seeds), so scalar surprise gating alone is not an
obvious rescue.

After correction for multiple comparisons, the plain trained condition is not
significantly above its paired untrained floor: gain 20 +0.045 (Holm
*p* = 1.0), gain 50 +0.070 (Holm *p* = 0.39). It *is* significantly above 0.25
chance in the recoded runs, but the untrained recoded network is also above
chance, so chance alone is not the right null. The matched controls show that
the large growth effect is a **decoder-capacity effect**, not a learned,
label-guided class prototype.

## Final recode results (RNG-isolated, 10 seeds, 600 trials)

`runs/recode-v2-rng-merged` (gain 20) and `runs/recode-50-v2-rng-merged` (gain
50); chance 0.25; 95 % percentile-bootstrap CIs over seeds. `online argmax` is
the network's own argmax on held-out words, before any supervised decoder.

| condition | gain 20 acc | 95 % CI | gain 50 acc | 95 % CI | online 20 | online 50 |
|---|---|---|---|---|---|---|
| untrained | 0.338 | [0.275, 0.393] | 0.330 | [0.265, 0.390] | 0.248 | 0.250 |
| trained | **0.383** | [0.323, 0.437] | **0.400** | [0.365, 0.433] | **0.232** | **0.245** |
| structural_off (≡ trained) | 0.383 | [0.323, 0.437] | 0.400 | [0.365, 0.433] | 0.232 | 0.245 |
| shuffled_labels | 0.285 | [0.232, 0.332] | 0.253 | [0.222, 0.287] | 0.250 | 0.250 |
| dopamine_off | 0.585 | [0.522, 0.645] | 0.855 | [0.807, 0.893] | 0.253 | 0.250 |
| dopamine_off_no_growth | 0.365 | [0.310, 0.423] | 0.388 | [0.357, 0.425] | 0.250 | 0.250 |
| trained_pretrained | 0.533 | [0.482, 0.583] | 0.318 | [0.277, 0.360] | 0.420 | 0.325 |
| structural_on | 0.573 | [0.507, 0.635] | 0.813 | [0.727, 0.890] | 0.250 | 0.250 |

Paired per-seed contrasts (two-sided Wilcoxon, n = 10, Holm-corrected):

| contrast | Δ gain 20 | *p* (Holm) | Δ gain 50 | *p* (Holm) | reading |
|---|---|---|---|---|---|
| trained − untrained | +0.045 | 1.0 | +0.070 | 0.3906 | no significant training increment |
| structural_on − structural_off | +0.190 | 0.0352 | +0.413 | 0.0156 | growth beats no growth |
| structural_on − trained | +0.190 | 0.0352 | +0.413 | 0.0156 | growth beats plain training |
| structural_on − dopamine_off | −0.012 | 1.0 | −0.042 | 1.0 | dopamine off is not worse |
| dopamine_off − dopamine_off_no_growth | +0.220 | 0.0312 | +0.467 | 0.0156 | growth effect within dopamine-off |
| trained − structural_off | 0.000 | 1.0 | 0.000 | 1.0 | RNG isolation confirmed |
| trained_pretrained − untrained | +0.195 | 0.0312 | −0.012 | 1.0 | supervised init only, not learning |
| shuffled_labels − untrained | −0.053 | 1.0 | −0.077 | 0.3711 | no leakage |

The shuffled-label control is now at chance under the sign-flip permutation
test: gain 20 mean 0.285, *p* = 0.122; gain 50 mean 0.253, *p* = 0.463. The
earlier reported gain-20 anomaly (0.330, *p* = 0.020) does **not** reproduce
under RNG isolation. The no-growth isolation controls are 0.230 (gain 20,
*p* = 0.75) and 0.233 (gain 50, *p* = 0.72).

## Growth-matched controls (gain 50, 10 seeds)

`runs/recode-50-controls-rng-merged`; same run family as above. The point of
these controls is to separate growth from decoder capacity.

| condition | decoder acc | online argmax |
|---|---|---|
| untrained | 0.330 | 0.250 |
| trained | 0.400 | 0.245 |
| structural_off | 0.400 | 0.245 |
| **structural_on** | **0.813** | 0.250 |
| structural_random_category (control) | 0.652 | 0.255 |
| structural_unguided (control) | 0.798 | 0.268 |
| dopamine_off | 0.855 | 0.250 |
| dopamine_off_no_growth | 0.388 | 0.250 |
| trained_extra_features (control) | **0.990** | 0.245 |

Paired contrasts:

| contrast | Δ accuracy | *p* (Holm) | reading |
|---|---|---|---|
| structural_on − structural_off | +0.413 | 0.0176 | growth beats no growth |
| structural_on − trained | +0.413 | 0.0176 | growth beats plain training |
| structural_on − structural_unguided | +0.015 | 1.0 | **label guidance not needed** |
| structural_on − structural_random_category | +0.162 | 0.4883 | category inheritance not needed |
| structural_on − dopamine_off | −0.042 | 1.0 | dopamine off is not worse |
| trained − trained_extra_features | −0.590 | 0.0176 | **extra decoder capacity dominates** |
| trained − untrained | +0.070 | 0.4883 | no significant training increment |
| trained − structural_off | 0.000 | 1.0 | RNG isolation confirmed |
| dopamine_off − dopamine_off_no_growth | +0.467 | 0.0176 | growth effect within dopamine-off |

Three readings:

- **Label guidance is not the mechanism.** Removing all reward/label guidance
  leaves accuracy statistically unchanged (`structural_unguided` 0.798 vs
  `structural_on` 0.813, Δ = +0.015, Holm *p* = 1.0).
- **Category inheritance is not the mechanism.** Random category assignment
  lowers the mean to 0.652, but the contrast is not significant (Δ = +0.162,
  Holm *p* = 0.49) and the unguided control is unaffected.
- **Decoder capacity explains it.** A no-growth decoder given 300 random
  Kenyon-cell projections reaches **0.990**, above every growth condition and
  0.590 above `trained` (Holm *p* = 0.018). With the Kenyon-cell probe at 1.000,
  this is close to the 300-feature linear ceiling. Growth improves access to an
  already decodable code; it does not learn one.

## Fast RPE diagnostic (3 seeds, not for reported accuracy)

`runs/fast-online-rpe-merged`; `configs/fast-online-rpe.yaml`; gain-50 recode,
`dt` 0.5 ms, 240 trials, 200 ms stimulus + 100 ms rest, argmax readout, probes
and growth off. This is a triage run on a cheaper grid, not a formal sweep, so
it can detect a large rescue but not a small one.

| condition | online argmax | 95 % CI | train acc |
|---|---|---|---|
| untrained | 0.250 | [0.250, 0.250] | — |
| trained | 0.233 | [0.150, 0.300] | 0.237 |
| online_rpe | 0.250 | [0.250, 0.250] | 0.251 |

The RPE-gated per-class credit rule is at chance on all three seeds (0.250,
0.250, 0.250) and collapses to a near-constant prediction, like the untrained
network. Its training accuracy (0.251) is only 0.014 above `trained` and still at
the four-way floor. Scalar-surprise gating alone did not open the online
bottleneck; a formal RPE sweep is therefore low priority. Its absolute numbers
are not used in the headline claims.

## What is claimed, and what is not

**Claimed (robust):**

1. At the physiological operating point the class code is lost at the
   `ALPN → Kenyon cell` transform in this calibrated regime. The plastic readout
   and its credit rule are **not** shown to be the only bottleneck: restoring
   the transform leaves the online readout at chance, so the readout is a
   second bottleneck.
2. The recode restores a linearly decodable Kenyon-cell class code (probe CV
   0.956–1.000) and raises the post-hoc supervised decoder.
3. Structural growth and extra decoder features both improve *decoding*, and the
   improvement does not require dopamine, label guidance, or category
   inheritance; it is a decoder-capacity effect.
4. The RNG-isolated shuffled-label controls are at chance, so the earlier
   gain-20 leakage anomaly is resolved.
5. Supervised pretraining puts the online argmax above chance (0.420 at gain 20,
   0.325 at gain 50). This is a positive control showing the 4-dimensional
   readout *can* express the code when its weights start in the right place; it
   is not evidence that the dopamine rule learns it.
6. A fast RPE-gated variant did not rescue the online readout (0.250 on three
   seeds). This is a triage result, not a formal negative.

**Not claimed:**

1. That the circuit learns online — the online argmax is at chance throughout
   the unsupervised/trained conditions.
2. That the recode is sufficient — it is necessary, not sufficient.
3. That structural growth constructs or learns a class prototype — the unguided
   and random-category controls match it, and plain random features beat it.
4. That the recode reflects physiology — it runs at a 100 Hz calibration
   ceiling, far outside the 0.5–15 Hz band, and is a diagnostic intervention.
5. That the transform is *the* mechanism rather than a gain/SNR confound — the
   separating diagnostics (spontaneous vs evoked KC rates, sparseness) are
   implemented but not in the archived runs.

## Known methodological cautions

- **The recode and control numbers are RNG-isolated and final.** Each condition
  and probe pass calls `seed_everything(seed)` at entry, so condition order
  cannot affect a condition's random stream. `trained` and `structural_off`
  (identical configs) now have a paired difference of exactly 0.000.
- **The physiological baseline re-run is paused.** `runs/odor-v1-rng-*` was
  stopped mid-run to free compute; it is resumable with
  `scripts/run_final_sweeps.sh`. The archived baseline `runs/odor-v1-merged`
  remains the physiological reference and predates the RNG fix.
- **"Online argmax" is held-out accuracy, not training accuracy.** It is the
  network's own winner-take-most decision on the 20 unseen test tokens. The
  earlier docs described it as a training quantity; that was wrong.
- **The reported decoder is supervised and post hoc.** It can hide weight
  changes, so "no dopamine effect" means no detectable effect on decoded
  accuracy, not that plasticity is absent.
- **The `trained_pretrained` online argmax is not learning.** It is above chance
  because the readout is initialised with supervised logistic-regression
  weights. It is a positive control for readout expressivity.
- **The `DAN → Kenyon cell` edges are extracted but not simulated** (backward
  stage pair); the reward is a global scalar gated by real DAN spikes.
- **Statistics.** One α = 0.05 plan: one-sided Wilcoxon vs chance with Holm
  across conditions; two-sided paired Wilcoxon contrasts with Holm across a
  pre-specified contrast family; sign-flip permutation for leakage. At n = 10
  the exact two-sided Wilcoxon floor is 0.00195. There are 20 unique held-out
  tokens, so accuracy steps are 0.05.

## Reproduce

```bash
python -m pip install -r requirements.lock.txt && python -m pip install -e .
python -m flyread fetch-data

# final RNG-isolated recode sweeps
python -m flyread --config configs/odor-recode.yaml \
    --run-id recode-v2-rng sweep
python -m flyread --config configs/odor-recode-50.yaml \
    --run-id recode-50-v2-rng sweep
python -m flyread --config configs/odor-recode-50-controls.yaml \
    --run-id recode-50-controls-rng sweep

# fast online-RPE diagnostic (not for reported accuracy)
python -m flyread --config configs/fast-online-rpe.yaml \
    --run-id fast-online-rpe sweep --workers 3
```

Archived runs: `runs/odor-v1-merged` (physiological null),
`runs/pctrl-v1-merged` / `runs/pctrl-sep-merged` (probes, separable control),
`runs/recode-v2-rng-merged` (gain 20), `runs/recode-50-v2-rng-merged` (gain 50),
`runs/recode-50-controls-rng-merged` (growth-matched controls),
`runs/recode-v2-extra-rng-merged` / `runs/recode-50-extra-rng-merged`
(shuffled no-growth isolation controls). These directories are gitignored and
await Zenodo deposit; see `CITATION.cff`.
