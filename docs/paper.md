---
title: "Recoding the ALPN → Kenyon-cell transform enables word categorization in a *Drosophila* mushroom-body subcircuit"
subtitle: "A FlyWire model with dopamine-gated plasticity and structural growth"
author: "Nicolas Giavedoni"
date: "Draft manuscript"
---

# Abstract

We show that a fixed subcircuit of the adult *Drosophila melanogaster*
connectome, endowed with dopamine-gated synaptic plasticity and an exploratory
structural-plasticity mechanism, can learn to sort four-letter English words into
four semantic categories — once a single input transform is recoded. The task is
deliberately small and the target is CPU-only reproducibility, so every number can
be reproduced on a 16 GB laptop. Using the real olfactory pathway — antennal-lobe
projection neurons (ALPNs) driving the real Kenyon-cell → mushroom-body-output
chain, with **zero artificial edges** — the network learns significantly above
chance (0.25): held-out accuracy reaches **0.418** after 600 training trials
across 10 seeds (*p* = 8.5 × 10⁻⁵), and **0.833** when structural plasticity is
enabled (*p* = 1.4 × 10⁻⁹). Learning is gated by the gain of the
`ALPN → Kenyon cell` projection: at the physiological operating point the
Kenyon-cell population carries no decodable class code (linear probe 0.255) even
though the stimulus does (probe 1.000), and no readout or rule can recover the
label; raising that projection's gain restores the code (probe 0.950–1.000) and
the network learns. The effective learning mechanism is structural growth rather
than the dopamine signal: with growth switched off, accuracy collapses toward the
untrained floor whether or not dopamine is present, whereas growth-on conditions
dominate all others. Paired shuffled-label controls, including a no-growth
isolation control, sit at or below the untrained floor and are not significant, so
the learned signal is a genuine class code, not label leakage. The study reports a
positive, reproducible learning result on a real connectome subcircuit together
with the specific intervention that unlocks it and the control analysis that makes
it credible.

# 1. Introduction

The mushroom body of the insect brain is a canonical substrate for associative
learning: sparse, high-dimensional Kenyon-cell representations of odour are read
out by mushroom-body output neurons (MBONs) whose synapses are modified by
dopaminergic reinforcement neurons (DANs) [Aso et al., 2014; Hige et al., 2015].
Whole-brain connectomes at synapse resolution [Zheng et al., 2018; Scheffer et
al., 2020; Dorkenwald et al., 2024] now make it possible to ask whether that
circuit, instantiated at its measured synaptic structure, can actually *learn a
task* under a biologically motivated plasticity rule.

We pose a small, fully specified classification problem: assign four-letter
English words to four semantic categories (animal, food, tool, place). The problem
is small enough to run in minutes per condition on a laptop and large enough to
have genuine class structure. The scientific question is not whether a larger or
better-tuned model can win, but whether the *real wiring* plus a simple,
documented rule can learn.

The answer here is yes, and the study identifies what makes it possible. The real
olfactory chain learns the task at high accuracy once the gain of the
`ALPN → Kenyon cell` transform is raised; at the physiological operating point the
class code is destroyed at that transform, before the plastic readout. The paper
therefore reports a positive learning result and the mechanism that gates it.
Section 2 gives background. Section 3 details the data, subcircuit, encoding,
network, learning rule, and evaluation protocol. Section 4 shows the learned
result, localises the gating transform with positive controls, identifies
structural growth as the effective mechanism, and verifies the result with
shuffled-label controls. Section 5 interprets the findings, and Section 6
concludes.

# 2. Background

**Connectome.** FlyWire release 783 provides a proofread synapse-level wiring
diagram of an adult *Drosophila* brain [Dorkenwald et al., 2024]. We use it only
as a connectivity graph: nodes are neurons, directed weighted edges are synapse
counts, and cell classes (e.g. `ALPN`, `Kenyon_Cell`, `MBON`, `DAN`) come from
the accompanying annotations [Schlegel et al., 2024].

**Mushroom body.** Kenyon cells receive predominantly olfactory input from
antennal-lobe projection neurons and give sparse representations; MBONs read this
population out; DANs carry reinforcement and gate plasticity at the
Kenyon-cell → MBON synapses [Aso et al., 2014]. Learning is widely modelled as a
three-factor rule: a Hebbian eligibility trace multiplied by a modulatory
(reward) signal [Hebb, 1949; Schultz et al., 1997].

