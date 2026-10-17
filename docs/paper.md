---
title: "Structural growth, not dopamine, drives category learning in a recoded *Drosophila* mushroom-body subcircuit"
subtitle: "A FlyWire model, with a caution on the untrained baseline"
author: "Nicolas Giavedoni"
date: "Draft manuscript"
---

# Abstract

We use a fixed subcircuit of the adult *Drosophila melanogaster* connectome (673
antennal-lobe projection neurons → 384 Kenyon cells → 4 mushroom-body output
neurons, with **zero artificial edges**) to classify four-letter words drawn from
four categories. Words are encoded as seeded per-category prototype patterns over
the input channels, not as orthographic or semantic forms. At the physiological
operating point the Kenyon-cell population carries no linearly decodable class
code (held-out probe 0.255) although the stimulus does (probe 1.000); raising the
gain of the `ALPN → Kenyon cell` transform (a "recode", to 20 or 50, with the
calibration rate ceiling widened to 100 Hz) restores the code (probe 0.950–1.000)
and the network then learns.

We are explicit about what the result is and is not. First, the **untrained
network already scores 0.33**, so we report significance against that internal
floor rather than against chance (0.25): the plain trained condition is only
marginally above it (not significant at gain 20; *p* = 0.020 at gain 50). Second,
the robust effect is **structural growth, not the dopamine-gated plasticity**:
growth-on conditions (`structural_on` 0.833, `dopamine_off` 0.785 at gain 50) far
exceed growth-off ones (`structural_off` 0.435, `dopamine_off_no_growth` 0.387;
paired *p* < 0.001), while switching dopamine off within the growth-on regime
changes nothing (*p* = 0.19). The best number, 0.833, therefore rests on an
exploratory structural-recruitment rule whose category guidance may construct a
class prototype rather than learn one; we flag this as the central open question
and propose the control that would settle it.

Three further departures bound the claim: the readout is a supervised linear
decoder fitted *after* training (not an online MBON readout), the 100 Hz
calibration ceiling lies far outside the physiological 0.5–15 Hz band, and the
extracted `DAN → Kenyon cell` edges are not actually simulated. We also document
a decoder-fit leak we introduced and fixed: a shuffled-label control that briefly
scored 0.615 now sits at or below the untrained floor. The positive contribution
is a causal, well-controlled localisation — the `ALPN → Kenyon cell` transform,
not the plastic readout, is the gate on whether this circuit can learn — and a
reproducible pipeline in which the result can be inspected and challenged.

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
documented rule can learn — and if so, what does the learning.

The study answers with a mechanism, not just a number. The real olfactory chain
learns the task once the gain of the `ALPN → Kenyon cell` transform is raised; at
the physiological operating point the class code is destroyed at that transform,
before the plastic readout ever sees it. Crucially, within the recoded regime the
learning is carried by the exploratory structural-growth rule, not by the
dopamine-gated weight update. We therefore report both a positive learning result
and an unusually explicit account of how fragile and what it rests on. Section 2
gives background. Section 3 details data, subcircuit, encoding, network, learning
rule, recode, and evaluation protocol. Section 4 reports the result, the causal
localisation with probes, the growth-versus-dopamine decomposition, and the
controls. Section 5 discusses mechanics, threats to validity, and what would
change the answer. Section 6 concludes.

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

The task assigns 4-letter words to four categories, but the words are **not**
processed as letters or meaning. Each category owns a fixed, seeded, disjoint
subset of the input neurons; each word drives 64 inputs from its own category
prototype plus 4 cross-category inputs at graded drive in [0.35, 1.0], with the
rest silent. The stimuli are therefore **category prototype patterns with partial
cross-category overlap** — a deliberately easy, low-dimensional task. "Word" below
denotes the token that indexes a prototype; no orthographic or semantic structure
is present. The mapping uses the selected input-stage size (673 inputs).

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
switched on/off as an ablation. **The recruitment rule is supervised by the
network's own predictions and transfers the winning unit's category, so it may
construct a class prototype directly rather than learn one; Section 5.2 makes this
the central threat to the interpretation of the headline number.**

## 3.4 Recoding the input transform

The `ALPN → Kenyon cell` projection carries a configurable pair gain (default 1.0,
the identity). The recode raises this gain to 20 or 50 and widens the calibration
rate ceiling (`calibration.max_mean_rate_hz`, default 15 Hz) to 100 Hz, so that
calibration may accept a scale at which the Kenyon cells carry the label (the
single global weight scale otherwise falls as the gain rises, holding Kenyon-cell
activity roughly constant and nullifying the gain). **The 100 Hz ceiling is far
outside the physiological 0.5–15 Hz band**; the recode is a diagnostic
intervention that isolates the transform as the bottleneck, not a claim about how
the circuit operates *in vivo*.

