# structural-plasticity Specification

NOTE: This capability is exploratory design with no published precedent
found. It is a modeling mechanism, not a claim about real neurogenesis.

## ADDED Requirements

### Requirement: Fixed-size reserve pool
The system SHALL pre-allocate reserve neurons at network construction with
no synapses, because Brian2 NeuronGroups cannot change size at runtime.

#### Scenario: Reserve inactive
- GIVEN a configured reserve pool size
- WHEN the network is built
- THEN reserve neurons exist and have no incoming or outgoing synapses
- AND they never spike

### Requirement: Synaptic pruning
The system SHALL remove the effect of plastic synapses whose weight stays
below a configured threshold for a configured number of checks.

#### Scenario: Prune weak synapse
- GIVEN a plastic synapse below the weight threshold for the required checks
- WHEN the pruning step runs
- THEN its weight is set to zero and it is marked pruned
- AND the event is logged with trial number and synapse identifiers

### Requirement: Neuron silencing
The system SHALL silence neurons whose activity stays below a configured
threshold over a configured window, by disconnecting them.

#### Scenario: Silence inactive neuron
- GIVEN a plastic-pathway neuron below the activity threshold for the window
- WHEN the silencing step runs
- THEN all its synapses are disabled
- AND the event is logged

### Requirement: Neuron recruitment
The system SHALL recruit reserve neurons by connecting them with weak
synapses cloned, with noise, from a configured template neuron.

#### Scenario: Recruit from reserve
- GIVEN an available reserve neuron and a template neuron
- WHEN the growth rule triggers
- THEN the reserve neuron receives weak cloned synapses
- AND the event is logged with trial number, template ID, and new neuron ID

#### Scenario: Reserve exhausted
- GIVEN no reserve neurons remain
- WHEN the growth rule triggers
- THEN no recruitment occurs
- AND a warning is logged once

### Requirement: Rate limits and safety
The system SHALL cap the number of structural changes per check interval and
the total fraction of the plastic pathway that can change.

#### Scenario: Cap enforced
- GIVEN more candidates than the per-interval cap
- WHEN the structural step runs
- THEN only the capped number of changes are applied, chosen deterministically

### Requirement: Ablation switch
The system SHALL allow structural plasticity to be turned off by
configuration without altering any other behavior.

#### Scenario: Controlled comparison
- GIVEN two runs with the same seed, one with structural plasticity on and one off
- WHEN both complete
- THEN they differ only in structural events and their downstream effects
- AND both appear in the evaluation report

### Requirement: Event log
The system SHALL write a complete structural event log to the run outputs.

#### Scenario: Log content
- GIVEN a completed run with structural plasticity on
- WHEN the log is read
- THEN every prune, silence, and recruit event is present with its trial number
