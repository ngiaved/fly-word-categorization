# A dopamine-gated mushroom-body readout does not learn categorical word structure in a FlyWire subcircuit

**A reproducible negative result**

*Author: Nicolas Giavedoni* · *Draft manuscript* · *Code and data: `ngiaved/fly-word-categorization`*

---

## Abstract

We asked whether a fixed subcircuit of the adult *Drosophila melanogaster*
connectome, endowed with dopamine-gated synaptic plasticity and an exploratory
structural-plasticity mechanism, can learn to sort four-letter English words
into four semantic categories. The task is deliberately small and the target
is CPU-only reproducibility, so every number below can be reproduced on a
16 GB laptop. Two stimulus routes were tested. First a visual route, mapping
rendered words onto photoreceptors; this was abandoned when the analysis of
the connectome showed that the mushroom body receives essentially no
optic-lobe input (1 lobula→Kenyon-cell synapse over all candidates in FlyWire
release 783), so the required pathway does not exist and would have to be
fabricated. Second an olfactory route, in which real antennal-lobe projection
neurons drive the real Kenyon-cell → mushroom-body-output-neuron chain, with
**zero artificial edges**. Across the formal evaluation (10 seeds, 600 training
trials each, 6 conditions), the trained olfactory readout reached a held-out
accuracy of **0.262** (95 % CI [0.237, 0.287]) against a chance level of 0.25,
which is not significantly above chance (one-sample *t*-test *p* = 0.21;
Wilcoxon signed-rank *p* = 0.24). Every control condition — untrained, shuffled
training labels, dopamine off, structural plasticity on/off — was likewise
indistinguishable from chance at α = 0.01. Because the output stage is *not*
silent in the trained condition (no-response rate 0.257), the failure is not an
under-driven readout but a loss of decodable class information upstream of it.
Positive controls then localise that loss precisely. A supervised linear probe
decodes the category perfectly from the stimulus stage (held-out accuracy 1.000
on unseen words) but at chance from the Kenyon-cell population (0.255,
95 % CI [0.227, 0.287]), so the positive-control gate fails: the class signal
that demonstrably reaches the network is gone before the plastic readout. A
supervised-pretrained readout — initialised from a perfect fit on the
Kenyon-cell features (training accuracy 1.000) — still generalises at chance,
and a second task with perfectly separable categories reproduces the same null
(probe: input 1.000, Kenyon cell 0.227). The Kenyon-cell code the readout would
have to use does not linearly carry the label, which is why no learning rule or
pretraining recovers it. We report this as a negative result without reframing,
and release the full pipeline, manifests, and the failing runs alongside the
code.

## 1. Introduction

The mushroom body of the insect brain is a canonical substrate for associative
learning: sparse, high-dimensional Kenyon-cell representations of odour are
read out by mushroom-body output neurons (MBONs) whose synapses are modified by
dopaminergic reinforcement neurons (DANs) [Aso et al., 2014; Hige et al.,
2015]. The recent availability of an adult *Drosophila* whole-brain connectome
at synapse resolution [Zheng et al., 2018; Scheffer et al., 2020; Dorkenwald
et al., 2024] makes it possible to ask whether that circuit, instantiated
at its measured synaptic structure, can actually *learn a task* under a
biologically motivated plasticity rule.

We pose a small, fully specified classification problem: assign four-letter
English words to four semantic categories (animal, food, tool, place). The
problem is small enough to run in minutes per condition on a laptop and large
enough to have a genuine class structure. The scientific question is not
whether a larger or better-tuned model can win, but whether the *real wiring*
plus a simple, documented rule suffices. A clear null is informative here:
it localises the failure and constrains what the connectome alone provides.

This paper reports that null, and the process of arriving at a task the
connectome can actually express. Section 2 gives background. Section 3 details
the data, subcircuit, encoding, network, learning rule, and evaluation
protocol. Section 4 reports results for both routes. Section 5 interprets the
failure and states its limits. Section 6 concludes.