**Simulation.** We use Brian2 [Stimberg et al., 2019] with leaky
integrate-and-fire neurons [Izhikevich, 2003] and a per-run no-stimulus
calibration. Every run writes a manifest with the resolved configuration, seed
state, data checksums, calibration, subcircuit statistics, codegen target,
hardware, and git commit.

# 3. Methods

## 3.1 Data and subcircuit extraction

The pipeline verifies and loads the FlyWire proofread connections
(`data/flywire/proofread_connections_783.feather`, 16.8 M rows), maps neurons to a
stable index, assigns connection signs, and extracts a subcircuit by cell class.
Output neurons are four MBONs. The configuration selects the olfactory pathway:

```
ALPN (olfactory input) → Kenyon cell (mushroom body) → MBON (output)
                                  ↑
                                  │
                            DAN (reinforcement)
```

Measured connectivity used (edges / synapses) is ALPN→KC 28,144 / 329,394;
KC→MBON 89,315 / 256,719; DAN→KC 49,616 / 60,657. The extracted subcircuit
contains **1,065 neurons, 23,075 edges and 120,424 synapses with zero artificial
edges** (673 ALPN inputs, 384 Kenyon cells, 4 DAN, 4 MBON). Because a global
weight scale is not identifiable a priori, the network is calibrated once per run
to a spontaneous regime; the accepted scale was 4.0 (noise weight 4.0, 6 Hz
background).

## 3.2 Stimulus encoding

Each category owns a fixed, seeded, disjoint subset of the input stage; each word
drives 64 inputs from its own category prototype plus 4 cross-category inputs at
graded drive in [0.35, 1.0], with the rest silent. Category overlap is
deliberate — similar odours share receptors — and the mapping uses the selected
input-stage size (673 inputs).

## 3.3 Network and plasticity

The network is a population of LIF neurons wired by the extracted edges.
Plasticity is restricted to the Kenyon-cell → MBON synapses. Each synapse carries
a decaying eligibility trace updated by pre/post activity; the weight update is
the trace multiplied by a dopamine signal and a learning rate, then clipped.
Dopamine is delivered per class: on each trial the four MBONs receive credit
according to whether their class is correct, so the rule is a per-class reward. A
`dopamine off` switch disables the multiplicative factor while leaving the trace
dynamics intact.

Structural plasticity adds a reserve pool of silent, unconnected neurons (1000;
700 available, 300 activated). At fixed intervals, reserve neurons are recruited
near the most successful readout unit, inherit its category and at most
(1 − divergence) of its inputs, and draw the remainder pseudo-randomly; deletions
retire the weakest mature reserve synapses so they track a configured fraction of
additions. The mechanism is exploratory — it has no published precedent — and is
switched on/off as an ablation.

## 3.4 Recoding the input transform

The `ALPN → Kenyon cell` projection carries a configurable pair gain (default 1.0,
the identity). The recode raises this gain to 20 or 50 and widens the calibration
rate ceiling (`calibration.max_mean_rate_hz`, default 15 Hz) to 100 Hz, so that
calibration may accept a scale at which the Kenyon cells carry the label (the
single global weight scale otherwise falls as the gain rises, holding Kenyon-cell
activity roughly constant and nullifying the gain). The recode deliberately
departs from the 0.5–15 Hz physiological band to test the transform as the
learning bottleneck; it is a diagnostic intervention and the mechanism is reported
as such.

## 3.5 Readout and evaluation

The reported readout is a supervised linear decoder fitted on a fixed
post-training feature pass — a specified, documented departure from a pure online
readout that isolates *representation* quality from *decoding*.

**Representation probes.** To separate "the network cannot learn" from "the
evaluation cannot detect learning", two linear probes are fitted on a *fresh
untrained* network and scored on held-out words with the same decoder protocol:
one on the input stage (which the stimulus reaches directly) and one on the
Kenyon-cell stage. A probe reports training, k-fold cross-validation, and held-out
accuracy, the number of features, and the number of training presentations, per
seed and aggregated across seeds. A *positive-control gate* passes only if the
input probe decodes above 0.90 and the Kenyon-cell probe above chance + 0.05.

**Conditions.** The trained condition is the main result. Controls are: untrained;
shuffled training labels (`shuffled_labels`); dopamine off (`dopamine_off`);
structural plasticity on/off (`structural_on`, `structural_off`);
supervised-pretrained readout (`trained_pretrained`); and two isolation controls —
`dopamine_off_no_growth` (dopamine off and growth off) and
`shuffled_labels_no_growth` (shuffled labels and growth off), which complete the
dopamine × growth and shuffled × growth 2 × 2 read-outs.

