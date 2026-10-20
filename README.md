# FlyWord

Dopamine-gated plasticity in a subcircuit of the *Drosophila* FlyWire connectome,
driven by 4-letter word stimuli.

The current default (`odor-v1`) treats each word as an arbitrary **odor
identity** and drives the pathway the connectome actually carries: real
antennal-lobe projection neurons (`ALPN`) → Kenyon cells (`mushroom_body`) →
`MBON` output, with `DAN` dopamine neurons as the teaching signal. Every
simulated edge is real FlyWire release-783 connectivity; no connection is
invented.

The original `grid-v1` task (render the word to a pixel grid → photoreceptors →
optic lobe → mushroom body) is retained unchanged and is what
`configs/smoke.yaml` exercises. It required two declared-synthetic edges,
because this stage definition contains **no direct optic-lobe → Kenyon-cell
pathway**. That is narrower than saying the mushroom body is not a visual
target: visual projection neurons do innervate the mushroom-body calyx
(Vogt et al., 2016; Li et al., 2020), but they are not part of the optic-lobe
chain defined here.

Either way four output neurons are read out as `animal / food / tool / place`.

---

## Current state (19 October 2026)

Plain-language summary; full detail in [docs/current-state.md](docs/current-state.md).

- The real olfactory subcircuit **does not learn the task at its physiological
  operating point** — a confirmed null localised to the `ALPN → Kenyon cell`
  transform (positive controls: input probe 1.000, Kenyon-cell probe CV 0.236 /
  held-out 0.255).
- Raising that transform's gain (a diagnostic "recode", ceiling 100 Hz) restores
  a decodable Kenyon-cell class code (probe CV 0.950 / 1.000), **but it does not
  restore the online learner**. The recode is **necessary, not sufficient**.
- The **online MBON-argmax readout stays at chance (0.235–0.272) in every
  condition measured** (nine conditions, ten seeds), so the four-dimensional
  MBON readout and its credit rule are a second bottleneck.
- The **post-hoc supervised decoder** rises with gain (0.363 at gain 20; 0.418 at
  gain 50), but the plain trained condition is **not significantly above the
  untrained floor** after Holm correction (+0.025, *p* = 1.0; +0.088,
  *p* = 0.117).
- **Structural growth helps decoding but is not special.** The growth-matched
  controls (`runs/recode-50-controls-merged`, 10 seeds) show the
  growth gain (`structural_on` 0.750 vs `trained` 0.418) **survives the removal
  of all label guidance** (`structural_unguided` 0.785, Holm *p* = 1.0) and of
  category inheritance (`structural_random_category` 0.707, Holm *p* = 1.0), and
  is **exceeded by 300 random decoder features with no growth** (0.983, Holm
  *p* = 0.018). The effect is therefore a **decoder-capacity /
  representation-access effect**, not a learned, label-guided prototype.
- Dopamine has **no detectable effect** on decoded accuracy (`dopamine_off` 0.828
  vs `structural_on` 0.750, Holm *p* = 0.34). That is a null result, not proof
  that plasticity is absent: the decoder is refit and can undo weight changes.
- **Revision caveat.** These archived numbers predate the per-condition
  RNG-isolation fix. Under the fix, `trained` and `structural_off` (identical
  configs) are bit-identical. Absolute numbers are provisional; conclusions rest
  on internally paired contrasts and are stated in
  [docs/paper.md](docs/paper.md).

## Status: read this before trusting any number