## 3.5 Readout and evaluation

The reported readout is a **supervised linear decoder fitted on a fixed
post-training feature pass** — a specified, documented departure from an online
MBON readout that isolates *representation* quality from *decoding* (an online
MBON-argmax readout is also produced and is no better; Section 5.3).

**Representation probes.** To separate "the network cannot learn" from "the
evaluation cannot detect learning", two linear probes are fitted on a *fresh
untrained* network and scored on held-out words with the same decoder protocol:
one on the input stage and one on the Kenyon-cell stage. A probe reports training,
k-fold cross-validation, and held-out accuracy per seed and aggregated. A
*positive-control gate* passes only if the input probe decodes above 0.90 and the
Kenyon-cell probe above chance + 0.05.

**Conditions.** The trained condition is nominally the main result. Controls are:
untrained; shuffled training labels (`shuffled_labels`); dopamine off
(`dopamine_off`); structural plasticity on/off (`structural_on`,
`structural_off`); supervised-pretrained readout (`trained_pretrained`); and two
isolation controls — `dopamine_off_no_growth` and `shuffled_labels_no_growth` —
which complete the dopamine × growth and shuffled × growth 2 × 2 read-outs. Note
that **`trained` and `structural_off` are the same configuration**
(`{"trained": true, "structural": false}`) and are retained as an internal
replicate; they are expected to agree, and any disagreement measures residual
run-order variance (Section 5.4).

**Statistics.** Because the untrained network is not at chance (Section 4.1), the
correct null for the learning effect is the **untrained internal control**, not
chance. We therefore report one-sample tests against chance *and* paired
per-seed contrasts against the untrained network and across the ablations.
Protocol: seeded 15/5 per-category train/test split; 600 training trials per seed;
95 % percentile-bootstrap CIs over seeds; significance at α = 0.05. Formal runs
use 10 seeds (1000–1009) with `eval_repeats = 3`.

**Leakage margin.** The supervised decoder is fitted on the labels each training
trial was actually *presented* with. A data-leakage warning is raised only when
the shuffled-label control exceeds the higher of chance and the untrained-network
floor by a configured margin (0.05).

## 3.6 Reproducibility

Seeds are independent, so each formal run executes one `evaluate` process per seed
(`flyread sweep`), resumes completed (seed, condition) pairs from a checkpoint, and
merges the shards into a single report. Kernels were compiled with Cython. All
archived runs carry their manifest.

# 4. Results

## 4.1 The untrained network is well above chance

Before interpreting any learning effect, the internal floor matters. The untrained
network scores **0.338** (gain 20) and **0.330** (gain 50) — a full 8–9 points
above the 0.25 chance level (Table 1). This is the recoded representation's own
decodability, not learning. Testing against chance therefore inflates every
significance value; the meaningful comparison is against the untrained control.

## 4.2 The recoded network learns, but the effect is marginal

Table 1 gives the two recoded runs (`runs/recode-v2-merged`, gain 20;
`runs/recode-50-v2-merged`, gain 50; 10 seeds each); both pass the
positive-control gate and carry no leakage warning.

: **Table 1.** Held-out accuracy by condition, 10 seeds, chance 0.25, untrained
floor in bold. Both *p*-values are shown: one-sample vs chance, and paired vs the
untrained control.

| condition | gain 20 | gain 50 |
|---|---|---|
| **untrained (internal floor)** | **0.338** | **0.330** |
| trained | 0.363 (chance *p* = 0.0013; **vs untrained *p* = 0.39**) | 0.418 (chance *p* = 8.5 × 10⁻⁵; vs untrained *p* = 0.020) |
| structural_off (≡ trained config) | 0.370 | 0.435 |
| shuffled_labels | 0.330 | 0.245 |
| shuffled_labels_no_growth | 0.230 | 0.233 |
| dopamine_off | 0.597 | 0.785 |
| dopamine_off_no_growth | 0.357 | 0.387 |
| trained_pretrained | 0.512 | 0.340 |
| structural_on | **0.608** | **0.833** |

The plain trained condition lies only **+0.025 above the untrained floor at gain
20 (paired *p* = 0.39, not significant)** and +0.088 at gain 50 (paired
*p* = 0.020, marginal). Its apparent significance against chance (0.0013 / 8.5 ×
10⁻⁵) does not survive comparison with the internal control. The large, robust
effects in Table 1 are the growth-on conditions; we analyse them next.

## 4.3 The robust effect is structural growth, not dopamine

Reading the 2 × 2 ablations isolates the effective mechanism (Table 2). Accuracy
follows the growth switch, not the dopamine switch.