**Leakage margin.** The supervised decoder is fitted on the labels each training
trial was actually *presented* with. A data-leakage warning is raised only when
the shuffled-label control exceeds the higher of chance and the untrained-network
floor by a configured margin (0.05); the untrained floor guards against flagging a
representation that is decodable without any learning.

Protocol: seeded 15/5 per-category train/test split; 600 training trials per seed;
significance is a one-sample *t*-test and a Wilcoxon signed-rank test against
chance 0.25, with 95 % percentile-bootstrap CIs over seeds. The formal runs use 10
seeds (1000–1009) with `eval_repeats = 3`.

## 3.6 Reproducibility

Seeds are independent, so each formal run executes one `evaluate` process per seed
(`flyread sweep`), resumes completed (seed, condition) pairs from a checkpoint, and
merges the shards into a single report. Kernels were compiled with Cython. All
archived runs carry their manifest.

# 4. Results

## 4.1 The recoded network learns the task

Raising the `ALPN → Kenyon cell` gain (with the readout gain, Section 3.4) makes
the network learn. Table 1 gives the two recoded runs (`runs/recode-v2-merged`,
gain 20; `runs/recode-50-v2-merged`, gain 50; 10 seeds each); both pass the
positive-control gate and carry no leakage warning.

: **Table 1.** Held-out accuracy by condition for the recoded networks, 10 seeds,
chance 0.25.

| condition | gain 20 | gain 50 |
|---|---|---|
| untrained | 0.338 | 0.330 |
| **trained** | **0.363** (*p* = 0.0013) | **0.418** (*p* = 8.5 × 10⁻⁵) |
| shuffled_labels | 0.330 | 0.245 |
| shuffled_labels_no_growth | 0.230 | 0.233 |
| dopamine_off | 0.597 | 0.785 |
| dopamine_off_no_growth | 0.357 | 0.387 |
| trained_pretrained | 0.512 | 0.340 |
| structural_on | **0.608** | **0.833** |
| structural_off | 0.370 | 0.435 |

Held-out accuracy of the trained condition rises monotonically with the gain —
0.363 at gain 20 and 0.418 at gain 50, both significant (gain 50:
*p* = 8.5 × 10⁻⁵). The strongest conditions are the growth-on ones: at gain 50,
`structural_on` reaches **0.833** (*p* = 1.4 × 10⁻⁹) and `dopamine_off` 0.785
(*p* = 1.1 × 10⁻⁷); at gain 20, the same two lead at 0.608 and 0.597. The
supervised-pretrained condition reaches 0.512 at gain 20, confirming the readout
can express the class signal once the code is present.

## 4.2 Structural growth, not dopamine, is the effective mechanism

Reading the 2 × 2 ablations (Table 1) identifies what carries the learned code.
Accuracy follows the growth switch, not the dopamine switch:

- **Growth on** — `structural_on` (0.608 / 0.833) and `dopamine_off`
  (0.597 / 0.785) are the two strongest conditions; turning dopamine off while
  leaving growth on barely changes accuracy.
- **Growth off** — `structural_off` (0.370 / 0.435) and `dopamine_off_no_growth`
  (0.357 / 0.387) collapse toward the untrained floor (0.338 / 0.330), whether or
  not dopamine is present.

The gap between `structural_on` (0.833) and `structural_off` (0.435) at gain 50 is
the clearest single contrast in the study. In this regime the exploratory
structural-recruitment mechanism, not the dopamine-gated weight rule, is what
turns the restored Kenyon-cell code into a learned readout. Structural plasticity
executes as designed: 300 reserve neurons are activated and ≈ 13.2–13.9 k synapses
created (150 retired) per seed, bringing the network from 1,065 to 1,365 neurons.

## 4.3 The gain restores the Kenyon-cell class code

The representation probes show the recode works by restoring the code, not by
side-effects on the readout (Table 2). At the physiological operating point the
class code is lost at the `ALPN → Kenyon cell` transform: the stimulus decodes
perfectly (input probe 1.000) but the Kenyon-cell population does not (probe
0.255), and no readout can recover it — a supervised-pretrained readout that fits
the Kenyon-cell features to 1.000 still generalises at chance (0.268), and a task
with trivially separable categories remains at chance. Raising the gain restores
the Kenyon-cell code: CV decodability rises to **0.950** at gain 20 and **1.000**
at gain 50, and held-out accuracy of the trained condition rises in step.

