# evaluation Specification

## ADDED Requirements

### Requirement: Held-out evaluation
The system SHALL evaluate on words never presented during training.

#### Scenario: Train/test split
- GIVEN a dataset of 80 words (20 per category)
- WHEN the split is generated with a given seed
- THEN 15 words per category are used for training and 5 for testing
- AND the split is identical on every run with that seed
- AND no word appears in both sets

### Requirement: Chance baseline
The system SHALL report accuracy against the 25% chance level.

#### Scenario: Statistical comparison
- GIVEN results from at least 10 independent seeds
- WHEN the report is generated
- THEN it states mean accuracy, a confidence interval, and a significance
  test against chance
- AND the significance level used is stated

### Requirement: Required baselines and ablations
The system SHALL run and report these conditions under identical seeds and
budgets: untrained network, shuffled training labels, dopamine off,
shuffled-pixel input, structural plasticity on, structural plasticity off.

#### Scenario: All conditions reported
- GIVEN a completed evaluation
- WHEN the report is generated
- THEN each condition appears with mean accuracy and confidence interval
- AND shuffled-labels accuracy is consistent with chance, otherwise a
  data-leakage warning is raised

### Requirement: Learning curves
The system SHALL record accuracy over training trials.

#### Scenario: Curve output
- GIVEN a training run
- WHEN it completes
- THEN a learning curve (accuracy per window of trials) is saved as data and as a plot

### Requirement: Confusion matrix and no-response rate
The system SHALL report a 4x4 confusion matrix and the no-response rate on
the held-out set.

#### Scenario: Report content
- GIVEN held-out predictions
- WHEN the report is generated
- THEN the confusion matrix and no-response rate are included

### Requirement: Supervised linear readout (decoder metric)
The system SHALL report accuracy through a multinomial logistic (linear)
decoder fit on the condition's own training count vectors and applied to the
held-out count vectors. The per-condition decoder corrects the per-output firing
biases that raw argmax cannot, and it is refit from each condition's own
outputs so untrained/trained/dopamine_off/structural_on remain a fair comparison
of the representation each condition produces. Raw argmax remains available and
is still what drives dopamine credit during training.

#### Scenario: Decoder on held-out counts
- GIVEN training trial count vectors and labels for a condition
- WHEN the report metric is computed
- THEN a logistic decoder is fit on the training vectors (`evaluation.readout: linear`)
- AND predictions on held-out vectors come from that decoder (not raw argmax)
- AND the decoder is refit independently for every condition

#### Scenario: Fixed decoder feature space under growth
- GIVEN a trained condition whose readout size changed mid-training (structural
  growth)
- WHEN the decoder fit vectors are collected
- THEN they are collected as a deterministic post-training pass over the
  training words with `learn=False`, all in one fixed feature space
- AND for conditions without growth these vectors are identical to the tail of
  the training loop, so reported numbers are unchanged

### Requirement: Negative results are reportable
The system SHALL report results the same way whether or not learning exceeds chance.

#### Scenario: No learning
- GIVEN held-out accuracy not significantly above chance
- WHEN the report is generated
- THEN it states that outcome explicitly without hiding or reframing it
