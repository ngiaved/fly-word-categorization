# visual-encoding Specification

## ADDED Requirements

### Requirement: Deterministic word rendering
The system SHALL render each 4-letter word to a grayscale image using a
fixed bundled font, fixed size, and fixed layout.

#### Scenario: Identical rendering
- GIVEN the same word and configuration
- WHEN the word is rendered on two different runs
- THEN the resulting images are pixel-identical

### Requirement: Word dataset constraints
The system SHALL use a dataset in which every word is exactly 4 letters,
uppercase-normalized, with exactly 4 categories and equal words per category.

#### Scenario: Dataset validation
- GIVEN data/words.csv
- WHEN the dataset validator runs
- THEN it confirms every word has exactly 4 letters
- AND confirms there are no duplicate words
- AND confirms each category has the same number of words
- AND otherwise fails listing the offending rows

### Requirement: Versioned photoreceptor mapping
The system SHALL map image pixels to photoreceptor neurons through a mapping
identified by a scheme id, and SHALL record that id in the run manifest.

#### Scenario: Grid mapping v1
- GIVEN mapping scheme "grid-v1"
- WHEN an image is encoded
- THEN each photoreceptor receives the mean darkness of its assigned pixel patch
- AND the mapping is documented as a simplification of real ommatidia layout

### Requirement: Rate to current conversion
The system SHALL convert encoded rates into input to photoreceptor neurons
through a defined, configurable function with documented units.

#### Scenario: Zero input
- GIVEN an all-white image
- WHEN it is encoded
- THEN photoreceptor input is at baseline

#### Scenario: Monotonic response
- GIVEN two images where one is uniformly darker
- WHEN both are encoded
- THEN the darker image produces input that is greater or equal at every photoreceptor

### Requirement: Stimulus presentation
The system SHALL present each word for a configurable duration, followed by a
configurable inter-trial rest period that returns network state to baseline.

#### Scenario: State reset between trials
- GIVEN consecutive trials
- WHEN the rest period elapses
- THEN membrane potentials and eligibility traces are reset or decayed to the
  documented baseline before the next word is presented

### Requirement: Shuffled-pixel control
The system SHALL support a control condition in which pixel-to-photoreceptor
assignments are randomly permuted with a seed.

#### Scenario: Control available
- GIVEN the control enabled in configuration
- WHEN encoding runs
- THEN the permutation is seeded and recorded in the manifest