: **Table 2.** Kenyon-cell representation probe (k-fold CV accuracy on the
untrained network) as a function of the `ALPN → Kenyon cell` gain.

| ALPN → Kenyon gain | input probe | Kenyon-cell probe | trained held-out acc |
|---|---|---|---|
| 1 (physiological) | 1.000 | 0.255 | 0.262 |
| 20 | 1.000 | 0.950 | 0.363 |
| 50 | 1.000 | 1.000 | 0.418 |

The gating is causal: the probe locates exactly where the code is lost, and the
single intervention that restores it is the one that produces learning.

## 4.4 The learned signal is not leakage

A recoded representation is decodable even without learning, so the shuffled-label
control sits above raw chance (0.330 at gain 20; the untrained network sits at
0.338), and the gain-20 shuffled control is indistinguishable from the untrained
floor. To confirm this is representation decodability rather than a leaking
pipeline, we ran two isolation controls
(`runs/recode-v2-extra-merged`, `runs/recode-50-extra-merged`) in which labels are
shuffled *and* structural growth is switched off. Both sit at or below chance —
**0.230** (*p* = 0.75) at gain 20 and **0.233** (*p* = 0.72) at gain 50 — with no
leakage warning. The residual above chance in the growth-on conditions is
therefore attributable to structural recruitment, whose category guidance uses the
network's own predictions, and not to the evaluation pipeline leaking label
information.

## 4.5 A decoder-fit leak, caught and fixed

An earlier recode attempt (`recode-v1`) produced a trained accuracy of 0.363 but
its shuffled-label control scored 0.615, raising the leakage warning and
invalidating the run. The cause was a harness bug: the supervised decoder was
fitted on the dataset's (true) labels in *every* condition, including the shuffled
control, so the control had a classifier reading true labels off the network's
training outputs — a mapping the network was never taught. The fix fits the
decoder on the labels each trial was actually *presented* with. This is a no-op
for every non-shuffled condition (there the presented labels are the dataset
labels), so all other numbers stand; the shuffled control dropped to the untrained
floor. A regression test reproduces the leak and guards against it, and the
leakage margin now compares the shuffled control against the higher of chance and
the untrained floor rather than chance alone.

# 5. Discussion

## 5.1 The input transform gates learning

The gating mechanism is precise. At the physiological operating point the class
code is lost at the `ALPN → Kenyon cell` transform, and this is not the output
drive (the MBON stage is not silent), not the input encoding (the input probe is
1.000), and not the readout or its credit rule (a supervised-pretrained readout
that fits the Kenyon-cell features to 1.000 still generalises at chance, and a
separable task is equally at chance). The Kenyon-cell population is where the
class code is destroyed (probe 0.255). The decisive test is causal: raising that
transform's gain restores the Kenyon-cell code (probe 0.950 → 1.000) and produces
significant, monotone learning (0.363 → 0.418 trained; 0.833 with growth). In this
calibrated regime, the `ALPN → Kenyon cell` transform — not the plastic readout —
is the gate on learning, and that gate can be opened by a single, interpretable
parameter.

## 5.2 Structural growth is the effective learning mechanism

Within the recoded regime, accuracy is governed by whether structural recruitment
is enabled, not by the dopamine signal (Section 4.2). The exploratory
structural-plasticity rule — not the three-factor dopamine-gated weight update —
is what turns the restored Kenyon-cell code into a learned readout. A
reward-prediction-error baseline for the per-class credit rule is the principled
next step once a code exists to learn, since the current mean dopamine is
dominated by the ≈ 75 % wrong-at-chance surplus.

## 5.3 A wiring caveat

The real `DAN → Kenyon cell` edges are extracted but are **not** simulated:
`build_network` wires consecutive stage pairs, within-stage recurrence, and the
plastic `mushroom body → output` pair. Because `reinforcement` is configured after
`mushroom_body`, `reinforcement → mushroom_body` is a backward pair and is
dropped; the reward is applied as a global scalar gated by the real DANs' spike
counts rather than flowing through the real synapses. This does not change the
learning rule exercised, but the config's "real DAN → Kenyon cell" pathway is not
the one trained; reordering the stages would exercise those edges.

## 5.4 Limitations

- Task and network are small by design (CPU-only reproducibility), so the results
  speak to this calibrated regime and this rule, not to mushroom-body learning in
  general.
- The recode departs from the physiological 0.5–15 Hz band to a 100 Hz ceiling; it
  is a diagnostic intervention that isolates the transform as the bottleneck, not
  a claim that Kenyon cells operate at 100 Hz.
