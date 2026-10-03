# Proposal: add-fly-word-categorization

## Why
Test whether a fixed FlyWire connectome subcircuit, combined with
dopamine-gated plasticity and an exploratory structural-plasticity
mechanism, can learn to classify 4-letter English words into 4 semantic
categories. Results must be measurable and reproducible by a third party on
a CPU-only laptop (8-16 GB RAM).

## Decisions recorded (from requirements clarification)
| Question | Decision |
|---|---|
| What counts as success for v1? | Reproducible, research-grade results |
| Where do growth and pruning go? | In v1, together with everything else |
| Compute target | CPU laptop, 8-16 GB RAM |

## Tension acknowledged
Research-grade rigor, structural plasticity in v1, and CPU-only hardware do
not fit a full 139k-neuron simulation over thousands of training trials.
v1 therefore simulates a **subcircuit** (visual input pathway, mushroom
body, output neurons). This is validated by a benchmark gate (Task 0)
before any other work proceeds. If the benchmark fails, scope is reduced or
a separate change is opened; the spec is not silently relaxed.

## What Changes
- New Brian2 simulation pipeline using a pinned FlyWire release
- Visual encoding of words into photoreceptor spike input
- Dopamine-modulated plasticity in mushroom body synapses
- Structural plasticity: synaptic pruning, neuron silencing, and neuron
  recruitment from a pre-allocated reserve pool (Brian2 NeuronGroups have
  fixed size)
- Evaluation harness with baselines, ablations, and multi-seed statistics
- Reproducibility tooling (pinned environment, seeds, run manifests)

## Capabilities (new)
- connectome-loading
- visual-encoding
- dopamine-learning
- structural-plasticity
- evaluation
- reproducibility

## Non-goals
- Any claim about consciousness
- Biological claims about neurogenesis (the mechanism is exploratory design,
  with no published precedent found)
- GPU or cloud execution (possible later change)
- Whole-brain (139k neuron) training in v1
- Biologically faithful ommatidia mapping (v1 uses a documented simplification)

## Success criteria
Thresholds marked [TO CONFIRM] require approval.
- Held-out accuracy significantly above the 25% chance level across
  >= 10 seeds (significance level [TO CONFIRM], proposed p < 0.01)
- Ablation results reported: untrained network, shuffled labels,
  dopamine off, structural plasticity on vs off
- Full pipeline reruns from a clean checkout and reproduces reported
  numbers within a stated tolerance
- A negative result (no learning, or structural plasticity not helping) is
  an acceptable, reportable outcome

## Impact
- Hardware: CPU only, 8-16 GB RAM
- Data: FlyWire release pinned by version and checksum
- Language: Python 3.11, Brian2
