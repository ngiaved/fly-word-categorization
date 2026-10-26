# FlyWord: executive report

**Bottom line.** The *Drosophila* FlyWire (release 783) subcircuit does not learn
the word-classification task at its calibrated operating point — a robust,
well-controlled null. Positive controls localise a bottleneck to a single
transform (`ALPN → Kenyon cell`): at gain 1 the stimulus decodes perfectly
(1.000) but the Kenyon-cell population does not (CV 0.236 / held-out 0.255).
Recoding that transform's gain (to 20 or 50) restores the Kenyon-cell class code
(CV 0.956 / 1.000) and lifts the post-hoc supervised decoder. **But the recode is
necessary, not sufficient:** the online MBON-argmax readout — the network's own
class decision on held-out words, with no supervised decoder — stays at chance
(0.232–0.250 in the main recode runs; 0.245–0.268 across the growth controls),
including at gain 50 where the Kenyon-cell probe is 1.000. The four-dimensional
MBON readout and its credit rule are a second, unresolved bottleneck. The
growth-matched controls also demote the headline: structural growth is a
decoder-capacity effect, and the dopamine rule has no detectable effect on
decoded accuracy. The RNG-isolated re-runs are complete for the recode and
control conditions; the physiological baseline re-run is paused. See
[current-state.md](current-state.md).

---

## 1. What was asked

Two tasks, both reported as they are:

1. **grid-v1 (visual):** rendered 4-letter tokens → photoreceptors → optic lobe →
   mushroom body. The original task; needs a synthetic bridge because this stage
   definition has no direct optic-lobe → Kenyon-cell pathway (visual projection
   neurons do innervate the calyx, but are not in the modelled chain).
2. **odor-v1 (olfactory):** tokens name arbitrary odor identities and drive the
   real antennal-lobe projection neurons (ALPN) → Kenyon cell → MBON chain. Now
   the default; **zero synthetic edges**.

The stimuli are 80 seeded four-category prototype patterns (20 per category;
15/5 train/test per category). The input probe is 1.000 by construction; the task
tests the circuit, not word knowledge.

Success = held-out classification accuracy on *unseen* tokens significantly above
the appropriate floor, with 10 seeds and positive-control probes validating the
instrument. Because the untrained network is itself above chance, the learning
claim is tested against the paired untrained control, not chance alone.

## 2. Headline results (RNG-isolated, 10 seeds)

| run | task / gain | trained held-out acc | verdict |
|---|---|---|---|
| `odor-v1-merged` | odor, calibrated baseline | **0.262** (10 seeds, 600 trials) | null (not above chance, *p* = 0.21); baseline RNG re-run paused |
| `recode-v2-rng-merged` | odor, recode gain 20 | **0.383** | above chance; **not above untrained floor** (+0.045, Holm *p* = 1.0) |
| `recode-50-v2-rng-merged` | odor, recode gain 50 | **0.400** | above chance; **not above untrained floor** (+0.070, Holm *p* = 0.39) |
| `recode-50-controls-rng-merged` | odor, gain 50 + growth controls | **0.400** trained; **0.813** growth; **0.990** random features | decoder capacity, not learning |

The visual task is a documented negative result. All odor runs pass the
positive-control gate at baseline. In the recoded runs the online argmax stays at
chance (0.232–0.250); across the nine control conditions it is 0.245–0.268. All
reported accuracies are therefore supervised post-hoc-decoder representation
quality, not online learning.

The statistical plan is one α = 0.05 scheme: one-sided Wilcoxon versus chance with
Holm across conditions; two-sided paired Wilcoxon contrasts with Holm across a
pre-specified contrast family; sign-flip permutation test for the shuffled-label
control against chance. At n = 10 the exact two-sided Wilcoxon floor is 0.00195;
there are 20 unique held-out tokens, so accuracy steps are 0.05.

## 3. Why the baseline fails — and what the recode does and does not fix

**Diagnosis (positive controls).** At the calibrated baseline the category is
perfectly decodable at the stimulus (input probe 1.000), yet the Kenyon-cell
population carries no generalisable class code (CV 0.236; held-out 0.255). The
label is lost in the `ALPN → Kenyon cell` transform, before the plastic readout
has anything to learn.

**Fix (recode).** Raising the `ALPN → Kenyon cell` gain (with the calibration
rate ceiling widened so the hotter Kenyon-cell regime is accepted) restores a
decodable code. Held-out decoder accuracy across conditions:

| condition | gain 20 | gain 50 |
|---|---|---|
| untrained | 0.338 | 0.330 |
| trained | 0.383 | 0.400 |
| dopamine_off | 0.585 | 0.855 |
| structural_on | **0.573** | **0.813** |
| structural_off | 0.383 | 0.400 |
| shuffled_labels | 0.285 | 0.253 |
| shuffled_labels_no_growth | 0.230 | 0.233 |

**But the online readout does not recover.** In the recoded runs the online
argmax is 0.232–0.250; in the growth-control run every condition sits at
0.245–0.268, despite the Kenyon-cell probe being 1.000 at gain 50. The recode
opens the representational bottleneck; it does not make the learner work. That is
the corrected headline.

Three further findings:

- **Accuracy tracks structural growth under the supervised decoder, not
  dopamine.** With growth on (`structural_on`, `dopamine_off`) decoder accuracy
  far exceeds growth-off controls; turning growth off collapses it toward the
  untrained floor. Turning dopamine off changes decoded accuracy by −0.042
  (Holm *p* = 1.0), a null result — not proof that plasticity is absent.
