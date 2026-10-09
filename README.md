# FlyWord

Dopamine-gated plasticity in a subcircuit of the *Drosophila* FlyWire connectome,
driven by rendered 4-letter word stimuli.

Words are rendered to a pixel grid, mapped onto photoreceptors, propagated
through a real optic-lobe subcircuit (lamina, medulla, lobula) extracted from
FlyWire release 783, into a mushroom-body/output stage that learns by a
three-factor dopamine rule. Four output neurons are read out as
`animal / food / tool / place`.

---

## Status: read this before trusting any number

This repository contains a working implementation and an honest account of what
does and does not work yet. **No results should be reported from it yet.**

| Area | State |
|---|---|
| Data download, checksum verification, schema validation | Working |
| Subcircuit extraction from real FlyWire connectivity | Working (~13 s, 16.8 M rows) |
| Per-connection neurotransmitter signs | Working |
| Deterministic word rendering + encoding | Working |
| Brian2 network construction | Working |
| Calibration to a spontaneous operating regime | Working (accepted in-band scale 4–6) |
| CPU feasibility gate (Task 0.3/0.4) | **Not yet passed** |
| Learning / evaluation / report | Runs end to end; the first real 2-seed run is a **null result** (all conditions at chance) and the readout-drive sweep confirms the signal is lost upstream, not at the output stage |

The current blocking item is the **mushroom-body word signal**: the first real
2-seed run is a null result, and a drive sweep shows that even with the output
stage firing on 100% of presentations (readout ratio >= 20), the untrained
linear readout stays at chance (0.22–0.33, single-seed noise). The word-class
information the readout needs does not reach the mushroom body under the
calibrated operating regime. The result is documented in full in
[docs/final-report.md] (negative result, reported without reframing).
See [Known issues](#known-issues).

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
essentially no optic-lobe input to the mushroom body.

This is real biology, not a bug in the annotation filters: **the mushroom body
is not a visual target in *Drosophila*.** It is driven by the antennal lobe
and by reinforcement neurons. So the required pathway cannot be built from real
connectivity without inventing a connection, and inventing one silently would
make the whole experiment meaningless.

### What was decided instead

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

2. **Real reinforcement neurons as the teachers.** The `MBIN` class in release
   783 contains exactly 2× APL (GABA) and 2× DPM (dopamine) neurons, which make
   real synapses onto Kenyon cells. The reward **value** is synthetic, but it
   is injected into these real neurons, and their resulting spike count **gates**
   the weight update. Plasticity therefore only happens if genuine
   reinforcement neurons respond — the three-factor rule is enforced by real
   neurons rather than by a bare scalar.

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
flyread --config configs/smoke.yaml selftest   # fast end-to-end smoke check
```

Useful flags: `--set key.path=value` (repeatable), `--seed N`,
`--log-level DEBUG`.

`configs/default.yaml` is the full experiment. `configs/smoke.yaml` is the same
code path with the subcircuit and trial budget shrunk for fast checks; it is
**not** a source of reportable numbers.

## What the subcircuit looks like

With `subcircuit.candidate: medium` and `bridge.max_sources: 64`:

| | |
|---|---|
| Neurons | 1,928 |
| Edges | 21,454 (19,150 real + 2,304 artificial) |
| Synapses | 147,466 (145,162 real + 2,304 artificial) |
| Per stage | photoreceptor 576, lamina 384, medulla 384, lobula 192, reinforcement 4, mushroom_body 384, output 4 |
| Signs | 15,364 excitatory / 3,786 inhibitory |
| Extraction time | ~13 s, single streaming pass, bounded memory |

Reachability is reported twice, and the distinction matters:

- `with_bridge.signal_reaches_outputs: true` — 4/4 outputs reachable
- `real_edges_only.signal_reaches_outputs: false` — **no visual signal reaches
  the mushroom body on real connectivity alone**

## Architecture

| Module | Responsibility |
|---|---|
| `flyread.config` | YAML loading, validation, dotted overrides |
| `flyread.repro` | checksums, seeding, run manifests, environment capture |
| `flyread.connectome` | schema validation, annotation rules, streaming connectivity, subcircuit selection, artificial bridge |
| `flyread.encoding` | deterministic rendering, `grid-v1` mapping, rate→current |
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

## Known issues

1. **Calibration converges to an in-band scale (4–6), but the paradigm is
   narrow.** With a deterministic resting LIF network every scale and every
   seed lands at either silence or runaway; only a handful of scales sit in the
   0.5–15 Hz band. The accepted scale is real (recorded in the manifest), but
   throughput is dominated by the per-trial overhead of the growth wiring, so a
   full ≥10-seed run is expensive.
2. **Brian2 falls back to NumPy codegen** on machines without a working C++
   compiler, which changes throughput by roughly an order of magnitude. Fix
   the toolchain before trusting any benchmark number.
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
6. **`structural_on` has not yet beaten `trained`, and through the real
   pipeline everything is at chance.** Through the real pipeline
   (`runs/run-20261008T204756Z`, 2 seeds, 100 training trials) untrained 0.258,
   trained 0.233, structural_on 0.283 against chance 0.25 — trained lands *at
   or below* untrained, so structural growth has no advantage above it to beat.
   The earlier 0.45–0.567 numbers came from a harness operating point and are
   withdrawn. Whether a more informative recruitment rule is needed (or whether
   any recruitment benefit exists) is unresolved.
7. **The word signal is lost upstream of the readout.** Through the real
   pipeline, 42–65 % of test presentations get a zero-spike output reading
   (`no-response`) at the archived drive, so both the argmax readout and the
   linear decoder collapse toward the category prior. The two probe sweeps
   (temp probes, seed 1000, untrained condition, chance 0.25) narrow the
   cause: raising `artificial_readout.weight_ratio` to >= 20 makes the outputs
   fire on 100 % of presentations, and raising `encoding.max_hz` contrast up to
   800 Hz neither lifts the readout above chance (0.217–0.333 across 17
   settings, all within single-seed noise of 0.25). The readout can only
   amplify what the Kenyon cells encode, and under the calibrated regime the
   mushroom body does not carry decodable word-class activity at its output
   stage.

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

## Licensing

Code: MIT (see `LICENSE`). Bundled font: DejaVu Sans Mono, see
`src/flyread/assets/LICENSE_DEJAVU`. FlyWire data: CC-BY-NC 4.0, not
distributed here.