: **Table 2.** Paired per-seed contrasts (n = 10). Positive Δ means the first
condition is higher.

| contrast | isolates | Δ acc (gain 20) | *p* | Δ acc (gain 50) | *p* |
|---|---|---|---|---|---|
| trained − untrained | learning above floor | +0.025 | 0.39 | +0.088 | 0.020 |
| structural_on − structural_off | **growth effect** | +0.238 | < 0.001 | +0.398 | < 0.001 |
| structural_on − dopamine_off | **dopamine effect** | +0.012 | 0.75 | +0.048 | 0.19 |
| dopamine_off − dopamine_off_no_growth | growth effect (dop off) | +0.240 | 0.001 | +0.398 | < 0.001 |

- **Growth effect is large and robust.** `structural_on` (0.833) exceeds
  `structural_off` (0.435) by +0.398 at gain 50 (paired *p* < 0.001) and by +0.238
  at gain 20; the same gap appears within the dopamine-off arm.
- **Dopamine effect is negligible.** Within the growth-on regime, switching
  dopamine off (`dopamine_off` 0.785 vs `structural_on` 0.833) changes accuracy by
  +0.048 (paired *p* = 0.19; +0.012, *p* = 0.75 at gain 20). The dopamine-gated
  weight rule contributes essentially nothing once growth is enabled.

Structural plasticity executes as designed: 300 reserve neurons are activated and
≈ 13.2–13.9 k synapses created (150 retired) per seed, bringing the network from
1,065 to 1,365 neurons. The gain-50 `structural_on` condition is the study's best
number (**0.833**), and by the decomposition above it is attributable to the
recruitment rule, not the dopamine rule.

## 4.4 The gain restores the Kenyon-cell class code

The representation probes show the recode works by restoring the code, not by a
side-effect on the readout (Table 3). At the physiological operating point the
class code is lost at the `ALPN → Kenyon cell` transform: the stimulus decodes
perfectly (input probe 1.000) but the Kenyon-cell population does not (probe
0.255), and no readout recovers it — a supervised-pretrained readout that fits the
Kenyon-cell features to 1.000 still generalises at chance (0.268), and a task with
trivially separable categories remains at chance. Raising the gain restores the
Kenyon-cell code: CV decodability rises to **0.950** at gain 20 and **1.000** at
gain 50.

: **Table 3.** Kenyon-cell representation probe (k-fold CV accuracy) versus the
`ALPN → Kenyon cell` gain.

| ALPN → Kenyon gain | input probe | Kenyon-cell probe | trained held-out acc |
|---|---|---|---|
| 1 (physiological) | 1.000 | 0.255 | 0.262 |
| 20 | 1.000 | 0.950 | 0.363 |
| 50 | 1.000 | 1.000 | 0.418 |

The gating is causal: the probe locates where the code is lost, and the single
intervention that restores it is the one that enables learning. This localisation
— not the absolute accuracy — is the study's most robust contribution.

## 4.5 The learned signal is not leakage

A recoded representation is decodable even without learning, so the shuffled-label
control sits at the untrained floor (0.330 at gain 20) rather than at raw chance;
this is expected and is why the leakage margin is untrained-relative. We confirmed
no leaking pipeline with two isolation controls
(`runs/recode-v2-extra-merged`, `runs/recode-50-extra-merged`) in which labels are
shuffled *and* structural growth is switched off: both sit at or below chance —
**0.230** (*p* = 0.75) at gain 20 and **0.233** (*p* = 0.72) at gain 50 — with no
leakage warning. The residual above chance in the growth-on conditions is
attributable to structural recruitment, not to the evaluation pipeline leaking
label information.

## 4.6 A decoder-fit leak, caught and fixed

An earlier recode attempt (`recode-v1`) produced a trained accuracy of 0.363 but
its shuffled-label control scored **0.615**, raising the leakage warning and
invalidating the run. The cause was a harness bug: the supervised decoder was
fitted on the dataset's (true) labels in *every* condition, including the shuffled
control, so the control had a classifier reading true labels off the network's
training outputs — a mapping the network was never taught. The fix fits the
decoder on the labels each trial was actually *presented* with. This is a no-op
for every non-shuffled condition (there the presented labels are the dataset
labels), so all other numbers stand; the shuffled control dropped to the untrained
floor. A regression test reproduces the leak and guards against it.

# 5. Discussion

## 5.1 The input transform gates learning