- **The growth gain is decoder capacity, not a learned prototype.** The
  growth-matched controls show the gain is statistically unchanged when all
  label guidance is removed (`structural_unguided` 0.798 vs 0.813, Holm
  *p* = 1.0); random category assignment lowers the mean (0.652) but not
  significantly (Δ = +0.162, Holm *p* = 0.49); and a no-growth decoder given 300
  random Kenyon-cell projections reaches **0.990**, above every growth condition
  (Holm *p* = 0.018 vs trained). Growth adds the same number of feature
  dimensions, so it improves access to an already-decodable code rather than
  learning one. With the KC probe ≈ 1.000, 0.990 is close to the 300-feature
  linear ceiling.
- **The shuffled control is clean under RNG isolation.** The gain-20 shuffled
  control is 0.285 (*p* = 0.122) and gain-50 is 0.253 (*p* = 0.463), both at
  chance. The earlier gain-20 anomaly (0.330, *p* = 0.020) does not reproduce;
  it was run-order noise, not leakage. The no-growth isolation controls are
  0.230 / 0.233 (*p* = 0.75 / 0.72).

**The readout can express the code.** Supervised pretraining puts the online
argmax at 0.420 (gain 20) and 0.325 (gain 50). That is not dopamine learning —
the readout starts with supervised weights — but it is a useful positive control:
the 4-dimensional MBON readout is not incapable of expressing the category. The
failure is in what the plastic rule does, not in the readout's capacity to
represent.

**A fast RPE diagnostic does not rescue it.** We triaged a reward-prediction-error
gated per-class rule on a cheaper grid (3 seeds, 240 trials, `dt` 0.5 ms,
`runs/fast-online-rpe-merged`). It stayed at chance on all three seeds (0.250,
0.250, 0.250; `trained` 0.233) and collapsed to a near-constant prediction, with
training accuracy 0.251 — at the four-way floor. This diagnostic is underpowered
and off the formal grid, so it is not a formal negative, but it makes a full RPE
sweep low priority.

## 4. Harness corrections made during the work

- **Decoder-fit leakage (invalidated earlier recode run).** The supervised
  decoder was fit on dataset labels even in the shuffled control, letting the
  control read true labels off the network. Fixed to fit on *presented* labels;
  the shuffled control dropped from 0.615 to 0.285 / 0.253. Regression test
  added. This is a methods note, not a scientific contribution.
- **RNG isolation.** Each condition and probe pass now calls
  `seed_everything(seed)` at entry, so condition order cannot affect a
  condition's random stream and identical configurations are bit-identical.
  Confirmed in the final recode runs: `trained` − `structural_off` = 0.000
  exactly. The archived physiological baseline predates this; its paired
  contrasts remain internally valid, but its absolute numbers are provisional.
- **Leakage test corrected.** The earlier rule raised the threshold to the
  untrained floor after the gain-20 control failed. That exemption was removed as
  a goalpost move; the control is now tested against chance by a sign-flip
  permutation test. Under RNG isolation it passes.
- **Diagnostics added.** Each probe pass records spontaneous and evoked firing
  rates per stage, the fraction of Kenyon cells active in the stimulus window, and
  their mean spike count, to separate a transform failure from a signal-to-noise
  failure. These are not in the archived runs.
- **Confusion-matrix condition bug** fixed (`trained` was hardcoded; single-
  condition isolation runs crashed) via `pick_confusion_condition`, with tests.
- **Resume-from-checkpoint** added, so long runs resume completed
  (seed, condition) pairs instead of recomputing them.

## 5. Recommendations

1. **Report the recode as a diagnostic probe, not as evidence of learning.** It
   localises a bottleneck to the `ALPN → Kenyon cell` transform, but the online
   MBON-argmax readout never leaves chance.
2. **Do not report the growth numbers as learning.** The growth-matched controls
   show the growth gain is decoder capacity: it survives removal of label
   guidance and is beaten by random features with no growth. Report
   `trained_extra_features` (0.990) alongside any growth number as the capacity
   reference.
3. **Treat the readout/credit rule as a co-equal bottleneck.** Supervised
   pretraining lifts the online argmax to 0.420/0.325, so the readout can
   express the code; the unsupervised rule does not get there. A fast
   reward-prediction-error variant also stayed at chance (0.250 on three seeds),
   so scalar surprise gating alone is not the fix; a formal RPE sweep is low
   priority.
4. **Finish the physiological-baseline RNG re-run** only if absolute baseline
   accuracies are needed for publication. The recode/control conclusions are
   already final.
5. **Simulate the real `DAN → mushroom body` edges** instead of the global-scalar
   reward surrogate (backward stage pair currently dropped).

## 6. Reproduce

```bash
python -m pip install -r requirements.lock.txt && python -m pip install -e .
python -m flyread fetch-data
python -m flyread --config configs/odor-recode-50.yaml --run-id recode-50-v2-rng sweep
python -m flyread --config configs/odor-recode-50-controls.yaml \
    --run-id recode-50-controls-rng sweep

# fast online-RPE diagnostic (do not publish these numbers)
python -m flyread --config configs/fast-online-rpe.yaml --run-id fast-online-rpe sweep --workers 3
```

Archived: `runs/odor-v1-merged` (baseline null), `runs/recode-v2-rng-merged`
(gain 20), `runs/recode-50-v2-rng-merged` (gain 50), the growth-matched controls
`runs/recode-50-controls-rng-merged`, and the isolation controls
`runs/recode-v2-extra-rng-merged`, `runs/recode-50-extra-rng-merged`. These
directories are gitignored and await Zenodo deposit; see `CITATION.cff`. Full
detail in [`docs/final-report.md`](final-report.md) and
[`docs/current-state.md`](current-state.md).