## 2. Background

**Connectome.** FlyWire release 783 provides a proofread synapse-level wiring
diagram of an adult *Drosophila* brain [Dorkenwald et al., 2024]. We use it
only as a connectivity graph: nodes are neurons, directed weighted edges are
synapse counts, and cell classes (e.g. `ALPN`, `Kenyon_Cell`, `MBON`, `DAN`)
come from the accompanying annotations [Schlegel et al., 2024].

**Mushroom body.** Kenyon cells receive predominantly olfactory input from
antennal-lobe projection neurons and give sparse representations; MBONs read
this population out; DANs carry reinforcement and gate plasticity at the
Kenyon-cell → MBON synapses [Aso et al., 2014]. Learning is widely modelled as
a three-factor rule: a Hebbian eligibility trace multiplied by a modulatory
(reward) signal [Hebb, 1949; Schultz et al., 1997].

**Simulation.** We use Brian2 [Stimberg et al., 2019] with leaky
integrate-and-fire neurons [Izhikevich, 2003] and a per-run no-stimulus
calibration. Concretely, all reported numbers come from a reproducible
pipeline in which every run writes a manifest with the resolved configuration,
seed state, data checksums, calibration, subcircuit statistics, codegen target,
hardware, and git commit.

## 3. Methods

### 3.1 Data and subcircuit extraction

The pipeline verifies and loads the FlyWire proofread connections
(`data/flywire/proofread_connections_783.feather`, 16.8 M rows), maps neurons
to a stable index, assigns connection signs, and extracts a subcircuit by cell
class. Output neurons are four MBONs. The default configuration selects the
olfactory pathway:

```
ALPN (olfactory input) → Kenyon cell (mushroom body) → MBON (output)
                                  ↑
                                  │
                            DAN (reinforcement)
```

Measured connectivity used (edges / synapses) is ALPN→KC 28,144 / 329,394;
KC→MBON 89,315 / 256,719; DAN→KC 49,616 / 60,657. The extracted subcircuit
contains **1,065 neurons, 23,075 edges and 120,424 synapses with zero
artificial edges** (673 ALPN inputs, 384 Kenyon cells, 4 DAN, 4 MBON). Because
a global weight scale is not identifiable a priori, the network is calibrated
once per run to a spontaneous regime; the accepted scale was 4.0 (noise weight
4.0, 6 Hz background).

### 3.2 Stimulus encoding

Two encodings were implemented behind one interface, `stimulus(word, config)`,
selected by `encoding.scheme_id`.

- **grid-v1 (visual).** Words are rendered in a bundled font and mapped to
  photoreceptors. This encoding requires a `lobula → Kenyon cell` bridge and a
  dense `mushroom body → output` readout, both synthetic, because the visual
  chain does not reach the mushroom body (Section 4.1).
- **odor-v1 (olfactory, default).** Each category owns a fixed, seeded,
  disjoint subset of the input stage; each word drives 64 inputs from its own
  category prototype plus 4 cross-category inputs at graded drive in
  [0.35, 1.0], with the rest silent. Category overlap is deliberate: similar
  odours share receptors. The mapping uses the selected input-stage size (673
  of the 685-cap inputs).

### 3.3 Network and plasticity

The network is a population of LIF neurons wired by the extracted edges.
Plasticity is restricted to the Kenyon-cell → MBON synapses. Each synapse
carries a decaying eligibility trace updated by pre/post activity; the weight
update is the trace multiplied by a dopamine signal and a learning rate, then
clipped. Dopamine is delivered per class: on each trial the four MBONs receive
credit according to whether their class is correct, so the rule is a per-class
reward. A `dopamine off` switch disables the multiplicative factor while
leaving the trace dynamics intact.

