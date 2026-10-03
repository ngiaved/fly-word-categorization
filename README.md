# Fly Word Categorization (FlyWire connectome + dopamine learning)

Spec-driven project using [OpenSpec](https://github.com/Fission-AI/OpenSpec).
All code, documentation, dataset words, and prompts are in English.

## What this is
A simulation of a *Drosophila* connectome subcircuit (FlyWire) that receives
4-letter English words as visual input and learns, via dopamine-gated
plasticity, to activate the correct one of 4 output neurons
(animal / food / tool / place). Structural plasticity (synaptic pruning and
neuron recruitment) is included as an exploratory, ablatable mechanism.

Out of scope: any claim about consciousness.

## Layout
```
openspec/changes/add-fly-word-categorization/
  proposal.md      why / what / scope / success criteria
  design.md        architecture and technical decisions
  tasks.md         ordered implementation checklist (traceable to specs)
  specs/<capability>/spec.md   requirements + GIVEN/WHEN/THEN scenarios
data/words.csv     80 four-letter English words, 20 per category
```

## How to use
1. Install OpenSpec: `npm install -g @fission-ai/openspec@latest`
2. In your repo root run `openspec init`, then copy the `openspec/changes/`
   folder from this archive into the generated `openspec/` directory.
3. Run `openspec list` and `openspec validate` to check the change
   (command names per the OpenSpec docs; verify against your installed version).
4. Review and approve the proposal BEFORE implementing anything.
5. Implement following `tasks.md`, starting with the Task 0 benchmark gate.

## Items marked [TO CONFIRM]
Numeric thresholds and some FlyWire file/column details were not verified
against real data. They are flagged in the documents and must be settled
during Task 0 and Task 1.
