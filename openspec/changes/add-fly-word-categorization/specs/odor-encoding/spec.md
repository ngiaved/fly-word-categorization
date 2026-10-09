# odor-encoding Specification

## ADDED Requirements

### Requirement: Versioned input mapping schemes
The system SHALL support more than one input scheme, select the active scheme
by a `scheme_id` in configuration, and record that id in the run manifest. The
two schemes defined here are `grid-v1` (visual, see the visual-encoding spec)
and `odor-v1` (olfactory).

#### Scenario: Scheme recorded
- GIVEN a configured scheme id
- WHEN a run is prepared
- THEN the manifest's `encoding.mapping` block carries `scheme_id`
- AND the value matches the configuration

### Requirement: Sparse olfactory word encoding
For scheme `odor-v1`, the system SHALL encode each word as a deterministic
sparse pattern over the olfactory input stage, without rendering an image. A
configured number of inputs SHALL be active at graded drive in a configured
range; every other input SHALL be silent.

#### Scenario: Deterministic per word
- GIVEN the same word, configuration, and seed
- WHEN the word is encoded twice
- THEN the two per-input activation vectors are identical

#### Scenario: Sparsity and drive range
- GIVEN `active_from_own` and `active_elsewhere` configured
- WHEN a word is encoded
- THEN exactly `active_from_own + active_elsewhere` inputs are non-zero
- AND every active drive lies within `[min_drive, max_drive]`
- AND every other input is exactly zero

#### Scenario: Distinct words differ
- GIVEN two different words
- WHEN both are encoded
- THEN their activation vectors are not identical

### Requirement: Category structure exists in the stimulus
For scheme `odor-v1`, each category SHALL own a fixed, seeded, disjoint
prototype subset of the inputs, and every word SHALL draw most of its active
inputs from its own category prototype. Word pairs within a category SHALL
share more active inputs than word pairs across categories.

#### Scenario: Within-category overlap exceeds across-category overlap
- GIVEN the labeled dataset and a seeded mapping
- WHEN every same-category and cross-category word pair is compared
- THEN the median within-category active-input overlap exceeds the median
  cross-category overlap by a documented margin
- AND no cross-category pair exceeds the configured cross-category budget

### Requirement: Mapping size matches the simulated input stage
For scheme `odor-v1`, the number of inputs in the encoding SHALL equal the
number of simulated input-stage neurons, which can be smaller than the
configured cap when candidates are dropped during extraction.

#### Scenario: Size override
- GIVEN a selected input stage smaller than the configured `n_inputs` cap
- WHEN the evaluation builds the mapping
- THEN the mapping's input count equals the selected stage size
- AND a stimulus vector has exactly that many elements

### Requirement: Olfactory input stage identification
The system SHALL treat the first stage in `subcircuit.stages` (signal order) as
the input layer, whatever its name (`photoreceptor` for `grid-v1`, `olfactory`
for `odor-v1`), and drive that stage with the stimulus current.

#### Scenario: Olfactory pathway is driven
- GIVEN a subcircuit whose first stage is `olfactory`
- WHEN a word is presented
- THEN the stimulus current is applied to the `olfactory` neuron group
- AND no `photoreceptor`-named stage is required