Structural plasticity adds a reserve pool of silent, unconnected neurons
(1000; 700 available, 300 activated). At fixed intervals, reserve neurons are
recruited near the most successful readout unit, inherit its category and at
most (1 − divergence) of its inputs, and draw the remainder pseudo-randomly;
deletions retire the weakest mature reserve synapses so they track a configured
fraction of additions. The mechanism is exploratory — it has no published
precedent — and is switched on/off as an ablation.

### 3.4 Readout and evaluation

The reported readout is a supervised linear decoder fitted on a fixed
post-training feature pass (a specified, documented departure from pure online
readout: it isolates *representation* quality from *decoding*). Warnings are
emitted if shuffled-label control accuracy exceeds a leakage margin.

**Representation probes.** To separate "the network cannot learn" from "the
evaluation cannot detect learning", two linear probes are fitted on a *fresh
untrained* network and scored on held-out words with the same decoder protocol:
one on the input stage (which the stimulus reaches directly) and one on the
Kenyon-cell stage. A probe reports training, k-fold cross-validation, and
held-out accuracy, the number of features, and the number of training
presentations, per seed and aggregated across seeds. A *positive-control gate*
passes only if the input probe decodes above 0.90 and the Kenyon-cell probe
above chance + 0.05; a failed gate states explicitly where the class signal is
lost.

**Supervised-pretrained readout.** A seventh condition (`trained_pretrained`)
initialises the plastic readout from a supervised fit on that seed's Kenyon-cell
training presentation vectors (`learning.pretrain`) and then runs the same
dopamine-gated training and evaluation. If the readout *can* carry the
Kenyon-cell signal, this condition should exceed the trained condition.

**Separable-task control.** A second configuration
(`configs/odor-separable.yaml`) sets the cross-category receptor overlap to zero
(`encoding.odor.active_elsewhere: 0`), making the classes separable by a trivial
linear readout of the stimulus. If the null were caused by task difficulty, this
task should learn.

Protocol: seeded 15/5 per-category train/test split; 600 training trials per
seed; the trained condition plus controls (untrained, shuffled training labels,
dopamine off, structural plasticity on/off, and supervised pretraining).
Significance is a one-sample *t*-test and a Wilcoxon signed-rank test against
chance 0.25 at α = 0.01, with 95 % percentile-bootstrap CIs over seeds. The
formal runs use 10 seeds (1000–1009) with `eval_repeats = 3`.

### 3.5 Reproducibility

Seeds are independent, so the formal run executes one `evaluate` process per
seed (`flyread sweep`), skips seeds whose `results.json` already exists
(crash-resume), and merges the shards into a single report. The formal run
took ≈ 13.8 h wall-clock on 12 cores versus ≈ 126 h of single-core compute, at
a peak RSS of 224 MB per process. Kernels were compiled with Cython (verified
by the compiled-extension cache). Full commands are in the repository README;
all archived runs carry their manifest.

## 4. Results

### 4.1 The visual route does not exist in the connectome

The original design assumed
`photoreceptor → lamina → medulla → lobula → Kenyon cell → MBON`. Measuring
synapse counts over *all* candidate neurons in release 783 shows the top of
the chain is effectively absent (Table 1).

**Table 1.** Optic-lobe → mushroom-body connectivity in release 783.

| from → to | synapses (excitatory) |
|---|---|
| photoreceptor → lamina | 192,878 (180,523) |
| lamina → medulla | 239,863 (205,959) |
| medulla → lobula | 1,117 (474) |
| **lobula → Kenyon cell** | **1 (0 excitatory)** |
| medulla → Kenyon cell | 2 (2) |
| Kenyon cell → Kenyon cell | 379,234 (379,234) |
| Kenyon cell → MBON | 247,053 (247,053) |

Kenyon cells receive input essentially only from other Kenyon cells, from
`MBIN`, and from `DAN`. This is a property of the biology, not of annotation
filtering: the mushroom body is not a visual target in *Drosophila*. The
visual task therefore required a synthetic bridge and readout; through the real
pipeline the trained readout scored 0.233 (2 seeds), at or below chance and at
or below the untrained control (0.258). Three probe sweeps — readout drive,
stimulus contrast, and bridge density — kept the untrained readout within
single-seed noise of chance even when the output fired on 100 % of
presentations, indicating the word-class signal was lost upstream of the
readout. We therefore pivoted to the olfactory route, which the connectome
carries natively.

