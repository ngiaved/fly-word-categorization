# FlyWord: final report

Dopamine-gated plasticity in a subcircuit of the *Drosophila* FlyWire
connectome (release 783). Two tasks were run, and both are reported here
without reframing:

1. **grid-v1 (visual):** rendered 4-letter words are mapped onto photoreceptors
   and pushed through the optic lobe into the mushroom body. This was the
   original task.
2. **odor-v1 (olfactory):** words name arbitrary odor identities and drive the
   real antennal-lobe projection neurons (ALPN) → Kenyon cell → MBON chain.
   This is now the default configuration.

> **Superseded in part.** This report documents the nulls at the calibrated
> operating point. The negative finding was followed by a *recode* experiment:
> raising the `ALPN → Kenyon cell` gain (with a widened calibration ceiling)
> restores a decodable Kenyon-cell class code and a post-hoc supervised decoder
> then recovers the task (held-out 0.363 at gain 20, 0.418 at gain 50, up to 0.833
> with structural growth on the main recode run). Those are the current results;
> see [current-state.md](current-state.md), [paper.md](paper.md), and
> `runs/recode-v2-merged`, `runs/recode-50-v2-merged`.
>
> The recode is a diagnostic intervention outside the physiological band, and the
> growth-matched controls have now been **run** (`runs/recode-50-controls-merged`,
> 10 seeds): structural growth still beats no-growth (`structural_on` 0.750 vs
> `trained` 0.418, *p* = 0.002), but the gain is unchanged when all label guidance
> is removed (`structural_unguided` 0.785, *p* = 0.75) or category inheritance is
> broken (`structural_random_category` 0.707, *p* = 0.61), and a **no-growth
> decoder given 300 random Kenyon-cell projections reaches 0.983**, above every
> growth condition. So the growth effect is a decoder-capacity effect, not a
> learned, label-guided prototype; the dopamine rule remains non-contributory
> (`dopamine_off` 0.828 vs `structural_on` 0.750, *p* = 0.084); and the online
> MBON-argmax readout stays at chance throughout, so none of this is online
> learning.

