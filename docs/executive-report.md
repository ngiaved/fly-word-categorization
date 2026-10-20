# FlyWord: executive report

**Bottom line.** The *Drosophila* FlyWire (release 783) subcircuit does not learn
the word-classification task at its calibrated operating point — a robust,
well-controlled null. Positive controls localise a bottleneck to a single
transform (`ALPN → Kenyon cell`): at gain 1 the stimulus decodes perfectly
(1.000) but the Kenyon-cell population does not (CV 0.236 / held-out 0.255).
Recoding that transform's gain (to 20 or 50) restores the Kenyon-cell class code
(CV 0.950 / 1.000) and lifts the post-hoc supervised decoder. **But the recode is
necessary, not sufficient:** the online MBON-argmax readout — the only readout
that can show what the plastic synapses learned — stays at chance (0.235–0.272)
in every condition measured, including at gain 50. The four-dimensional MBON
readout and its credit rule are a second, unresolved bottleneck. The
growth-matched controls also demote the headline: structural growth is a
decoder-capacity effect, and the dopamine rule has no detectable effect on
decoded accuracy. See [current-state.md](current-state.md).

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

## 2. Headline results

| run | task / gain | trained held-out acc | verdict |
|---|---|---|---|
| `odor-v1-merged` | odor, calibrated baseline | **0.262** (10 seeds, 600 trials) | null (not above chance, *p* = 0.21) |
| `recode-v2-merged` | odor, recode gain 20 | **0.363** (10 seeds) | above chance; **not above untrained floor** (+0.025, Holm *p* = 1.0) |
| `recode-50-v2-merged` | odor, recode gain 50 | **0.418** (10 seeds) | above chance; **not above untrained floor after correction** (+0.088, Holm *p* = 0.117) |
| `recode-50-controls-merged` | odor, gain 50 + growth controls | **0.418** trained; **0.750** growth; **0.983** random features | decoder capacity, not learning |

The visual task is a documented negative result. All odor runs pass the
positive-control gate at baseline. In the control run, where the online argmax was
recorded for every condition, it stays at chance (0.235–0.272); the two main
recode runs did not record it. All reported accuracies are therefore supervised
post-hoc-decoder representation quality, not online learning.

The statistical plan is one α = 0.05 scheme: one-sided Wilcoxon versus chance with
Holm across conditions; two-sided paired Wilcoxon contrasts with Holm across a
pre-specified 11-pair family; sign-flip permutation test for the shuffled-label
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
| trained | 0.363 | 0.418 |
| dopamine_off | 0.597 | 0.785 |
| structural_on | **0.608** | **0.833** |
| structural_off | 0.370 | 0.435 |
| shuffled_labels | 0.330 | 0.245 |
| shuffled_labels_no_growth | 0.230 | 0.233 |

**But the online readout does not recover.** In the control run all nine
conditions sit at 0.235–0.272, despite the Kenyon-cell probe being 1.000 at gain
50. The recode opens the representational bottleneck; it does not make the
learner work. That is the corrected headline.

Three further findings:

- **Accuracy tracks structural growth under the supervised decoder, not
  dopamine.** With growth on (`structural_on`, `dopamine_off`) decoder accuracy
  far exceeds growth-off controls; turning growth off collapses it toward the
  untrained floor. Turning dopamine off changes decoded accuracy by −0.078
  (Holm *p* = 0.34), a null result — not proof that plasticity is absent.
- **The growth gain is decoder capacity, not a learned prototype.** The
  growth-matched controls (10 seeds) show the gain is statistically unchanged when
  all label guidance is removed (`structural_unguided` 0.785, Holm *p* = 1.0) or
  category inheritance is broken (`structural_random_category` 0.707, Holm
  *p* = 1.0), and is exceeded by a no-growth decoder given 300 random Kenyon-cell
  projections (0.983, Holm *p* = 0.018 vs trained). Growth adds the same number
  of feature dimensions, so it improves access to an already-decodable code
  rather than learning one. With the KC probe ≈ 1.000, 0.983 is close to the
  300-feature linear ceiling.
- **The shuffled control is not exculpated.** Under the pre-specified permutation
  test, the gain-20 growth-on shuffled control is above chance (0.330,
  *p* = 0.020) while the no-growth isolation control is not (0.230, *p* = 0.76);
  at gain 50 it is at chance (0.245, *p* = 0.56). The pattern is consistent with
  growth adding generically decodable features, but it is reported as a residual
  anomaly rather than explained away by moving the threshold.

## 4. Harness corrections made during the work

- **Decoder-fit leakage (invalidated earlier recode run).** The supervised
  decoder was fit on dataset labels even in the shuffled control, letting the
  control read true labels off the network. Fixed to fit on *presented* labels;
  the shuffled control dropped from 0.615 to 0.330. Regression test added. This
  is a methods note, not a scientific contribution.
- **RNG isolation.** Each condition and probe pass now calls
  `seed_everything(seed)` at entry, so condition order cannot affect a condition's
  random stream and identical configurations are bit-identical. Validated on a
  smoke run (`trained` == `structural_off` exactly). Archived runs predate this;
  their absolute numbers are provisional.
- **Leakage test corrected.** The earlier rule raised the threshold to the
  untrained floor after the gain-20 control failed. That exemption was removed as
  a goalpost move; the control is now tested against chance by a sign-flip
  permutation test.
- **Diagnostics added.** Each probe pass records spontaneous and evoked firing
  rates per stage, the fraction of Kenyon cells active in the stimulus window, and
  their mean spike count, to separate a transform failure from a signal-to-noise
  failure. These are not in the archived runs.
- **Confusion-matrix condition bug** fixed (`trained` was hardcoded; single-
  condition isolation runs crashed) via `pick_confusion_condition`, with tests.
- **Resume-from-checkpoint** added, so long runs resume completed
  (seed, condition) pairs instead of recomputing them.

## 5. Recommendations

1. **Treat the recode as a diagnostic probe, not as evidence of learning.** It
   localises a bottleneck to the `ALPN → Kenyon cell` transform, but the online
   MBON-argmax readout never leaves chance.
2. **Do not report the growth numbers as learning.** The growth-matched controls
   show the growth gain is decoder capacity: it survives removal of label
   guidance and is beaten by random features with no growth. Report
   `trained_extra_features` (0.983) alongside any growth number as the capacity
   reference.
3. **Build an online-learning benchmark before claiming the circuit learns.**
   The MBON-argmax readout must beat the untrained floor online; a
   reward-prediction-error baseline would remove the ≈75 % wrong-at-chance
   surplus that swamps the per-class credit rule. This is the second bottleneck.
4. **Re-run the sweep with RNG isolation and the new diagnostics** before
   publishing absolute accuracies. The archived sweeps took ≈ 126–242 h of
   single-core compute; this is the first item of future work.
5. **Simulate the real `DAN → mushroom body` edges** instead of the global-scalar
   reward surrogate (backward stage pair currently dropped).

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
`runs/recode-v2-extra-merged`, `runs/recode-50-extra-merged`. These directories
are gitignored and await Zenodo deposit; see `CITATION.cff`. Full detail in
[`docs/final-report.md`](final-report.md) and
[`docs/current-state.md`](current-state.md).
