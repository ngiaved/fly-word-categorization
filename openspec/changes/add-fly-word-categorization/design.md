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

### D9: Per-output credit beats one global scalar (measured, post-hoc)
The original D4 scalar rule assigned ONE sign to every plastic synapse per
trial. At 4-way chance (~75% wrong) punishment therefore depressed the whole
readout uniformly, including the winner's own weights, and measured trained
accuracy drifted below the untrained control. Per-class credit corrects the
true output while punishing the chosen one on the same trial. Combined with
two measured fixes, this turned the readout from a null into a positive signal
**at inspect/harness operating points**:
- `network.artificial_readout.weight_ratio = 8` (was 0.05 then 20): at low
  gain the outputs fire ~0.6 spikes/word (decoded 0.15, below chance); at 20
  untrained already sits at the Kenyon-cell ceiling (~0.54) leaving training no
  headroom. 8 transmits the signal while leaving headroom for learning.
- `network.noise.stage_weight_ratio.output = 0.1` (was 1.0): at full weight the
  outputs measure their own Poisson noise, so re-tuned readout weights are
  drowned and trained <= untrained in every trial; at 0.1 trained >= untrained
  in the completed seeds.
- Recruited units are returned to the linear decoder as extra feature
  dimensions (they still fold into their category for the argmax readout),
  so growth adds capacity instead of injecting noise into 4 buckets.

CORRECTION (real pipeline, runs/run-20261008T204756Z, seeds 1000+1001, 100
training trials, linear readout; chance 0.25): through the FULL evaluation path
these settings do NOT produce the harness numbers above. The calibration-accepted
drive (weight_scale 4, base noise weight 4.0) under-drives the output stage
(`0.1` output ratio => 0.4 output noise) so the four outputs fire on only
~35-58 % of presentations; untrained 0.258, trained 0.233, structural_on 0.283
-- a null result, trained at or below untrained. The D9 harness numbers are
withdrawn. A three-axis probe sweep (untrained, seed 1000) then showed the
silence is not the blocker: raising the readout ratio to 50 restores 100 %
firing yet accuracy stays at chance (0.18-0.33), as do stimulus contrast up to
800 Hz max_hz and bridge densities to 48 synapses/KC. The
word-class signal is lost in the mushroom body under the calibrated regime, so
no readout-drive choice recovers it. Per-class credit and the
recruits-as-decoder-features decisions remain valid design choices; whether they
give a measurable payoff is now unconfirmed (see docs/final-report.md).

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
