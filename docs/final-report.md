# FlyWord: final report (negative / null result)

Dopamine-gated plasticity in a subcircuit of the *Drosophila* FlyWire
connectome (release 783), driven by rendered 4-letter English words and
evaluated on four-way `ink_quartile` classification.

## Result

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

## Why the null result is not an artifact of one unlucky seed

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

## Diagnosis

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

## Interpretation

Within the calibrated spontaneous regime (weight scale 4, noise weight 4.0,
background dc 0) and the real connectivity plus the documented synthetic
bridge, the network does not transfer a decodable word-class signal to its
readout. The mechanism, as embedded, has no measurable functional payoff for
this task at the tested budgets. This is reported as a negative result without
reframing, as the evaluation spec requires.

## What would need to change to revisit it

- A genuinely denser or differently-attributed visual route to the Kenyon-cell
  stage (the lobula is the weakest link: medulla→lobula is nearly empty in the
  release), so the bridge transmits real, strong, word-dependent activity; or
- a different task that the *available* real signal can express; or
- a larger stimulus-integration budget per word.

## Reproducing

```bash
python -m pip install -r requirements.lock.txt
python -m pip install -e .
python -m flyread fetch-data        # pin + verify FlyWire artifacts (CC-BY-NC)
python -m flyread run               # calibrate -> gate -> evaluate -> report
```

Every archived run under `runs/` includes its manifest (config, seed state,
checksums, annotation rules, calibration attempts, codegen target, hardware,
git commit). The 2-seed run that produced this report is
`runs/run-20261008T204756Z/`.