The gating mechanism is precise. At the physiological operating point the class
code is lost at the `ALPN → Kenyon cell` transform, and this is not the output
drive (the MBON stage is not silent), not the input encoding (the input probe is
1.000), and not the readout or its credit rule (a supervised-pretrained readout
that fits the Kenyon-cell features to 1.000 still generalises at chance, and a
separable task is equally at chance). The Kenyon-cell population is where the
class code is destroyed (probe 0.255). Raising that transform's gain restores the
code (probe 0.950 → 1.000). In this calibrated regime the `ALPN → Kenyon cell`
transform — not the plastic readout — is the gate on learning, and that gate can
be opened by a single, interpretable parameter.

## 5.2 What the headline number actually rests on

The best result (0.833) is produced by the growth-on conditions, and the
structural-recruitment rule is a **supervised, exploratory** mechanism: a reserve
neuron is recruited near the most successful readout unit and *inherits that
unit's category* and most of its inputs. It is therefore plausible that the rule
constructs a class prototype — effectively labelling new units with the class the
network most recently guessed correctly — rather than learning a class code from
the data. Three observations make this a live concern rather than a technicality:
the effect is large and abrupt (growth off collapses accuracy to the untrained
floor), it is insensitive to the dopamine signal that is supposed to supply the
learning, and the recruitment source is the network's own prediction. The control
that would settle it is to break the category inheritance: recruit with
**shuffled or random category labels** (or from a random unit), and test whether
the learning survives. We did not run this control, and we flag its absence as the
main threat to the interpretation of 0.833. The plain dopamine-gated trained
condition, by contrast, is only marginally above the untrained floor; the honest
summary is that this circuit learns *something* under the recode, and that the
large number is the growth rule's.

## 5.3 The readout and the operating point do part of the work

Two departures inflate the result relative to a claim about online learning. The
reported accuracy uses a **supervised linear decoder fitted after training** on a
fixed feature pass; an online MBON-argmax readout is also produced and is no
better, but the headline number is the post-hoc decoder's. And the recode operates
at a **100 Hz calibration ceiling**, far outside the physiological 0.5–15 Hz band.
Both are diagnostic choices that isolate the transform as the bottleneck; neither
should be read as a claim about the circuit *in vivo*.

## 5.4 A wiring caveat and a reproducibility caveat

The real `DAN → Kenyon cell` edges are extracted but are **not** simulated:
`build_network` wires consecutive stage pairs, within-stage recurrence, and the
plastic `mushroom body → output` pair. Because `reinforcement` is configured after
`mushroom_body`, `reinforcement → mushroom_body` is a backward pair and is
dropped; the reward is applied as a global scalar gated by the real DANs' spike
counts rather than through the real synapses. The config's "real DAN → Kenyon
cell" pathway is therefore not the one trained; reordering the stages would
exercise those edges.

Separately, `trained` and `structural_off`, which share the identical
configuration, do not produce identical results (0.363 vs 0.370; 0.418 vs 0.435).
The small differences are consistent with residual run-order dependence in RNG
consumption across conditions rather than with a real effect, but they indicate
that per-condition random state is not perfectly isolated. We report them so the
identical-condition replicates are not mistaken for a contrast.

## 5.5 Limitations

- Task and network are small by design (CPU-only reproducibility) and the stimuli
  are low-dimensional prototype patterns, so the results speak to this calibrated
  regime and this rule, not to mushroom-body or word learning in general.
- The headline number depends on the exploratory structural-recruitment rule
  (Section 5.2), whose prototype-building potential is uncontrolled here.
- The reported readout is a supervised post-training decoder; the recode leaves
  the physiological band (Section 5.3).
- All conditions share a single calibration per run; a broader sweep of operating
  points was not performed.

# 6. Conclusion

A real, synapse-level FlyWire subcircuit with zero artificial edges can be made to
categorise prototype patterns once a single input transform is recoded: raising
the `ALPN → Kenyon cell` gain restores a Kenyon-cell class code that the
physiological operating point destroys (input probe 1.000, Kenyon-cell probe 0.255
→ 0.95–1.00). We are deliberately conservative about the resulting accuracy. The
untrained network already sits at 0.33, and against that internal floor the plain
dopamine-gated trained condition is only marginally better (not significant at
gain 20; *p* = 0.020 at gain 50). The large, robust effect is structural growth:
growth-on conditions reach 0.833 and exceed growth-off ones by +0.40 (paired
*p* < 0.001), while switching dopamine off changes nothing (*p* = 0.19). The
headline number therefore rests on an exploratory recruitment rule that may
construct a class prototype rather than learn one — the control that would settle
this (recruit with shuffled categories) is the first experiment we would run next.
The study's durable contribution is a causal localisation of the learning gate to
the `ALPN → Kenyon cell` transform, a self-corrected leakage control, and a
reproducible pipeline in which these claims can be inspected and challenged.

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