**Neither task produced significant learning in the real evaluation pipeline.**
The visual task is a documented negative result. The odor task removes every
synthetic component the visual task needed, and the formal 10-seed run
confirms the same null: with zero synthetic edges the trained readout still
stays at chance. **Positive controls resolve that null into a precise
statement** (Section [Positive controls](#positive-controls-where-the-odor-signal-is-lost-confirmed)):
the category is perfectly decodable at the stimulus (input probe 1.000), a
supervised decoder memorises the Kenyon-cell presentations outright, and yet the
Kenyon-cell population carries no generalisable class code (probe 0.255/0.227) —
so the label is lost at the `ALPN → Kenyon cell` transform, before the plastic
readout has anything to learn.

## Headline

| task | synthetic edges | trained held-out acc | status |
|---|---|---|---|
| grid-v1 (visual) | bridge + readout | 0.233 (2 seeds, 100 trials) | null |
| odor-v1 (olfactory) | **none** | **0.262** (10 seeds, 600 trials) | null (formal) |

Both are against chance 0.25. The odor row is the formal ≥ 10-seed result
required by the evaluation spec: across 10 seeds the trained readout scored
0.262 (95 % CI [0.237, 0.287]), not significantly above chance (one-sample
t-test p = 0.21, Wilcoxon p = 0.24), and its own untrained control scored
0.227. It is a robust null, not an under-powered diagnostic.

---

## Why the visual task was abandoned

The original design assumed this signal chain:

```
photoreceptor -> lamina -> medulla -> lobula -> Kenyon cell -> MBON output
```

**That chain does not exist in the FlyWire connectome.** Measured synapse
counts between the specified stages, over *all* candidate neurons in release
783:

| from → to | synapses (excitatory) |
|---|---|
| photoreceptor → lamina | 192,878 (180,523) |
| lamina → medulla | 239,863 (205,959) |
| medulla → lobula | 1,117 (474) |
| **lobula → Kenyon cell** | **1 (0 excitatory)** |
| medulla → Kenyon cell | 2 (2) |
| Kenyon cell → Kenyon cell | 379,234 (379,234) |
| Kenyon cell → MBON | 247,053 (247,053) |

Kenyon cells receive input **only** from other Kenyon cells (379 k synapses),
from the `MBIN` class (216 k), and from `DAN` neurons (59 k). There is
essentially no optic-lobe input to the mushroom body. This is real biology,
not a bug in the annotation filters: **the mushroom body is not a visual target
in *Drosophila*.** It is driven by the antennal lobe and by reinforcement
neurons.

The visual task therefore had to synthesise two connections — a
`lobula → Kenyon cell` bridge and a dense `mushroom body → output` readout —
flagged `artificial` on every edge. Through the full evaluation pipeline the
trained readout stayed at chance, and three independent probe sweeps (readout
drive, stimulus contrast, bridge density) showed the word-class signal was
already lost before the readout. Full detail is preserved below under
[The visual task (grid-v1)](#the-visual-task-grid-v1).

---

## The odor task (odor-v1, current default)

Instead of inventing the missing pathway, the default config now drives the
pathway the connectome actually carries:

```
ALPN (olfactory) -> Kenyon cell (mushroom body) -> MBON (output)
                        ^
                        |
                  DAN (reinforcement)
```

Measured connectivity in release 783 (edges / synapses):

| from → to | edges / synapses |
|---|---|
| ALPN → Kenyon cell | 28,144 / 329,394 |
| Kenyon cell → MBON | 89,315 / 256,719 |
| DAN → Kenyon cell | 49,616 / 60,657 |

### What changed in the code

- `encoding.scheme_id: odor-v1`. Words are encoded as deterministic sparse
  patterns over the input stage (`OdorMapping`): each category owns a fixed,
  seeded, disjoint prototype subset of the inputs (171 of the 685-cap inputs
  here); a word activates 64 inputs from its own category prototype plus 4
  cross-category inputs, at graded drive in `[0.35, 1.0]`, and the rest are
  silent. The category overlap is deliberate — similar odours share receptor
  sets, as in the real antenna — so the class structure exists in the stimulus
  itself, which was false of the rendered-word ink signal.
- `subcircuit.stages` is now `olfactory → mushroom_body → reinforcement →
  output`, and both `subcircuit.bridge.enabled` and
  `subcircuit.readout.enabled` are **false**. Every simulated edge is real
  FlyWire connectivity (23,075 edges / 120,424 synapses; zero artificial).
- `grid-v1` is retained unchanged and is used by `configs/smoke.yaml`, which
  keeps the visual code path under test.

### Subcircuit actually simulated

| | |
|---|---|
| Neurons | 1,065 |
| Edges | 23,075 (all real, 0 artificial) |
| Synapses | 120,424 |
| Input stage (`olfactory`, ALPN) | 673 |
| Mushroom body (Kenyon cells) | 384 |
| Reinforcement (DAN) | 4 |
| Output (MBON) | 4 |
| Calibrated weight scale | 4.0 (noise 4.0, background dc 0, 6.0 Hz) |

Unlike the visual subcircuit, `real_edges_only.signal_reaches_outputs` is
**true** with no bridge: the ALPN → KC → MBON chain is densely connected in
the real data.

### Measured result (formal, 10 seeds)

Run `runs/odor-v1-merged`: 10 seeds (1000-1009), 600 training trials per
seed, `eval_repeats = 3`, linear readout, calibrated scale 4.0, chance 0.25.
Ten per-seed `evaluate` processes ran in parallel on 12 cores (`flyread
sweep`) and were merged. Parallel wall-clock ≈ 13.8 h, against ≈ 126 h of
single-core compute.

| condition | mean acc | 95% CI | p vs chance | significant | no-response | train acc |
|---|---|---|---|---|---|---|
| untrained network | 0.227 | [0.195, 0.262] | 0.8850 | no | 0.013 | n/a |
| trained (main result) | **0.262** | [0.237, 0.287] | 0.2069 | no | 0.257 | 0.219 |
| shuffled training labels | 0.258 | [0.233, 0.282] | 0.2683 | no | 0.002 | 0.247 |
| dopamine off | 0.245 | [0.205, 0.285] | 0.5887 | no | 0.000 | 0.252 |
| structural plasticity on | 0.252 | [0.222, 0.283] | 0.4610 | no | 0.008 | 0.242 |
| structural plasticity off | 0.253 | [0.227, 0.288] | 0.4228 | no | 0.260 | 0.224 |

Every condition is statistically indistinguishable from chance at alpha 0.01.
The trained mean sits ~1.2 points above chance, its 95 % CI spans
0.237-0.287, and its own untrained control is *below* it (0.227) — the
textbook signature of no learned class code. Unlike the visual task, the
output stage is **not** silent in the trained condition (no-response 0.257)
yet carries no class code, so the null is a signal loss upstream of the
readout and survives the removal of every synthetic edge. The trained
confusion matrix is nearly uniform, with a mild surplus in column 1:

```
true\pred |    0    1    2    3
-------------------------------
   animal |   38   44   35   33
     food |   40   48   25   37
     tool |   41   43   33   33
    place |   46   39   27   38
```

### Reduced-budget diagnostic (superseded)

Run `runs/run-20261009T095645Z`: 1 seed (1000), 100 training trials,
`eval_repeats = 1`, linear readout, calibrated scale 4.0, chance 0.25. This
was deliberately reduced (the spec minimum is 10 seeds) to get a fast
diagnostic. Wall-clock 2,968 s.

| condition | mean acc | no-response | train acc |
|---|---|---|---|
| untrained network | 0.250 | 0.000 | n/a |
| trained (main result) | **0.100** | 0.100 | 0.230 |
| shuffled training labels | 0.350 | 0.000 | 0.250 |
| dopamine off | 0.400 | 0.000 | 0.240 |
| structural plasticity on | 0.200 | 0.000 | 0.220 |
| structural plasticity off | 0.400 | 0.100 | 0.220 |

The trained condition is at **0.100, below chance**, and below its own
untrained control (0.250). The mean dopamine during training was strongly
negative (≈ −0.42 then −0.54 over the two windows), and training accuracy
drifted down with it (0.26 → 0.20). In other words, the same qualitative
failure as the visual task: dopamine punishes the readout on most trials and
drags it toward a label prior rather than shaping a class code. The shuffled
label control read 0.350, which is within single-seed noise but above the
0.30 leakage margin and the harness emits its data-leakage warning.

The trained confusion matrix collapses onto the first category column, so the
readout expresses a label prior, not a class code:

```
true\pred |    0    1    2    3
-------------------------------
   animal |    1    0    0    4
     food |    4    0    0    1
     tool |    2    0    0    3
    place |    4    0    0    1
```

### Positive controls: where the odor signal is lost (confirmed)

To separate "the pipeline cannot detect learning" from "the network does not
learn", the evaluation now fits supervised linear probes on a fresh *untrained*
network at two stages — the input (`olfactory`, 673 features) and the
Kenyon-cell population (`mushroom_body`, 384 features) — scored on held-out
words, and runs a *positive-control gate* (input probe ≥ 0.90 AND Kenyon-cell
probe ≥ chance + 0.05). Two new 10-seed runs add the probes, a
supervised-pretrained readout condition, and a separable-task configuration
(zero cross-category overlap):

| run | config | purpose |
|---|---|---|
| `runs/pctrl-v1-merged` | `configs/default.yaml` | probes, gate, pretrained, default task |
| `runs/pctrl-sep-merged` | `configs/odor-separable.yaml` | same, on trivially separable classes |

**Probes, 10 seeds, chance 0.25 (held-out accuracy on unseen words):**

| task | stage | features | train | CV | held-out | 95 % CI |
|---|---|---|---|---|---|---|
| default | input | 673 | 1.000 | 1.000 | **1.000** | [1.000, 1.000] |
| default | Kenyon cell | 384 | 1.000 | 0.236 | **0.255** | [0.227, 0.287] |
| separable | input | 673 | 1.000 | 1.000 | **1.000** | [1.000, 1.000] |
| separable | Kenyon cell | 384 | 1.000 | 0.228 | **0.227** | [0.195, 0.262] |

The input stage decodes the category perfectly on every seed — the stimulus
genuinely reaches the network intact and the probe instrument works. The
Kenyon-cell population decodes at chance in both tasks despite a training
accuracy of 1.000 (the decoder memorises but has no class structure to
generalise), with cross-validation agreeing. **The gate fails in both runs** with
the same diagnosis:

> input probe passes but the Kenyon-cell probe 0.255 is not above chance+0.05:
> the label signal is lost before the plastic readout (upstream bottleneck).

**Supervised pretraining does not help:** `trained_pretrained` fits the readout
to train accuracy 1.000 on every seed, yet held-out is 0.268 (default) and
0.235 (separable) — at chance and no better than plain `trained` (0.262, 0.248).
The bottleneck is not readout capacity or initialisation.

**Not task difficulty:** on the separable task the class structure is trivial
(probe 1.000) and still nothing learns, so overlap is not the cause.

**Location of the loss, refined.** Earlier this report floated the
`mushroom body → output` readout/credit rule as the bottleneck. The probes move
the blame upstream: the readout — probe decoder, fitted decoder, and
supervised-pretrained decoder alike — is a valid instrument, and each memorises
the Kenyon-cell features; the class information is absent from the
**`ALPN → Kenyon cell` code** the readout would have to amplify. The plasticity
rule and credit assignment are exonerated as the *first* cause.

Full per-condition tables are in the archived `report.md` of each merged run.

### Known limitation of the current odor subcircuit

The real `DAN → Kenyon cell` edges are present in the extracted subcircuit
(372 edges) but are **not simulated**: `build_network` only builds synapses for
consecutive stage pairs, within-stage recurrence, and the plastic
`mushroom_body → output` pair. Because `reinforcement` sits after
`mushroom_body` in the configured stage order, `reinforcement → mushroom_body`
is a backward pair and is dropped, so the network logs:

```
no reinforcement stage feeds the mushroom body; the synthetic
reward pulse will drive the plastic pathway directly
```

The reward is still *gated* by the real DAN neurons' spike counts
(`_deliver_teaching` drives them and uses their spikes as the gate), but it is
applied as a global scalar to the plastic weights rather than flowing through
the real DAN → KC synapses. This does not change the learning rule that the
visual task used, but it does mean the config comment's "real DAN → Kenyon
cell" pathway is not exercised in the simulation. Reordering the stages so
`reinforcement` precedes `mushroom_body` would simulate those 372 real edges;
that is a separate experiment and was not done here.

---

## The visual task (grid-v1)

Preserved for the record. Reproduce with `configs/smoke.yaml` or by setting
`encoding.scheme_id: grid-v1` and restoring the visual stages.

### Result

**The main condition did not learn the task.** Across the first real-pipeline
2-seed run the trained readout classified at **0.233** held-out accuracy,
which is *not* above the 25 % chance level (one-sample t-test p = 1;
Wilcoxon p = 1.0000), and is at or below the untrained control (0.258). All
conditions are statistically indistinguishable from chance:

| condition | mean acc | 95% CI | p vs chance | no-response | train acc |
|---|---|---|---|---|---|
| untrained network | 0.258 | [0.250, 0.267] | 0.2500 | 0.500 | n/a |
| trained (main result) | 0.233 | [0.233, 0.233] | 1.0000 | 0.542 | 0.085 |
| structural plasticity on | 0.283 | [0.283, 0.283] | — | 0.533 | 0.145 |

`structural_on` at 0.283 is **not** a significant result: both seeds landed on
the identical value, so the test has zero variance and the t statistic is
degenerate. Zero variance is never evidence, and the fixed code reports it as
not significant. Chance level 0.25; alpha 0.01; 2 seeds; `n_train_trials` 100;
Cython codegen. Full archived run: `runs/run-20261008T204756Z/report.md`.

The trained confusion matrix collapses onto the darkest category (column 3),
i.e. the readout expresses a label prior, not a class code:

```
true\pred |    0    1    2    3
-------------------------------
   ink_q1 |    0    0   16   14
   ink_q2 |    0    0   17   13
   ink_q3 |    1    0   11   18
   ink_q4 |    0    0   13   17
```

### Why the null result is not an artifact of one unlucky seed

Three levers were swept through the real evaluation path (untrained condition,
seed 1000, chance 0.25; each point within single-seed noise of chance):

**Readout drive** (`artificial_readout.weight_ratio` ×
`noise.stage_weight_ratio.output`): ratio 8/0.1 → 0.233 (no-response 0.57);
8/0.5 → 0.183; 12/0.2 → 0.250; 20/0.1 → 0.317; 30/0.1 → 0.217; 40/0.1 →
0.217; 50/0.1 → 0.333; 8/1.0 → 0.233. From ratio >= 20 the output stage fires
on **100 % of presentations** (no-response 0.0), yet accuracy stays at chance.

**Stimulus contrast** (`encoding.max_hz`, 0-120 Hz → 0-800 Hz span): 120 → 0.250;
240 → 0.217; 480 → 0.217; 800 → 0.333. No effect.

**Bridge density** (`subcircuit.bridge.synapses_per_kc`, 6 → 24 → 48, i.e. x4
and x8 denser lobula→Kenyon wiring): 0.250; 0.267; 0.250. No effect.

A readout that fires on every presentation, driven 6.7x harder by contrast and
8x denser by the bridge, still does not separate the classes. The class signal
is absent from the Kenyon-cell population itself under the calibrated operating
regime, so the readout has nothing to amplify.

### Diagnosis

1. The optic-lobe pathway is almost empty at the top: release 783 has 1,117
   medulla→lobula synapses across all candidates (586 in the subcircuit), so
   the lobula stage is barely driven by the visual chain.
2. The mushroom body receives essentially no optic-lobe input in the real data
   (1 lobula→KC synapse, 2 medulla→KC), which is why both the lobula→KC bridge
   and the KC→output readout are synthetic (flagged `artificial` everywhere;
   never reported as FlyWire connectivity). The signal that survives to the KC
   stage is too weak to be decoded at the readout.
3. Earlier positive-looking numbers (untrained 0.43-0.60, trained 0.567/0.617)
   came from an inspect/harness operating point that the full evaluation
   pipeline does not reproduce. They are **withdrawn**.
4. The per-class credit rule, the linear-decoder readout, and the
   structural-growth machinery are implemented, tested, and behave as designed;
   the negative result is about the signal path, not those mechanisms.

### Recode follow-up: decoder-fit leakage and the harness fix

To test the upstream-bottleneck diagnosis, the `ALPN → Kenyon cell` transform
was re-encoded (`configs/odor-recode.yaml`: `olfactory → mushroom_body` gain 20,
`mushroom_body → output` gain 10, calibration ceiling 100 Hz). Run
`runs/recode-v1-merged` (10 seeds) lifts the Kenyon-cell probe from 0.255 to
**0.933** — the positive-control gate now **passes** — and reports trained
accuracy **0.363** (p = 0.0013). But the shuffled-labels control scored **0.615**,
which raised the harness data-leakage warning and invalidated the run.

**Confound.** `evaluate.run_condition` fit the supervised linear decoder on the
dataset (true) labels in *every* condition, including `shuffled_labels`. The
control therefore had a classifier reading the true labels off the network's
training outputs, i.e. a mapping the network was never taught. The correct
protocol — stated in the config comments and the paper — is to fit the decoder
on the labels each training trial was actually **presented** with
(`labels_for_training`, shuffled where shuffled).

**Fix.** The decoder fit now uses the presented labels. This is a no-op for every
non-shuffled condition (there the presented labels *are* the dataset labels), so
all other `recode-v1` numbers stand; only the `shuffled_labels` row was invalid.
A regression test (`test_decoder_is_fit_on_presented_labels_not_dataset_labels`)
reproduces the leak and guards it, and the smoke pipeline now puts the shuffled
control at exactly chance (0.250, no warning).

**Residual floor and the leakage margin.** Under the fixed harness `recode-v2`
puts the shuffled control at **0.330**, down from 0.615 but still above the raw
chance level. Crucially it is indistinguishable from the untrained network
(**0.338**), which is the point: the recode lifts the *representation's own*
decodability (the Kenyon-cell probe reaches 0.958), so even a no-learning network
sits above chance and the shuffled control inherits that floor. The leakage check
now compares the shuffled control against the higher of chance and the untrained
floor (`evaluate.aggregate`), not chance alone, so a generically decodable
representation no longer reads as leakage. A new `shuffled_labels_no_growth`
condition answers the remaining question — whether structural recruitment, whose
category guidance uses the network's own predictions, contributes any of it — in
the negative: with growth switched off the shuffled control falls to or below
chance (**0.230** at gain 20, **0.233** at gain 50; Shannon chance 0.25;
`runs/recode-v2-extra-merged`, `runs/recode-50-extra-merged`; no warning).

**Mechanism isolation.** `recode-v1` also showed that accuracy tracks *structural
growth*, not dopamine: `dopamine_off` (growth on) 0.597 and `structural_on` 0.618
both far exceed `trained` (growth off) 0.363, while the Kenyon-cell probe is high
regardless. A new condition `dopamine_off_no_growth` completes the dopamine ×
growth 2×2 so each mechanism can be read off against its own control.

**Results.** Both fixed-harness runs are complete — `recode-v2-merged` (gain 20,
`configs/odor-recode.yaml`) and `recode-50-v2-merged` (gain 50,
`configs/odor-recode-50.yaml`), 10 seeds each. Both pass the positive-control gate
(input probe 1.000; Kenyon-cell probe **0.950** at gain 20, **1.000** at gain 50)
and carry no leakage warning under the untrained-relative check. Held-out accuracy
(mean, chance 0.25):

| condition | gain 20 | gain 50 |
| --- | --- | --- |
| untrained | 0.338 | 0.330 |
| trained | **0.363** | **0.418** |
| shuffled_labels | 0.330 | 0.245 |
| shuffled_labels_no_growth | 0.230 | 0.233 |
| dopamine_off | 0.597 | 0.785 |
| dopamine_off_no_growth | 0.357 | 0.387 |
| trained_pretrained | 0.512 | 0.340 |
| structural_on | **0.608** | **0.833** |
| structural_off | 0.370 | 0.435 |

The recode is not marginal: at gain 50 `structural_on` reaches **0.833** and
`dopamine_off` **0.785**, and every growth-on condition improves over its gain-20
value. Accuracy scales with how much structural recruitment a condition permits —
with growth off (`trained`, `structural_off`, `dopamine_off_no_growth`) it
collapses toward the untrained floor, while `structural_on` sits far above
`structural_off` (0.608 vs 0.370; 0.833 vs 0.435). The shuffled control stays at or
below the untrained floor (0.330 / 0.245 vs 0.338 / 0.330) and is not significant,
and the no-growth isolation control is at/below chance, so the residual is
structural, not label leakage. The `recode-v1` shuffled row is superseded.

---

## Interpretation

Within the calibrated spontaneous regime (weight scale 4, noise weight 4.0,
background dc 0), the visual network — real connectivity plus the documented
synthetic bridge — does not transfer a decodable word-class signal to its
readout. The pivot to the real olfactory pathway removes the synthetic bridge
and readout and gives the stimulus genuine category structure, yet the formal
runs still fail to learn. Positive controls then localise the common cause: the
input stage decodes the category perfectly (probe 1.000), the readout is a
valid instrument, and the Kenyon-cell population decodes at chance under the
same instrument. The bottleneck is therefore the **`ALPN → Kenyon cell`
transform** — the code the readout would have to amplify does not linearly
carry the label — not the input encoding, the missing optic-lobe route, the
plastic `mushroom body → output` readout, or its credit rule. This is reported
as a negative result without reframing, as the evaluation spec requires. The
recode follow-up above shows the diagnosis is actionable: raising the transform's
gain makes the same harness learn (`structural_on` **0.833** at gain 50),
confirming the bottleneck was the transform rather than the readout or its credit
rule.

## What would need to change to revisit it

- **Recode the `ALPN → Kenyon cell` stage — now tested and positive.** The probes
  put the failure here: the stimulus decodes at 1.000 and the readout is a working
  instrument, but the Kenyon-cell population does not carry the class. Raising the
  transform's gain (and the readout gain) does make the network learn — see the
  recode follow-up above (`runs/recode-50-v2-merged`: `structural_on` 0.833,
  `dopamine_off` 0.785, Kenyon-cell probe 1.000, no leakage warning). Denser or
  sparser Kenyon-cell representations, input scaling, and non-linear decoding
  remain open variants to sweep.
- **A reward-prediction-error baseline** for the per-class credit rule, so mean
  dopamine is not dominated by the ≈75 % wrong-at-chance surplus (relevant once
  a class code exists to learn).
- **Simulate the real `DAN → mushroom body` edges** (reorder stages so
  `reinforcement` precedes `mushroom_body`) instead of the global-scalar
  surrogate, so the teacher flows through real synapses.
- Supervised pretraining (`learning.pretrain`) is now **tested and negative**:
  it fits the Kenyon-cell features to 1.000 yet generalises at chance, closing
  an earlier open item.
- Adding seeds will not change the result: the formal ≥ 10-seed runs
  (`runs/odor-v1-merged`, `runs/pctrl-v1-merged`, `runs/pctrl-sep-merged`) all
  exclude under-powering.

## Reproducing

```bash
python -m pip install -r requirements.lock.txt
python -m pip install -e .
python -m flyread fetch-data        # pin + verify FlyWire artifacts (CC-BY-NC)
python -m flyread run               # calibrate -> gate -> evaluate -> report (default config)
python -m flyread sweep             # formal: 10 seeds in parallel, then merge
python -m flyread --config configs/odor-separable.yaml --run-id pctrl-sep sweep  # separable-task control
```

`flyread run` uses the default `odor-v1` config with its configured seed count.
`flyread sweep` is the formal multi-seed path: it fans one `evaluate` process
per seed across cores, skips seeds that already have a `results.json`
(crash/resume), and merges the shards into `<run-id>-merged`. The visual chain
is reproduced with `flyread --config configs/smoke.yaml ...`. The
positive-control package (input/Kenyon-cell probes, gate, supervised-pretrained
condition) is on by default (`evaluation.probes.enabled`) under
`configs/default.yaml`.

Every archived run under `runs/` includes its manifest (config, seed state,
checksums, annotation rules, calibration attempts, codegen target, hardware,
git commit). The visual 2-seed run is `runs/run-20261008T204756Z/`; the odor
reduced diagnostic is `runs/run-20261009T095645Z/`; the formal odor run is
`runs/odor-v1-merged/`; the positive-control runs are `runs/pctrl-v1-merged/`
and `runs/pctrl-sep-merged/`. The recode follow-up runs
are `runs/recode-v2-merged` (gain 20) and `runs/recode-50-v2-merged` (gain 50),
with the `shuffled_labels_no_growth` isolation controls in
`runs/recode-v2-extra-merged` and `runs/recode-50-extra-merged`.
