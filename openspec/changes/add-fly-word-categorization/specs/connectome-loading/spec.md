# connectome-loading Specification

## ADDED Requirements

### Requirement: Pinned data source
The system SHALL load connectome data from a FlyWire release identified by
version string and file checksums recorded in configuration.

#### Scenario: Checksum match
- GIVEN configured checksums for the neuron and connectivity files
- WHEN the loader starts
- THEN it verifies each file against its checksum
- AND aborts with a clear error if any checksum differs

### Requirement: Schema validation
The system SHALL validate that required columns exist before building a
network, and SHALL NOT assume column names silently.

#### Scenario: Missing column
- GIVEN a connectivity file lacking a required column
- WHEN the loader validates the schema
- THEN it fails with a message naming the missing column and the expected schema

### Requirement: Stable index mapping
The system SHALL map FlyWire neuron identifiers to contiguous internal
indices deterministically.

#### Scenario: Repeatable mapping
- GIVEN the same input files
- WHEN the mapping is built twice
- THEN both mappings are identical

### Requirement: Dropped-edge accounting
The system SHALL report how many connections were dropped because an
endpoint was not in the neuron list.

#### Scenario: Unknown endpoint
- GIVEN edges referencing neuron IDs absent from the neuron table
- WHEN the connectome is loaded
- THEN those edges are excluded
- AND the count of excluded edges is logged and stored in the manifest

### Requirement: Subcircuit extraction
The system SHALL extract a subcircuit containing photoreceptor input
neurons, intermediate visual pathway neurons, mushroom body neurons, and
output neurons, using annotation fields rather than hard-coded indices.

#### Scenario: Manifest produced
- GIVEN a configured subcircuit definition
- WHEN extraction completes
- THEN a manifest lists neuron counts per role, synapse counts, and the
  annotation rules used
- AND the manifest is saved with the run outputs

#### Scenario: Output neuron selection
- GIVEN annotation fields identifying candidate output neurons
- WHEN 4 output neurons are selected
- THEN the selection rule and the chosen neuron IDs are recorded in the manifest
- AND the selection is reproducible across runs

### Requirement: Neurotransmitter sign mapping
The system SHALL assign each synapse an excitatory or inhibitory sign from
the neurotransmitter annotation, with the mapping documented and configurable.

#### Scenario: Sign assignment
- GIVEN synapses annotated ACh, glutamate, and GABA
- WHEN signs are assigned under the default mapping
- THEN ACh and glutamate synapses are excitatory and GABA synapses are inhibitory
- AND any unrecognized neurotransmitter label raises a logged warning

### Requirement: Memory budget
The system SHALL report peak memory usage when loading and building the
subcircuit.

#### Scenario: Budget check
- GIVEN a configured memory budget
- WHEN the subcircuit is built
- THEN peak memory is logged
- AND the run fails fast if the budget is exceeded
