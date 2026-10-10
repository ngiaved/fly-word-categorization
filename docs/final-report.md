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

**Neither task produced significant learning in the real evaluation pipeline.**
The visual task is a documented negative result. The odor task removes every
synthetic component the visual task needed, and the formal 10-seed run
confirms the same null: with zero synthetic edges the trained readout still
stays at chance.

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

### Where the odor signal is lost (hypothesis, not yet confirmed)

The output stage is no longer silent — `no-response` is 0.000–0.100, i.e. the
`pair_gain.mushroom_body>output` knob (10.0) wakes the real KC → MBON readout.
So unlike the visual task, this is not an under-driven-output problem. The
observed pattern (KC population decodes well above chance in isolation, while
the four output counts carry no class code) points at the plastic
`mushroom body → output` projection and/or the dopamine credit rule as the
bottleneck. This was not resolved within this change.

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

---

## Interpretation

Within the calibrated spontaneous regime (weight scale 4, noise weight 4.0,
background dc 0), the visual network — real connectivity plus the documented
synthetic bridge — does not transfer a decodable word-class signal to its
readout. The pivot to the real olfactory pathway removes the synthetic bridge
and readout and gives the stimulus genuine category structure, yet the first
reduced run still fails to learn. Taken together, the two negative results
suggest the bottleneck is not the input encoding or the missing optic-lobe
route, but the dopamine-gated `mushroom body → output` readout stage itself
under this calibrated regime. This is reported as a negative result without
reframing, as the evaluation spec requires.

## What would need to change to revisit it

- Make the plasticity actually shape a class code: the per-class credit rule
  currently drives mean dopamine strongly negative (≈ −0.5) and shrinks the
  readout toward a prior. A reward-prediction-error baseline that is not
  dominated by the ~75 % wrong-at-chance surplus is the obvious first target.
- Simulate the real `DAN → mushroom body` edges (reorder stages so
  `reinforcement` precedes `mushroom_body`) instead of the global-scalar
  surrogate, so the teacher flows through real synapses.
- A supervised-pretraining initialisation of the plastic readout
  (`learning.pretrain`, implemented but off by default) would test whether the
  readout *can* carry the KC signal at all before blaming the learning rule.
- The formal ≥ 10-seed run is now complete (`runs/odor-v1-merged`) and is also
  null (trained 0.262, p = 0.21), so the null is not an under-powering artifact.
  Revisiting it means attacking the readout/credit-rule bottleneck above, not
  adding seeds.

## Reproducing

```bash
python -m pip install -r requirements.lock.txt
python -m pip install -e .
python -m flyread fetch-data        # pin + verify FlyWire artifacts (CC-BY-NC)
python -m flyread run               # calibrate -> gate -> evaluate -> report
python -m flyread sweep             # formal: 10 seeds in parallel, then merge
```

`flyread run` uses the default `odor-v1` config with its configured seed count.
`flyread sweep` is the formal multi-seed path: it fans one `evaluate` process
per seed across cores, skips seeds that already have a `results.json`
(crash/resume), and merges the shards into `<run-id>-merged`. The visual chain
is reproduced with `flyread --config configs/smoke.yaml ...`.

Every archived run under `runs/` includes its manifest (config, seed state,
checksums, annotation rules, calibration attempts, codegen target, hardware,
git commit). The visual 2-seed run is `runs/run-20261008T204756Z/`; the odor
reduced diagnostic is `runs/run-20261009T095645Z/`; the formal odor run is
`runs/odor-v1-merged/`.