### 4.2 The olfactory route is null at 10 seeds

The formal 10-seed run (`runs/odor-v1-merged`) is summarised in Table 2. The
main condition is the trained network; all others are controls.

**Table 2.** Held-out accuracy by condition, 10 seeds, chance 0.25.

| condition | mean acc | 95 % CI | *p* vs chance | no-response | train acc |
|---|---|---|---|---|---|
| untrained network | 0.227 | [0.195, 0.262] | 0.8850 | 0.013 | — |
| **trained (main)** | **0.262** | **[0.237, 0.287]** | **0.2069** | **0.257** | 0.219 |
| shuffled training labels | 0.258 | [0.233, 0.282] | 0.2683 | 0.002 | 0.247 |
| dopamine off | 0.245 | [0.205, 0.285] | 0.5887 | 0.000 | 0.252 |
| structural plasticity on | 0.252 | [0.222, 0.283] | 0.4610 | 0.008 | 0.242 |
| structural plasticity off | 0.253 | [0.227, 0.288] | 0.4228 | 0.260 | 0.224 |

No condition is significantly above chance at α = 0.01. The trained mean lies
1.2 percentage points above chance, its CI includes values as low as 0.237, and
its own untrained control (0.227) is *below* it. Critically, the trained output
stage is not silent (no-response 0.257), so the failure is not a dead readout.
The trained confusion matrix is nearly uniform, with a mild surplus in one
column (Table 3).

**Table 3.** Trained confusion matrix, summed over 10 seeds (160 test
presentations per class).

```
true\pred |    0    1    2    3
-------------------------------
   animal |   38   44   35   33
     food |   40   48   25   37
     tool |   41   43   33   33
    place |   46   39   27   38
```

Per-category recall is animal 0.253, food 0.320, tool 0.220, place 0.253 — all
at or near the 0.25 chance level.

**Per-seed spread.** Table 4 shows the per-seed held-out accuracy. No seed's
trained condition separates cleanly from its own controls.

**Table 4.** Per-seed held-out accuracy by condition.

| seed | dopamine_off | shuffled | structural_off | structural_on | trained | untrained |
|---|---|---|---|---|---|---|
| 1000 | 0.200 | 0.200 | 0.183 | 0.233 | 0.200 | 0.200 |
| 1001 | 0.217 | 0.300 | 0.233 | 0.283 | 0.250 | 0.200 |
| 1002 | 0.217 | 0.267 | 0.267 | 0.333 | 0.267 | 0.350 |
| 1003 | 0.350 | 0.283 | 0.217 | 0.233 | 0.317 | 0.250 |
| 1004 | 0.200 | 0.267 | 0.267 | 0.283 | 0.333 | 0.267 |
| 1005 | 0.300 | 0.250 | 0.233 | 0.167 | 0.217 | 0.217 |
| 1006 | 0.217 | 0.317 | 0.267 | 0.200 | 0.300 | 0.233 |
| 1007 | 0.283 | 0.200 | 0.233 | 0.217 | 0.233 | 0.233 |
| 1008 | 0.133 | 0.283 | 0.250 | 0.250 | 0.250 | 0.183 |
| 1009 | 0.333 | 0.217 | 0.383 | 0.317 | 0.250 | 0.133 |

### 4.3 Structural growth runs but does not help

Structural plasticity executed as designed: 300 reserve neurons were activated
and ≈13.2–13.9 k synapses created (150 retired) per seed, bringing the network
from 1,065 to 1,365 neurons and from 120 k to ≈136 k synapses. Across seeds the
counts vary only slightly (net new synapses 13,063–13,799). Despite this, the
structural-on condition (0.252) is not distinguishable from structural-off
(0.253) or from the trained condition (0.262), and none exceeds chance. Growth
changes the network but not the class information available to the readout.

