# Tasks: add-fly-word-categorization

Each task references the spec it satisfies. Do not start Task 1+ until the
Task 0 gate passes or scope is formally revised.

Checkmark = implemented and covered by tests in this working tree. Item 7.4
(the formal >= 10-seed significance run) and item 9 (final reproduction and
report) require the completed foreground experiment runs and remain open.

## 0. Feasibility gate (blocks everything else)
- [ ] 0.1 Pin the FlyWire release and record file names, versions, checksums [connectome-loading]
- [ ] 0.2 Inspect real file schemas; list actual column names (do not assume) [connectome-loading]
- [ ] 0.3 Draft candidate subcircuit definitions (small / medium / large) from annotations [connectome-loading]
- [ ] 0.4 Benchmark each candidate on the target laptop: simulated time per wall-clock time, peak memory [reproducibility]
- [ ] 0.5 Decide subcircuit size and trial budget; document the result and set wall-clock limit [reproducibility]
- [ ] 0.6 If nothing fits, STOP and revise scope with the project owner

## 1. Project scaffolding
- [x] 1.1 Create repo layout from design.md (src/flyread, configs, data, tests)
- [x] 1.2 Pin Python and dependencies (including Brian2) with a lockfile [reproducibility]
- [x] 1.3 Create configs/default.yaml with every tunable parameter [reproducibility]
- [x] 1.4 Implement seeding utility and run manifest writer [reproducibility]

## 2. Connectome loading
- [x] 2.1 Checksum verification and schema validation [connectome-loading]
- [x] 2.2 Stable neuron index mapping and dropped-edge accounting [connectome-loading]
- [x] 2.3 Neurotransmitter sign mapping with unknown-label warnings [connectome-loading]
- [x] 2.4 Subcircuit extraction by annotation rules; manifest output [connectome-loading]
- [x] 2.5 Output neuron selection (4 neurons), recorded in manifest [connectome-loading]
- [x] 2.6 Memory budget reporting and fail-fast [connectome-loading]

## 3. Dataset and visual encoding
- [x] 3.1 Review and curate data/words.csv; resolve ambiguous words [visual-encoding]
- [x] 3.2 Dataset validator (4 letters, no duplicates, balanced categories) [visual-encoding]
- [x] 3.3 Deterministic word rendering with bundled font [visual-encoding]
- [x] 3.4 grid-v1 pixel-to-photoreceptor mapping, with scheme id in manifest [visual-encoding]
- [x] 3.5 Rate-to-current function with documented units [visual-encoding]
- [x] 3.6 Presentation timing and inter-trial state reset [visual-encoding]
- [x] 3.7 Shuffled-pixel control [visual-encoding]

## 4. Network and calibration
- [x] 4.1 Build Brian2 LIF network from subcircuit, with reserve pool [dopamine-learning, structural-plasticity]
- [x] 4.2 No-stimulus calibration of global weight scale [dopamine-learning]
- [x] 4.3 Save calibration results in the manifest [dopamine-learning]

## 5. Dopamine learning
- [x] 5.1 Declare eligibility trace state variable and decay [dopamine-learning]
- [x] 5.2 Restrict plasticity to the configured Kenyon cell to output synapses [dopamine-learning]
- [x] 5.3 Dopamine-gated weight update with bounds [dopamine-learning]
- [x] 5.4 Readout with seeded tie-breaking and no-response handling [dopamine-learning]
- [x] 5.5 Dopamine-off switch [dopamine-learning]
- [x] 5.6 Per-class (per-output) credit assignment, default on [dopamine-learning]

## 6. Structural plasticity
- [x] 6.1 Synaptic pruning with consecutive-check rule [structural-plasticity]
- [x] 6.2 Neuron silencing by activity window [structural-plasticity]
- [x] 6.3 Recruitment from reserve pool using pseudorandom population-matched synapses, wired as plastic readout units [structural-plasticity]
- [x] 6.4 Per-interval and total change caps [structural-plasticity]
- [x] 6.5 Event log and on/off ablation switch [structural-plasticity]
- [x] 6.6 Prune recruited-reserve synapses so deletions track a configured fraction of additions [structural-plasticity]
- [x] 6.7 Recruited units are returned to the supervised decoder as separate feature dimensions [structural-plasticity]

## 7. Evaluation
- [x] 7.1 Seeded train/test split (15/5 per category) [evaluation]
- [x] 7.2 Train loop with learning curve recording [evaluation]
- [x] 7.3 Baselines and ablations: untrained, shuffled labels, dopamine off, shuffled pixels, structural on/off [evaluation]
- [ ] 7.4 Multi-seed runner (>= 10 seeds), statistics against 25% chance [evaluation]
- [x] 7.5 Confusion matrix, no-response rate, report generation [evaluation]
- [x] 7.6 Data-leakage warning if shuffled labels exceed chance [evaluation]
- [x] 7.7 Supervised linear decoder as the reported readout, fit on a fixed post-training feature pass [evaluation]

## 8. Testing
- [x] 8.1 One automated test per spec scenario (test groups per spec module) [evaluation]
- [x] 8.2 Same-seed determinism test within stated tolerance [reproducibility]
- [x] 8.3 Small-scale smoke test runnable in minutes on the laptop

## 9. Reproduction and reporting
- [x] 9.1 Single documented command sequence from clean checkout to report [reproducibility]
- [x] 9.2 Final report, including negative or null results explicitly [evaluation] -> docs/final-report.md (real 2-seed run is a null result; three-axis probe sweep confirms the readout stays at chance)
- [ ] 9.3 Run `openspec validate` (and verify/archive per your OpenSpec version) after implementation

## Open items [TO CONFIRM]
- Significance level and number of seeds beyond 10
- Wall-clock limit for the full experiment
- Acceptable spontaneous firing range and continuous-firing limit
- Whether 15/5 train/test split gives enough statistical power
- Whether structural-on with the current >80%-divergence recruitment rule can
  exceed the trained readout on the linear-decoder metric (measured through the
  real pipeline: both sit at chance -- untrained 0.258, trained 0.233,
  structural_on 0.283 -- because the output stage is under-driven, so no rule
  change can be evaluated until the output drive is fixed; see design D9
  CORRECTION)
- Output-stage drive under the calibration-accepted regime (base noise weight
  4.0 with stage ratio 0.1 leaves outputs mostly silent -- no-response
  0.42-0.65 -- and every real-pipeline condition at chance; three probe sweeps
  -- readout ratio to 50 and output noise to 1.0, stimulus contrast max_hz to
  800, and bridge synapses_per_kc to 48 -- all keep the untrained readout at
  chance (0.18-0.33, within single-seed noise) while restoring output firing
  to 100%, so the null result is an UPSTREAM (mushroom-body) signal loss, not
  a readout drive problem; see docs/final-report.md)