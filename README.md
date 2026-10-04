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
| Calibration to a spontaneous operating regime | **Not yet converged** |
| CPU feasibility gate (Task 0.3/0.4) | **Not yet passed** |
| Learning / evaluation / report | Runs end to end, produces no meaningful accuracy yet |

The blocking item is the weight-scale calibration in `calibration`: no scale in
the current grid produces a spontaneous mean rate inside the required
0.5–15 Hz band with ≤2 % continuously firing neurons. See
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

1. **Calibration does not converge.** No weight scale in the grid lands in the
   0.5–15 Hz spontaneous band while keeping ≤2 % of neurons continuously
   active. This blocks the Task 0 gate and therefore all reported numbers.
2. **Brian2 falls back to NumPy codegen** on machines without a working C++
   compiler, which changes throughput by roughly an order of magnitude. Fix
   the toolchain before trusting any benchmark number.
3. **No test suite yet.** `flyread selftest` exercises the pipeline, but there
   are no unit tests for the scoring, split, confusion, or significance code.
4. **medulla → lobula is nearly empty** (1,117 synapses over all candidates),
   so the lobula stage is weakly driven in the real data. Stage selection for
   the lobula may need rethinking.
5. The reward signal is synthetic. Only the neurons it reaches are real.

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

## Licensing

Code: MIT (see `LICENSE`). Bundled font: DejaVu Sans Mono, see
`src/flyread/assets/LICENSE_DEJAVU`. FlyWire data: CC-BY-NC 4.0, not
distributed here.