### 4.4 Positive controls: the Kenyon-cell code does not carry the label

Two additional 10-seed runs (`runs/pctrl-v1-merged` on the default task,
`runs/pctrl-sep-merged` on the separable task) add the representation probes,
the supervised-pretrained condition, and the separable-task control.

**Probes.** The input stage decodes the category perfectly — held-out 1.000 on
every seed in both tasks — so the stimulus genuinely reaches the network with
its class structure intact, and the probe instrument works (Table 5). The
Kenyon-cell population, by contrast, decodes at chance in both tasks (0.255 and
0.227), despite a training accuracy of 1.000: the decoder memorises the training
presentations but has no class structure to generalise. The k-fold
cross-validation accuracy (0.236 / 0.228) confirms the estimate is not an
artefact of the held-out split. The positive-control gate therefore **fails** in
both runs with the same diagnosis: *the label signal is lost before the plastic
readout*.

**Table 5.** Representation probes, 10 seeds, chance 0.25, held-out accuracy on
unseen words.

| task | stage | features | train | CV | held-out | 95 % CI |
|---|---|---|---|---|---|---|
| default | input (olfactory) | 673 | 1.000 | 1.000 | **1.000** | [1.000, 1.000] |
| default | Kenyon cell | 384 | 1.000 | 0.236 | **0.255** | [0.227, 0.287] |
| separable | input (olfactory) | 673 | 1.000 | 1.000 | **1.000** | [1.000, 1.000] |
| separable | Kenyon cell | 384 | 1.000 | 0.228 | **0.227** | [0.195, 0.262] |

**Supervised pretraining does not help.** The `trained_pretrained` condition
fits the readout to a training accuracy of 1.000 on every seed, yet its
held-out accuracy is 0.268 (default task) and 0.235 (separable task) — equal to
or below the plain trained condition (0.262 / 0.248) and at chance (Table 6).
The bottleneck is not the readout's capacity or initialisation.

**Table 6.** Held-out accuracy by condition for the two positive-control runs
(10 seeds, chance 0.25). *p* is a one-sided test against chance; none reach
significance at α = 0.01.

| run | condition | mean acc | 95 % CI | *p* |
|---|---|---|---|---|
| default | untrained | 0.227 | [0.195, 0.262] | 0.8850 |
| default | **trained** | **0.262** | [0.237, 0.287] | 0.2069 |
| default | shuffled labels | 0.258 | [0.233, 0.282] | 0.2683 |
| default | dopamine off | 0.245 | [0.205, 0.285] | 0.5887 |
| default | trained + pretrain | 0.268 | [0.223, 0.312] | 0.2301 |
| default | structural on | 0.235 | [0.195, 0.277] | 0.7450 |
| default | structural off | 0.248 | [0.212, 0.282] | 0.5343 |
| separable | untrained | 0.235 | [0.203, 0.268] | 0.7940 |
| separable | **trained** | **0.248** | [0.207, 0.285] | 0.5308 |
| separable | trained + pretrain | 0.235 | [0.207, 0.268] | 0.8058 |
| separable | dopamine off | 0.247 | [0.215, 0.287] | 0.5659 |

**Not task difficulty.** On the separable task the stimulus is decodable by a
trivial linear rule (probe 1.000), the untrained and trained conditions sit at
chance, and the gate fails for the same reason as the default task. The null is
therefore not caused by overlapping categories.

Across both tasks the probes show the encoding, the instrument, and the readout
as fitted are sound; the only point at which class information is lost is the
transform from the input (where the probe scores 1.000) to the Kenyon-cell
population (where it scores at chance).

## 5. Discussion

### 5.1 Where the signal is lost

Three observations localise the failure, and the positive controls add a
fourth, decisive one.

