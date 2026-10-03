# Design: add-fly-word-categorization

## Context
Existing public projects (e.g. `fly-brain`, `fly_ocr`, `fly-neuromod`)
show connectome simulation, character recognition with a fixed connectome
plus an external decoder, and dopamine plasticity in the mushroom body.
None combines these with structural plasticity. Structural plasticity here
is original, unvalidated design.

## Goals / Non-goals
See proposal.md. Key constraint: CPU laptop, research-grade reproducibility.

## Architecture
```
word (text)
  -> visual-encoding: render -> pixel grid -> photoreceptor rates -> currents
  -> connectome-loading: subcircuit (photoreceptors ... mushroom body ... outputs)
  -> Brian2 LIF simulation (CPU)
  -> readout: spike counts of 4 designated output neurons -> predicted category
  -> dopamine-learning: reward/punishment gates eligibility-trace weight updates
  -> structural-plasticity: periodic pruning / silencing / recruitment
  -> evaluation: held-out accuracy, baselines, ablations, multi-seed stats
```

## Decisions

### D1: Simulate a subcircuit, not the whole brain
Reason: laptop CPU budget. The subcircuit is defined from FlyWire
annotations (photoreceptors -> optic lobe -> mushroom body -> output
neurons) and recorded in a manifest. Task 0 measures simulation speed and
memory on a candidate subcircuit and sets the trial budget.
Trade-off: results apply to the subcircuit, not the whole brain.

### D2: Neuron model is leaky integrate-and-fire (LIF)
Reason: scalable, matches existing projects. Trade-off: no dendritic or
detailed biophysics. Parameters (tau, thresholds, reset) are configuration.

### D3: Synaptic weights from synapse counts with a calibrated scale
Weights derive from FlyWire synapse counts times a global scale factor.
The scale is not assumed; it is calibrated in a no-stimulus run so the
network is neither silent nor saturated (see dopamine-learning spec).
Sign comes from predicted neurotransmitter (ACh, glutamate treated as
excitatory in v1 as a documented simplification; GABA inhibitory).
[TO CONFIRM] against FlyWire annotation semantics.

### D4: Learning only at mushroom body Kenyon cell -> output synapses
Three-factor rule: an eligibility trace (declared state variable) marks
recently active synapses; a scalar dopamine signal (reward +1, punishment
-1) converts eligibility into weight change. Weights are clipped to bounds.

### D5: Readout is a winner-take-most spike count over 4 output neurons
Predicted category = argmax spike count in the response window. Ties are
broken deterministically (seeded). Output neuron selection is data-driven
and documented in the manifest, not hard-coded by index.

### D6: Structural plasticity through a fixed-size reserve pool
Brian2 NeuronGroups cannot grow at runtime. Reserve neurons exist from
construction with no synapses (inactive). Recruitment connects them;
silencing disconnects neurons. Synaptic pruning zeroes weights and removes
the synapse from effect (mask or weight = 0). All events are logged.
This is a modeling compromise, not a claim about biology.

### D7: Visual encoding is a documented simplification
Words are rendered with a fixed bundled font into a small grayscale image,
then mapped onto photoreceptor neurons through a grid mapping with a
versioned scheme id. A biologically faithful ommatidia/retinotopic mapping
is deferred to a later change.

### D8: Brian2 CPU code generation target
Use a compiled code generation target if available (Cython) with a numpy
fallback; the choice is recorded in the run manifest because it affects
speed and (slightly) numerical results.

## Module layout
```
src/flyread/
  connectome.py     load, validate, subcircuit extraction, manifest
  network.py        Brian2 network construction, calibration
  encoding.py       word rendering, rates, currents
  learning.py       eligibility trace, dopamine signal, readout
  structural.py     pruning, silencing, recruitment, event log
  evaluate.py       splits, baselines, ablations, statistics
  repro.py          seeds, manifests, checksums
  cli.py            entry points (calibrate, train, evaluate, report)
configs/default.yaml
data/words.csv
tests/              one test group per spec scenario
```

## Risks / Trade-offs
- Subcircuit may be too small or too large for the CPU budget -> Task 0 gate.
- Learning may not exceed chance with a fixed random-like readout path ->
  negative results are acceptable and reportable.
- Simplified visual mapping may hide or create effects -> documented, with
  a shuffled-pixel control in evaluation.
- Structural plasticity may destabilize the network -> rate limits,
  stability checks, and an on/off ablation.
- Brian2 stochastic elements can break determinism -> explicit seeding and
  stated tolerance.

## Open Questions [TO CONFIRM]
- Exact FlyWire release, file names, and column names (unverified here)
- Which annotation fields reliably identify photoreceptors and output neurons
- Final subcircuit size after the benchmark
- Significance threshold and number of seeds beyond the minimum of 10
- Whether 15/5 per-category train/test split is enough statistical power