This repository contains a working implementation and an honest account of what
does and does not work. **The authoritative results are the recoded runs
(`runs/recode-v2-merged`, gain 20; `runs/recode-50-v2-merged`, gain 50), the
growth-matched control run (`runs/recode-50-controls-merged`, gain 50), and the
manuscript in [docs/paper.md](docs/paper.md).** At the physiological operating
point the odor task is a confirmed null. Raising the `ALPN → Kenyon cell` gain
restores a decodable Kenyon-cell class code (probe CV 0.950 at gain 20, 1.000 at
gain 50), **but the online MBON-argmax readout stays at chance in every
condition measured (0.235–0.272)**. The recode is therefore **necessary, not
sufficient**: the four-dimensional MBON readout and its credit rule remain a
second bottleneck. Under a supervised post-hoc decoder the trained condition is
not significantly above the untrained floor after Holm correction (gain 20
+0.025, *p* = 1.0; gain 50 +0.088, *p* = 0.117). Structural growth raises decoder
accuracy further (up to 0.833 on the main recode run, 0.750 on the matched-control
run), but the controls show that this is a decoder-capacity effect: it does not
require label guidance and is beaten by plain random features (0.983). The recode
is a diagnostic intervention — it runs at a 100 Hz calibration ceiling, far
outside the physiological 0.5–15 Hz band — not a claim about the circuit *in
vivo*. The gain-1 failure is also confounded with signal-to-noise ratio; the
diagnostics needed to separate them are implemented but not yet present in the
archived runs. Older run directories and any README text predating the recode are
superseded; where the README and an archived run disagree, the archived run plus
its manifest is authoritative.

| Area | State |
|---|---|
| Data download, checksum verification, schema validation | Working |
| Subcircuit extraction from real FlyWire connectivity | Working (~13 s, 16.8 M rows) |
| Per-connection neurotransmitter signs | Working |
| Deterministic word rendering + encoding (`grid-v1`) | Working |
| Deterministic sparse odor encoding (`odor-v1`) | Working |
| Brian2 network construction (visual and olfactory) | Working |
| Calibration to a spontaneous operating regime | Working (accepted in-band scale 4–6 for the default; recode widens the ceiling to 100 Hz) |
| CPU feasibility gate (Task 0.3/0.4) | **Not yet passed** |
| Learning / evaluation / report | Physiological baseline is a null; the recode restores the Kenyon-cell code but **not the online readout**, and the supervised decoder's trained condition is not significantly above the untrained floor after Holm correction (see below) |

The current blocking item is still the **dopamine-gated `mushroom body → output`
readout**. Work so far:

1. **`grid-v1` (visual, 2 seeds):** null. The mushroom body receives essentially
   no optic-lobe input in release 783, so the required chain was bridged
   artificially; the word-class signal was already lost before the readout.
2. **`odor-v1` (olfactory, 10-seed formal run):** null at the calibrated
   operating point, but with **zero synthetic edges** — every simulated edge is
   real `ALPN → Kenyon cell → MBON` connectivity. Trained readout **0.262**
   (95 % CI [0.237, 0.287]), not above chance 0.25 (p = 0.21); untrained 0.227.
3. **Positive controls (10 seeds, two tasks):** instrument validated and the
   null localised. A supervised linear probe decodes the category **perfectly
   from the stimulus** (input probe 1.000) but **at chance from the Kenyon-cell
   population** (CV 0.236 / held-out 0.255; separable-task control CV 0.228).
   **The bottleneck in this calibrated regime is the `ALPN → Kenyon cell`
   transform.**