1. **Not the output drive.** In the trained condition the MBON stage is not
   silent (no-response 0.257; 0.000 in the dopamine-off control), so the
   readout is being driven. The visual-task probe sweeps further showed that
   forcing the output to fire on 100 % of presentations does not raise accuracy.
2. **Not the input encoding.** The odor encoding has genuine class structure
   (category-conditioned prototypes with partial overlap), and it reaches the
   output stage; the visual encoding's failure was traced upstream of the
   readout, and the olfactory route removes the synthetic bridge entirely.
3. **Not the plastic readout or its credit rule.** The input-stage probe
   reaches 1.000 held-out, so the decoder instrument and the fitted readout can
   in principle express the label. A supervised-pretrained readout that fits
   the Kenyon-cell features to 1.000 still generalises at chance, and the
   separable-task control — decodable by a trivial linear rule — is equally
   null. The readout is not the first place the signal dies.
4. **The Kenyon-cell representation.** On both tasks the Kenyon-cell probe
   scores at chance (held-out 0.255 / 0.227, CV ≈ 0.23) while the input probe
   scores 1.000. The `ALPN → Kenyon cell` transform is the point at which
   class information is lost; everything downstream — decoder, pretraining,
   dopamine rule — has no class code to amplify.

Taken together, these localise the failure to the `ALPN → Kenyon cell`
projection under this calibrated regime: the stimulus encodes the label (input
probe 1.000), the readout is a valid instrument (it memorises and could decode),
but the Kenyon-cell population does not linearly carry the category. The
three-factor rule and credit assignment are exonerated as the *first* cause;
they inherit a code with nothing to learn.

### 5.2 A wiring caveat

The real `DAN → Kenyon cell` edges are extracted (372 edges) but are **not
simulated**: `build_network` wires consecutive stage pairs, within-stage
recurrence, and the plastic `mushroom body → output` pair. Because
`reinforcement` is configured after `mushroom_body`, `reinforcement →
mushroom_body` is a backward pair and is dropped; the reward is applied as a
global scalar gated by the real DANs' spike counts rather than flowing through
the real synapses. This does not change the learning rule exercised, but it
means the config's "real DAN → Kenyon cell" pathway is not the one trained.
Reordering the stages so reinforcement precedes the mushroom body would
exercise those edges; that is a separate experiment.

### 5.3 Limitations

- The task and network are small by design (CPU-only reproducibility), so the
  null speaks to this calibrated regime and this rule, not to mushroom-body
  learning in general.
- The reported readout is a supervised linear decoder fitted on a fixed
  post-training pass — a documented deviation that isolates representation
  from decoding; an online MBON-argmax readout is also produced and is no
  better.
- The structural-plasticity recruitment rule is exploratory and has no
  published precedent.
- All treatments share a single calibration; a broader sweep of operating
  points was not performed for the olfactory task.

### 5.4 What would change the answer

The probe evidence moves the target from the readout to the representation.
Candidates that could change the answer include:

- **Improving the `ALPN → Kenyon cell` code.** The probes show the class
  signal is lost at this transform, so a biologically motivated recoding —
  denser or sparser Kenyon-cell representations, different input scaling,
  non-linear decoding of the KC population — is the direct next experiment.
- **A reward-prediction-error baseline for the per-class credit rule.** The
  mean dopamine signal is dominated by the ≈75 % wrong-at-chance surplus;
  while the current results show the readout inherits a code with no class
  signal, an RPE rule would still be the principled way to learn if a code were
  present.
- **Simulating the real `DAN → mushroom body` edges** by stage reordering, so
  the teacher flows through real synapses rather than a global scalar.

Each of these targets a different stage than the readout; adding seeds will
not, since the formal 10-seed result already excludes under-powering.

## 6. Conclusion

