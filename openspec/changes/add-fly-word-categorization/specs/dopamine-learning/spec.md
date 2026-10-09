# dopamine-learning Specification

## ADDED Requirements

### Requirement: Network stability calibration
The system SHALL provide a calibration run with no external stimulus that
measures spontaneous firing and sets the global weight scale accordingly.

#### Scenario: Stable regime
- GIVEN a calibrated weight scale
- WHEN the network runs with no stimulus
- THEN the mean firing rate lies within the configured acceptable range
- AND the fraction of neurons firing continuously stays below the configured limit
- AND the chosen scale and measured rates are saved in the manifest

#### Scenario: Unstable regime
- GIVEN a weight scale producing silence or saturation
- WHEN calibration evaluates it
- THEN it is rejected and the search continues

### Requirement: Eligibility trace
The system SHALL maintain a declared eligibility trace per plastic synapse
that increases with coincident pre/post activity and decays with a
configurable time constant.

#### Scenario: Decay
- GIVEN a synapse with nonzero eligibility
- WHEN no further activity occurs
- THEN its eligibility decays toward zero at the configured rate

### Requirement: Dopamine-gated weight update
The system SHALL change plastic weights only when a dopamine signal is
delivered, in proportion to eligibility and learning rate.

#### Scenario: Reward
- GIVEN a trial whose predicted category equals the true category
- WHEN reward is delivered (+1)
- THEN eligible synapses increase weight in proportion to their eligibility

#### Scenario: Punishment
- GIVEN a trial whose predicted category differs from the true category
- WHEN punishment is delivered (-1)
- THEN eligible synapses decrease weight in proportion to their eligibility

#### Scenario: No dopamine
- GIVEN dopamine disabled by configuration
- WHEN trials run
- THEN no plastic weight changes occur

### Requirement: Plasticity locus
The system SHALL restrict plastic synapses to Kenyon cell to output
pathway synapses in the mushroom body; all other synapses stay fixed.

#### Scenario: Fixed synapses
- GIVEN training of any length
- WHEN weights are compared before and after
- THEN synapses outside the plastic set are unchanged

### Requirement: Weight bounds
The system SHALL clip plastic weights to configured lower and upper bounds.

#### Scenario: No explosion
- GIVEN repeated rewards on the same synapse
- WHEN the weight reaches the upper bound
- THEN it does not exceed the bound

### Requirement: Per-class credit assignment
The system SHALL support per-output credit so a wrong trial punishes the chosen
output and rewards the correct one in the same trial, instead of applying one
global scalar to every plastic synapse. A scalar (global) mode remains
available; the configured default is per-class
(`learning.credit_assignment = per_class`).

The per-class valence vector is a "perceptron-style" correction: the wrong
readout is depressed while the correct one is potentiated on the same trial.
The three-factor rule is preserved because the valence is gated by the real
reinforcement-neuron response (`_deliver_teaching`), so the update never runs
unless genuine reinforcement neurons spike.

#### Scenario: Correct trial
- GIVEN a per-class trial whose predicted category equals the true category
- WHEN the update runs
- THEN only the winning (correct) output's synapses are potentiated by the
  reward magnitude

#### Scenario: Wrong trial corrects both poles
- GIVEN a per-class trial whose predicted category differs from the true category
- WHEN the update runs
- THEN the chosen (wrong) output's synapses are depressed by the punishment
  magnitude
- AND the true output's synapses are potentiated by the reward magnitude in
  the same trial

#### Scenario: Teacher gating still applies
- GIVEN a per-class trial
- WHEN the reinforcement neurons do not fire (gate 0)
- THEN the valence vector is zeroed before the update, so no plastic weight
  changes (the RNA-mode update remains genuinely three-factor)

### Requirement: Credit magnitudes do not change the teacher drive
The reward and punishment magnitudes (`learning.reward`, `learning.punishment`)
SHALL set the per-output credit floor and the dopamine VALUE only; the teacher
gate saturates at 1.0 because the reinforcement stage is driven at fixed
currents. This lets baseline tuning (e.g. lowering punishment potency) shift the
readout projection without breaking the neuron-gated plasticity.

#### Scenario: Magnitude affects credit, not gate
- GIVEN reward 1.5 / punishment -0.5
- WHEN a reward trial runs
- THEN the winning output is potentiated with magnitude 1.5
- AND the teacher gate is still the real spike-ratio gate (saturating at 1.0)

### Requirement: Readout
The system SHALL predict a category as the output neuron with the highest
spike count during the response window, breaking ties with a seeded
deterministic rule.

#### Scenario: Clear winner
- GIVEN spike counts [3, 9, 2, 1] for the four output neurons
- WHEN the readout runs
- THEN the predicted category is index 1

#### Scenario: Tie
- GIVEN equal highest spike counts on two output neurons
- WHEN the readout runs twice with the same seed
- THEN the same category is chosen both times

#### Scenario: No spikes
- GIVEN zero spikes on all output neurons
- WHEN the readout runs
- THEN the trial is recorded as "no response" and counted as incorrect
- AND the no-response rate is reported