4. **Recode (10 seeds each):** raising that transform's gain to 20 or 50 and
   widening the calibration ceiling to 100 Hz restores the Kenyon-cell code
   (probe CV 0.950–1.000) and raises the supervised-decoder accuracy (0.363 at
   gain 20, 0.418 at gain 50; growth-on conditions 0.608 / 0.833). The online
   MBON-argmax readout stays at chance (0.235–0.272) in every condition measured,
   so the recode is **necessary but not sufficient**: the reported numbers are
   representation quality under a post-hoc supervised decoder, and the readout /
   credit rule is a second unresolved bottleneck; see [Known issues](#known-issues).
5. **Growth-matched controls (10 seeds, `runs/recode-50-controls-merged`):**
   run and reported (Section 4.5 of the manuscript). Structural growth still
   beats no-growth (`structural_on` 0.750 vs `trained` 0.418, +0.37, Holm
   *p* = 0.018), but the gain **does not depend on label guidance**
   (`structural_unguided` 0.785, Holm *p* = 1.0) **or category inheritance**
   (`structural_random_category` 0.707, Holm *p* = 1.0), and a **no-growth
   decoder given 300 random features reaches 0.983**, above every growth
   condition. So the growth effect is a decoder-capacity / representation-access
   effect, not a learned prototype; dopamine has no detectable effect (0.828 vs
   0.750, Holm *p* = 0.34). This demotes the earlier "growth does the learning"
   headline; see [docs/current-state.md](docs/current-state.md).

The negative results are documented in full in
[docs/final-report.md](docs/final-report.md); a
manuscript draft is in [docs/paper.md](docs/paper.md). See
[Known issues](#known-issues).

---

## The main scientific finding

The original design assumed this signal chain:

```
photoreceptor -> lamina -> medulla -> lobula -> Kenyon cell -> MBON output
```

**That chain does not exist in the FlyWire connectome.** Measured synapse
counts between the specified stages, over *all* candidate neurons in release 783:

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
essentially no optic-lobe input to the mushroom body **through the stages defined
here**.

This is real biology, not a bug in the annotation filters: **there is no direct
optic-lobe → Kenyon-cell pathway in this stage definition.** It is narrower than
saying the mushroom body is not a visual target. Visual projection neurons do
innervate the mushroom-body calyx (Vogt et al., 2016; Li et al., 2020), but they
are not part of the photoreceptor → lamina → medulla → lobula chain modelled
here. So the required pathway cannot be built from real connectivity without
inventing a connection, and inventing one silently would make the whole
experiment meaningless.

### The route the connectome does carry

The same release shows the olfactory pathway is dense and feed-forward:

| from → to | edges / synapses |
|---|---|
| `ALPN → Kenyon_Cell` | 28,144 / 329,394 |
| `Kenyon_Cell → MBON` | 89,315 / 256,719 |
| `DAN → Kenyon_Cell` | 49,616 / 60,657 |

`odor-v1` (the default) drives exactly this chain, so no connection has to be
invented. Words are encoded as deterministic sparse patterns on the `ALPN`
input stage, with a shared per-category receptor prototype so the class
structure is present in the stimulus itself (a property the rendered-word ink
signal did not have).

### What was decided for the visual task

Both options were reviewed explicitly before any code was written:

1. **Real optic lobe plus one clearly-labelled artificial bridge.**
   Everything inside the subcircuit is real FlyWire wiring with real
   per-connection neurotransmitter signs, *except* a single synthetic
   `lobula -> Kenyon cell` synapse set. Bridge source neurons are chosen by
   their **real** incoming strength from the selected upstream stages, so the
   bridge inherits real structure; only the synapse counts and signs are
   invented. Every such edge is flagged `artificial`, counted separately, and
   printed in the manifest and the report. It is never described as FlyWire
   connectivity.

2. **Real reinforcement neurons as the teachers.** The default olfactory task
   (`odor-v1`) selects the real `DAN` (dopaminergic) class — 4 neurons and
   49,616 real `DAN → Kenyon cell` edges. The visual `grid-v1` task instead uses
   the `MBIN` class, which in release 783 holds 2× APL and 2× DPM neurons; these
   are **not** a canonical dopaminergic cluster (APL is GABAergic, DPM is
   serotonergic), so `grid-v1` should not be read as a real DAN circuit. In both
   tasks the reward **value** is synthetic, but it is injected into these real
   neurons and their resulting spike count **gates** the weight update, so
   plasticity only happens if genuine reinforcement neurons respond.

   Caveat: the real `DAN → Kenyon cell` edges are extracted but **not simulated**
   — the odor network wires stage pairs in signal order, so those backward edges
   are dropped and the reward is applied as a global scalar gated by the DANs'
   spike counts (Known issue 9). Reordering the stages would exercise them.

---

## Install

Requires Python 3.11.

```bash
python3.11 -m venv .venv
.venv/bin/pip install -e .
```

Brian2 2.9 requires NumPy < 2, which is why `requirements.lock.txt` pins
`numpy==1.26.4`. Exact versions are recorded there.

**Optional, strongly recommended:** a working C++ toolchain. Without one Brian2
silently falls back to its much slower NumPy code-generation target. On
macOS with a broken clang you can force this explicitly:

```bash
flyread --set simulation.codegen_target=numpy benchmark
```

or set `CC`/`CXX` to a working compiler.

## Data

FlyWire data is **CC-BY-NC 4.0** and is therefore *not* committed here.
`data/flywire/` is git-ignored. Fetch it with:

```bash
flyread fetch-data
```

| File | Source | Size | MD5 |
|---|---|---|---|
| `Supplemental_file1_neuron_annotations.tsv` | `flyconnectome/flywire_annotations` | 31,720,298 B | `fd4fcb205256ca2f80a2eca3a0cc5d87` |
| `proofread_connections_783.feather` | Zenodo record `10676866` | 852,022,274 B | `f48f972d262323a102aed49af1396b8a` |

Both checksums are verified before anything is built, and recorded in the run
manifest.

### Real connectivity schema (verified, not assumed)

The first draft of this project guessed the connectivity column names. They
were **wrong**. The real file has 16,847,997 rows in 258 record batches:

```
pre_pt_root_id  post_pt_root_id  neuropil  syn_count
gaba_avg  ach_avg  glut_avg  oct_avg  ser_avg  da_avg
```

The `*_avg` columns are per-connection predicted transmitter scores, so the
sign of each connection is its dominant transmitter mapped through
`connectome.neurotransmitter_signs` — genuinely per synapse, not inherited
from the presynaptic neuron. Observed dominant transmitters across a 3.9 M-row
sample: acetylcholine 54 %, GABA 21 %, glutamate 19 %, dopamine 2.8 %,
serotonin 1.5 %, octopamine 1.1 %; 0.01 % of rows have no scores.

## Usage

```bash
flyread fetch-data                        # download + verify (once)
flyread inspect --subcircuit              # real schemas + extracted subcircuit
flyread calibrate                         # weight-scale search
flyread benchmark                         # Task 0 feasibility gate
flyread evaluate                          # all conditions x seeds + report
flyread run                               # gate, then evaluate
flyread sweep                             # formal: 1 seed/process across cores, then merge
flyread merge                             # merge existing per-seed shards into a report
flyread --config configs/smoke.yaml selftest   # fast end-to-end smoke check
```

Useful flags: `--set key.path=value` (repeatable), `--seed N`,
`--run-id NAME` (stable output dir; `evaluate` resumes if `results.json`
exists), `--log-level DEBUG`.

`sweep` runs each seed in its own `evaluate` process (default workers
`cpu-2`), so a seed that finishes is kept and re-running the same command
resumes the rest; it then merges the shards and writes `<run-id>-merged/`.

`configs/default.yaml` is the full experiment. `configs/smoke.yaml` is the same
code path with the subcircuit and trial budget shrunk for fast checks; it is
**not** a source of reportable numbers.

## What the subcircuit looks like

`odor-v1` (default), `subcircuit.candidate: medium`, release 783:

| | |
|---|---|
| Neurons | 1,065 |
| Edges | 23,075 (**all real**, 0 artificial) |
| Synapses | 120,424 (all real) |
| Per stage | olfactory 673, mushroom_body 384, reinforcement 4, output 4 |
| Extraction time | ~13 s, single streaming pass, bounded memory |

Because the pathway is real end to end, `real_edges_only.signal_reaches_outputs`
is `true` without any bridge.

`grid-v1` (visual), for comparison, adds a synthetic `lobula → Kenyon cell`
bridge and a dense synthetic `mushroom body → output` readout:

| | |
|---|---|
| Neurons | 1,928 |
| Edges | 21,454 (19,150 real + 2,304 artificial) |
| Synapses | 147,466 (145,162 real + 2,304 artificial) |
| Per stage | photoreceptor 576, lamina 384, medulla 384, lobula 192, reinforcement 4, mushroom_body 384, output 4 |

For the visual chain reachability is reported twice: `with_bridge` is `true`
(4/4 outputs) but `real_edges_only` is `false` — **no visual signal reaches the
mushroom body on real connectivity alone**.

## Architecture

| Module | Responsibility |
|---|---|
| `flyread.config` | YAML loading, validation, dotted overrides |
| `flyread.repro` | checksums, seeding, run manifests, environment capture |
| `flyread.connectome` | schema validation, annotation rules, streaming connectivity, subcircuit selection, artificial bridge |
| `flyread.encoding` | deterministic rendering + `grid-v1` mapping, sparse `odor-v1` mapping, rate→current |
| `flyread.network` | Brian2 stages, signed synapses, eligibility traces, noise, teacher drive, calibration |
| `flyread.learning` | dopamine-gated updates, readout, trial runner |
| `flyread.structural` | pruning, silencing, reserve recruitment, event log |
| `flyread.evaluate` | splits, conditions, confusion, bootstrap CI, significance |
| `flyread.benchmark` | CPU throughput/memory gate |
| `flyread.report` | JSON/Markdown report and plots |
| `flyread.cli` | command line entry points |

### Model

LIF neurons, `tau = 20 ms`, threshold 1.0, refractory 2 ms. Synaptic current
decays with `tau_syn = 5 ms`. Each neuron gets one Poisson background-noise
source. Eligibility is a declared synaptic state variable `elig`, gated by real
reinforcement-neuron activity. Plastic weights are stored as a **nonnegative
magnitude** with the sign held separately, so clipping to `[0, 1]` can never
flip an inhibitory synapse to excitatory.

## Reproducibility

Every run writes a manifest under `runs/<run_id>/` recording the full resolved
config, seed, seed state for Python/NumPy/Brian2, data checksums, annotation
rules, subcircuit statistics, artificial-edge counts, calibration attempts,
throughput, hardware, and package versions. Determinism is asserted for
rendering, dataset splits, and trial order.

## Archived results and citation

`runs/` is gitignored and the archived run directories are **not** in the
repository. They are being prepared for deposit on Zenodo; the DOI will be added
to `CITATION.cff` and the manuscript when assigned. Each run keeps its manifest
(config, seed state, checksums, calibration, subcircuit statistics, codegen
target, hardware, git commit), and every reported statistic can be recomputed
from the run's `per_seed_test_accuracy` block with the functions in
`flyread.evaluate` (Wilcoxon + Holm, sign-flip leakage test, paired contrasts).

## Known issues

1. **Calibration converges to an in-band scale (4–6), but the paradigm is
   narrow.** With a deterministic resting LIF network every scale and every
   seed lands at either silence or runaway; only a handful of scales sit in the
   0.5–15 Hz band. The accepted scale is real (recorded in the manifest), but
   throughput is dominated by the per-trial overhead of the growth wiring, so a
   full ≥10-seed run is expensive; `flyread sweep` runs one seed per core in
   parallel (the formal 10-seed run took ≈ 13.8 h wall-clock on 12 cores vs
   ≈ 126 h of single-core compute).
2. **Brian2 falls back to NumPy codegen** when Cython compilation fails, which
   changes throughput by roughly 6–10×. On a machine with a partially broken
   Command Line Tools install the failure is a missing libc++ header
   (`fatal error: 'ios' file not found`) even though the headers exist under the
   SDK. Point clang at them for the run:
   `CPLUS_INCLUDE_PATH=/Library/Developer/CommandLineTools/SDKs/MacOSX.sdk/usr/include/c++/v1`.
   The manifest records the *configured* `codegen_target`
   (`simulation.codegen_target`, default `auto`), not the runtime resolution;
   to confirm Cython actually compiled, check for fresh `_cython_magic_*.so`
   files under `~/Library/Caches/cython/brian_extensions/` after a run.
3. **Unit tests exist and pass** for the scoring, split, confusion, and
   significance code (`tests/test_dopamine_learning.py`, `test_evaluation.py`,
   `test_signal_path.py`, `test_structural_plasticity.py`, plus
   encoding/connectome/reproducibility modules), alongside `flyread selftest`.
   `pytest tests/` is slow in NumPy codegen and timed out at 20 min rather than
   failing.
4. **medulla → lobula is nearly empty** (1,117 synapses over all candidates),
   so the lobula stage is weakly driven in the real data. Stage selection for
   the lobula may need rethinking.
5. The reward signal is synthetic. Only the neurons it reaches are real.
6. **The growth effect is a decoder-capacity effect, not a learned prototype.**
   At the calibrated operating point (`runs/run-20261008T204756Z`, 2 seeds, 100
   trials) untrained 0.258, trained 0.233, structural_on 0.283 against chance
   0.25 — growth had no advantage to beat, and the earlier 0.45–0.567 numbers
   from a harness operating point were withdrawn. Under the recode (gain 50)
   growth clearly beats no-growth (`structural_on` 0.750 vs `structural_off`
   0.378 / `trained` 0.418, +0.37, Holm *p* = 0.018, in
   `runs/recode-50-controls-merged`). The reviewer-matched controls, however,
   show the gain is **not** about the recruitment rule: removing all label
   guidance (`structural_unguided` 0.785, Holm *p* = 1.0) or category inheritance
   (`structural_random_category` 0.707, Holm *p* = 1.0) leaves it unchanged, and
   a **no-growth decoder given 300 random Kenyon-cell projections reaches 0.983**
   (Holm *p* = 0.018 vs `trained`), above every growth condition. So growth is
   best read as extra decoder access to an already decodable Kenyon-cell code,
   not as a structure that learns or a prototype that is constructed; switching
   dopamine off has no detectable effect on decoded accuracy (`dopamine_off`
   0.828 vs `structural_on` 0.750, Holm *p* = 0.34). Separately, the online
   MBON-argmax readout stays at chance (0.235–0.272) in every condition measured,
   so the headline rests on the post-hoc supervised decoder (representation
   quality), not on an online learner. **All archived numbers predate the
   per-condition RNG-isolation fix**: `structural_on` scores 0.750 here vs 0.833
   in `runs/recode-50-v2-merged`, and `trained` vs `structural_off` (identical
   configs) differ, so only internally-paired contrasts are interpreted and
   absolute values are provisional.
7. **Visual (`grid-v1`): the word signal is lost upstream of the readout.**
   Through the real pipeline, 42–65 % of test presentations get a zero-spike
   output reading (`no-response`) at the archived drive, so both the argmax
   readout and the linear decoder collapse toward the category prior. The two
   probe sweeps (temp probes, seed 1000, untrained condition, chance 0.25)
   narrow the cause: raising `artificial_readout.weight_ratio` to >= 20 makes
   the outputs fire on 100 % of presentations, and raising `encoding.max_hz`
   contrast up to 800 Hz neither lifts the readout above chance (0.217–0.333
   across 17 settings, all within single-seed noise of 0.25). The readout can
   only amplify what the Kenyon cells encode, and under the calibrated regime
   the mushroom body does not carry decodable word-class activity at its output
   stage.
8. **Odor (`odor-v1`): the readout does not learn — confirmed at 10 seeds.**
   The formal run (`runs/odor-v1-merged`, 10 seeds, 600 training trials each)
   removes the synthetic components and lands at trained 0.262 (95 % CI
   [0.237, 0.287]) vs untrained 0.227, chance 0.25; every condition is
   indistinguishable from chance (trained p = 0.21). The output stage is not
   silent (trained no-response 0.257), so this is not an under-driven-output
   problem. **The positive controls (see the deviation entries on probes,
   pretraining, and the separable task; runs `runs/pctrl-v1-merged` /
   `runs/pctrl-sep-merged`) localise a bottleneck:** the
   input probe scores 1.000 and the Kenyon-cell probe scores at chance
   (CV 0.236 / held-out 0.255; separable-task CV 0.228; train 1.000), the gate
   fails, and supervised pretraining still generalises at chance — so the class
   information is lost at the `ALPN → Kenyon cell` transform. **But restoring
   that transform is not sufficient:** under the recode the Kenyon-cell probe
   rises to 0.950–1.000 while the online MBON-argmax readout stays at chance, so
   the readout and its credit rule are a second, unresolved bottleneck. Probes
   added in revision now record evoked versus spontaneous Kenyon-cell rates and
   sparseness; they are not in the archived runs.
9. **The real `DAN → Kenyon cell` edges are extracted but not simulated.** The
   odor subcircuit contains 372 real `reinforcement → mushroom_body` edges, but
   `build_network` only simulates consecutive stage pairs, within-stage
   recurrence, and the plastic `mushroom_body → output` pair. Because
   `reinforcement` is configured after `mushroom_body`, those edges are
   backward and dropped; the network logs `no reinforcement stage feeds the
   mushroom body`. The reward is still gated by the real `DAN` spike counts,
   but applied as a global scalar rather than flowing through the real
   `DAN → KC` synapses. Reordering the stages to put `reinforcement` before
   `mushroom_body` would exercise them, but that is a separate experiment.

## Known deviations from the original OpenSpec proposal

- Added a `reinforcement` stage (`cell_class = MBIN`).
- Added `subcircuit.bridge` and an `artificial` flag on edges.
- Added `network.noise` and `network.background_dc_na`, because a deterministic
  resting LIF network is exactly silent and can never satisfy a nonzero lower
  rate bound.
- Added `network.lif.tau_syn_ms`; without a synaptic decay equation `I_syn`
  integrates without bound and pins every neuron hyperpolarized.
- Held-out evaluation now passes `learn=False`, so test labels can never leak
  into the weights.
- Significance testing is one-sided (`alternative="greater"`), so accuracy
  significantly *below* chance is no longer reported as above chance.
- **Per-class credit** replaces the single global dopamine scalar (D9): a wrong
  trial depresses the chosen output while potentiating the real one. Without
  this, ~75 % of trials punish the entire readout uniformly and trained
  accuracy tracks below untrained.
- **Supervised linear decoder is the reported readout** (`evaluation.readout:
  linear`); raw argmax still drives dopamine credit during training. The decoder
  is refit per condition and collapses the per-output firing bias.
- **Output readout is trimmed** to `weight_ratio = 8` and low output noise
  (`noise.stage_weight_ratio.output = 0.1`); at higher output noise the re-tuned
  readout spikes measure their own noise and `trained <= untrained`. NOTE that
  through the real pipeline this low output noise under-drives the outputs once
  calibration injects base noise weight 4.0, and all conditions currently sit at
  chance.
- **Recruited units are decoder feature dimensions**, not folded into the
  4 category buckets (they still fold in for the argmax readout). This changes
  growth from "inject noise into 4 counts" (0.50 → 0.28) to "add dimensions the
  decoder can down-weight" (0.40s–0.5s).
- **The default task was moved to the real olfactory pathway** (`odor-v1`)
  after the visual chain proved to require synthetic connections. The input
  stage is now the first stage in `subcircuit.stages` whatever its name
  (`photoreceptor` or `olfactory`), and `encoding.make_mapping` returns either a
  `GridMapping` or an `OdorMapping`, both exposing `stimulus(word, config)`.
  This is a `[TO CONFIRM]` scope change relative to the original visual-only
  proposal; see `openspec/changes/add-fly-word-categorization/specs/odor-encoding/`.
- **Representation probes and a positive-control gate** were added to the
  evaluation (`evaluation.probes.{enabled,cv_folds,input_min,kc_margin}`, on by
  default): linear probes of class decodability at the input and Kenyon-cell
  stages of a fresh untrained network, plus a gate (input ≥ 0.90 AND
  Kenyon-cell ≥ chance + 0.05) that makes a null attributable to the
  instrument impossible to report silently.
- **A supervised-pretrained readout condition** (`trained_pretrained`,
  `learning.pretrain`) initialises the plastic readout from a supervised fit on
  that seed's Kenyon-cell features. It is a positive control: it fits the
  training words (train accuracy 1.000) and still generalises at chance.
- **A separable-task control** (`configs/odor-separable.yaml`,
  `encoding.odor.active_elsewhere: 0`) makes the four categories disjoint by
  construction, so a trivial linear readout of the stimulus solves the task
  (input probe 1.000) while the network and probes still land at chance.
- **A recode** (`configs/odor-recode.yaml`, `configs/odor-recode-50.yaml`,
  `network.pair_gain.olfactory>mushroom_body` 20/50 with
  `calibration.max_mean_rate_hz: 100`) raises the `ALPN → Kenyon cell` gain so
  the class code survives that transform. The recode is a diagnostic
  intervention outside the physiological band, not a new baseline.
- **Growth-matched controls** were added for the recode
  (`configs/odor-recode-50-controls.yaml`): `structural_random_category`
  (`structural.recruitment.category_guidance: false`) breaks label-guided
  category inheritance; `structural_unguided`
  (`recruitment.anchor: random`, `recruitment.input_source: random`) removes all
  reward/label guidance of growth; and `trained_extra_features`
  (`evaluation.extra_random_features: 300`) gives a no-growth decoder the same
  extra feature budget growth adds. Condition dicts may now carry a generic
  `overrides` list of dotted config keys. **Run at 10 seeds
  (`runs/recode-50-controls-merged`).** Outcome: growth beats no-growth
  (Holm *p* = 0.018) but is unchanged by removing label guidance (Holm *p* = 1.0)
  or category inheritance (Holm *p* = 1.0), and is beaten by the random-feature
  no-growth control (0.983 vs 0.750, Holm *p* = 0.018) — the growth gain is
  decoder capacity, not a learned, label-guided prototype.
- **The online MBON-argmax held-out accuracy is recorded** per condition
  (`extra.argmax_test_accuracy`, reported as `argmax_test_accuracy_mean`) so the
  plasticity-rule readout and the supervised decoder can be told apart. It is at
  chance (0.235–0.272) in every condition of the control run, where it was
  recorded; the two main recode runs did not record it. The previously reported
  0.351 for `trained_pretrained` is not present in any archived result and has
  been withdrawn.
- **Per-condition RNG isolation** (revision): `evaluate.run_condition` and
  `representation_probes` now call `seed_everything(seed)` at entry, so condition
  order no longer affects a condition's random stream and identical
  configurations are bit-identical. Validated on a smoke run (`trained` ==
  `structural_off` exactly). Archived runs predate the fix; their absolute
  numbers are provisional.
- **Statistics unified** (revision): one α = 0.05 plan; one-sided Wilcoxon versus
  chance with Holm across conditions; two-sided paired Wilcoxon contrasts with
  Holm across a pre-specified 11-pair family; sign-flip permutation test for the
  shuffled-label control against chance. The untrained-floor leakage exemption
  was removed (it was a goalpost move after the control failed). At n = 10 the
  exact two-sided Wilcoxon floor is 0.00195 and the one-sided floor is 0.00098.
- **Probe diagnostics** (revision): each probe pass records spontaneous and
  evoked firing rates per stage, the fraction of Kenyon cells active in the
  stimulus window, and their mean spike count. These separate a transform failure
  from a signal-to-noise failure; they are not in the archived runs.

## Licensing

Code: MIT (see `LICENSE`). Bundled font: DejaVu Sans Mono, see
`src/flyread/assets/LICENSE_DEJAVU`. FlyWire data: CC-BY-NC 4.0, not
distributed here.