In a real, synapse-level FlyWire subcircuit with zero artificial edges, a
dopamine-gated mushroom-body readout did not learn to categorise four-letter
words: across 10 seeds it reached 0.262 (95 % CI [0.237, 0.287]) against chance
0.25 (*p* = 0.21), with every control also at chance. A prior visual
formulation failed for a stronger reason — the optic-lobe → mushroom-body
pathway does not exist in the connectome — which motivated the olfactory pivot.
Positive controls turned the residual null into a precise statement: the
category is decodable at the stimulus (input probe 1.000), a supervised decoder
memorises the Kenyon-cell presentations perfectly, and yet the Kenyon-cell
population carries no generalisable class code (probe 0.255) and supervised
pretraining can not recover one — on either an overlapping task or a trivially
separable one. The plasticity rule and the readout are exonerated as the first
cause: the *`ALPN → Kenyon cell` transform* is where the label is lost. The
negative result is reproducible end to end on a laptop, and the archived runs
and manifests are released with the code. We report it without reframing: the
connectome provides a beautifully structured circuit, but under this calibrated
regime the Kenyon-cell population does not turn its input into a code a linear
readout, a supervised decoder, or this plasticity rule can learn from.

## Data and code availability

Code: `ngiaved/fly-word-categorization` (MIT-style project layout; FlyWire data
is CC-BY-NC and downloaded by `flyread fetch-data`). The formal run is archived
under `runs/odor-v1-merged/` (report, results JSON, manifest, figures), and the
positive-control runs under `runs/pctrl-v1-merged/` and
`runs/pctrl-sep-merged/`. The visual 2-seed run is `runs/run-20261008T204756Z/`;
the reduced olfactory diagnostic is `runs/run-20261009T095645Z/`.

## References

*The following are the key background works; bibliographic details should be
verified against the primary sources before submission.*

1. Aso Y, Hattori D, Yu Y, et al. (2014). The neuronal architecture of the
   mushroom body provides a logic for associative learning. *eLife* 3:e04577.
2. Dorkenwald S, Matsliah A, Sterling AR, et al. (2024). Neuronal wiring
   diagram of an adult brain. *Nature* 634:124–138.
3. Hebb DO (1949). *The Organization of Behavior*. Wiley.
4. Hige T, Aso Y, Rubin GM, Turner GC (2015). Plasticity-driven
   individualization of olfactory coding in mushroom body output neurons.
   *Nature* 526(7572):258–262.
5. Izhikevich EM (2003). Simple model of spiking neurons. *IEEE Transactions
   on Neural Networks* 14(6):1569–1572.
6. Scheffer LK, Xu CS, Januszewski M, et al. (2020). A connectome and analysis
   of the adult *Drosophila* central brain. *eLife* 9:e57443.
7. Schlegel P, Yin Y, Bates AS, et al. (2024). Whole-brain annotation and
   multi-connectome cell typing of *Drosophila*. *Nature* 634:139–152.
8. Schultz W, Dayan P, Montague PR (1997). A neural substrate of prediction
   and reward. *Science* 275:1593–1599.
9. Stimberg M, Brette R, Goodman DFM (2019). Brian 2, an intuitive and
   efficient neural simulator. *eLife* 8:e47314.
10. Zheng Z, Lauritzen JS, Perlman E, et al. (2018). A complete electron
    microscopy volume of the brain of adult *Drosophila melanogaster*. *Cell*
    174(3):730–743.

## Appendix A. Reproducing the formal run

```bash
python -m pip install -r requirements.lock.txt
python -m pip install -e .
python -m flyread fetch-data
python -m flyread sweep        # default: 10 seeds in parallel, then merge into runs/odor-v1-merged
python -m flyread --config configs/default.yaml --run-id pctrl-v1 sweep   # + probes, pretrained, gate
python -m flyread --config configs/odor-separable.yaml --run-id pctrl-sep sweep  # separable-task control
```

The `sweep` command runs one `evaluate` process per seed, resumes completed
shards, and merges them into a single multi-seed report. Every run writes a
manifest (config, seed state, checksums, calibration, subcircuit statistics,
codegen target, hardware, git commit).