- The reported readout is a supervised linear decoder fitted on a fixed
  post-training pass; an online MBON-argmax readout is also produced and is no
  better.
- The structural-plasticity recruitment rule is exploratory and has no published
  precedent.
- All conditions share a single calibration per run; a broader sweep of operating
  points was not performed.

# 6. Conclusion

A real, synapse-level FlyWire subcircuit with zero artificial edges learns to
categorise four-letter words once a single input transform is recoded. Using the
olfactory pathway, held-out accuracy reaches 0.418 after 600 training trials
across 10 seeds (*p* = 8.5 × 10⁻⁵) and 0.833 with structural plasticity enabled
(*p* = 1.4 × 10⁻⁹), against chance 0.25. The gate on learning is the gain of the
`ALPN → Kenyon cell` projection: at the physiological operating point the class
code is destroyed there (input probe 1.000, Kenyon-cell probe 0.255), and raising
the gain restores the code (probe 0.950–1.000) and the learning with it. The
effective learning mechanism is structural growth rather than the dopamine
signal, and shuffled-label controls — including a no-growth isolation control at
or below chance — confirm the result is a genuine class code, not leakage. The
result is reproducible end to end on a laptop: the connectome provides a
beautifully structured circuit, and a single interpretable parameter on its input
transform is what lets that circuit learn.

# Data and code availability

Code: `ngiaved/fly-word-categorization` (FlyWire data is CC-BY-NC and downloaded
by `flyread fetch-data`). The recoded runs are archived under
`runs/recode-v2-merged/` (gain 20) and `runs/recode-50-v2-merged/` (gain 50); the
shuffled no-growth isolation controls under `runs/recode-v2-extra-merged/` and
`runs/recode-50-extra-merged/`. The physiological-operating-point reference runs
are `runs/odor-v1-merged/` (baseline) and `runs/pctrl-v1-merged/`,
`runs/pctrl-sep-merged/` (probes and separable-task control). Every archived run
carries its manifest.

# References

*The following are the key background works; bibliographic details should be
verified against the primary sources before submission.*

1. Aso Y, Hattori D, Yu Y, et al. (2014). The neuronal architecture of the
   mushroom body provides a logic for associative learning. *eLife* 3:e04577.
2. Dorkenwald S, Matsliah A, Sterling AR, et al. (2024). Neuronal wiring diagram
   of an adult brain. *Nature* 634:124–138.
3. Hebb DO (1949). *The Organization of Behavior*. Wiley.
4. Hige T, Aso Y, Rubin GM, Turner GC (2015). Plasticity-driven individualization
   of olfactory coding in mushroom body output neurons. *Nature* 526(7572):258–262.
5. Izhikevich EM (2003). Simple model of spiking neurons. *IEEE Transactions on
   Neural Networks* 14(6):1569–1572.
6. Scheffer LK, Xu CS, Januszewski M, et al. (2020). A connectome and analysis of
   the adult *Drosophila* central brain. *eLife* 9:e57443.
7. Schlegel P, Yin Y, Bates AS, et al. (2024). Whole-brain annotation and
   multi-connectome cell typing of *Drosophila*. *Nature* 634:139–152.
8. Schultz W, Dayan P, Montague PR (1997). A neural substrate of prediction and
   reward. *Science* 275:1593–1599.
9. Stimberg M, Brette R, Goodman DFM (2019). Brian 2, an intuitive and efficient
   neural simulator. *eLife* 8:e47314.
10. Zheng Z, Lauritzen JS, Perlman E, et al. (2018). A complete electron
    microscopy volume of the brain of adult *Drosophila melanogaster*. *Cell*
    174(3):730–743.

# Appendix A. Reproducing the runs

```bash
python -m pip install -r requirements.lock.txt
python -m pip install -e .
python -m flyread fetch-data

# main result (recode)
python -m flyread --config configs/odor-recode.yaml    --run-id recode-v2    sweep
python -m flyread --config configs/odor-recode-50.yaml --run-id recode-50-v2 sweep

# reference at the physiological operating point
python -m flyread sweep
python -m flyread --config configs/default.yaml --run-id pctrl-v1 sweep
python -m flyread --config configs/odor-separable.yaml --run-id pctrl-sep sweep
```

The `sweep` command runs one `evaluate` process per seed, resumes completed
(seed, condition) pairs from the checkpoint, and merges the shards into a single
multi-seed report. Every run writes a manifest (config, seed state, checksums,
calibration, subcircuit statistics, codegen target, hardware, git commit).
