# reproducibility Specification

## ADDED Requirements

### Requirement: Pinned environment
The system SHALL provide a lockfile or equivalent pinning exact dependency
versions, including Brian2, and the Python version.

#### Scenario: Clean install
- GIVEN a clean machine meeting the hardware target
- WHEN the documented install steps are followed
- THEN the environment matches the pinned versions

### Requirement: Seeded randomness
The system SHALL seed all random sources (Python, NumPy, Brian2) from a
single run seed recorded in the manifest.

#### Scenario: Same seed same result
- GIVEN two runs with identical configuration, data, environment, and seed
- WHEN both complete
- THEN their reported metrics are identical or within the documented tolerance

### Requirement: Run manifest
The system SHALL save a manifest per run containing: configuration, seed,
data version and checksums, subcircuit manifest, mapping scheme id,
dependency versions, Brian2 code generation target, hardware summary, and
git commit hash.

#### Scenario: Manifest present
- GIVEN any completed run
- WHEN its output directory is inspected
- THEN a manifest with all listed fields exists

### Requirement: Configuration-driven runs
The system SHALL take all tunable parameters from a configuration file with
no behavior-changing constants hidden in code.

#### Scenario: Override logged
- GIVEN a command-line override of a configuration value
- WHEN the run starts
- THEN the effective configuration is written to the manifest

### Requirement: CPU benchmark gate
The system SHALL include a benchmark that measures simulated time per
wall-clock time and peak memory for the chosen subcircuit on the target
hardware class.

#### Scenario: Gate result
- GIVEN the benchmark on a laptop with 8-16 GB RAM
- WHEN it completes
- THEN it reports whether the planned trial budget fits within the
  configured wall-clock limit [TO CONFIRM]
- AND the result is stored with the project documentation

### Requirement: Clean-checkout reproduction
The system SHALL provide one documented command sequence that regenerates
the reported tables and figures from a clean checkout.

#### Scenario: Regenerate report
- GIVEN a clean checkout and the pinned data files
- WHEN the documented sequence is run
- THEN the final report is regenerated with the same numbers (within tolerance)
