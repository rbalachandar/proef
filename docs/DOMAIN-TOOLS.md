# Domain tools: priors from specialist models

Status: design note, nothing built yet. Roadmap item S-T7.

## Problem

The language model proposes hypotheses. For chemistry, biology and materials, specialist models (QSAR and ADMET
predictors, docking, protein models, interatomic potentials) predict endpoints better and cheaper than a general model.
Proef should be able to use them without becoming domain-specific and without letting any model decide a verdict.

## Principle

Flexibility lives in what is proposed. Rigidity lives in what is judged.

| Step | Who | Fixed or open |
|---|---|---|
| Choose tools and endpoints | the model proposes, code validates, a person approves | open |
| Run the tools | code | fixed |
| Show predictions | plan review, labelled as priors | fixed |
| Run the experiment | code, through the safety gateway | fixed |
| Judge the result | the pre-registered rule on measured data | fixed |

A prediction is never evidence. It can rank candidates and tell a scientist where to look. Only a measurement reaches the
verdict rule.

## The registry

Tools are registered, not built in. A tool is a Python callable or an MCP server plus a declaration:

| Field | Meaning |
|---|---|
| `endpoint`, `unit`, `direction` | what it predicts, for example `ic50_um`, lower is better |
| `input` | candidate representation it needs: SMILES, sequence, composition, structure |
| `modality` | the modality packs it applies to, or `any` |
| `domain` | an applicability-domain check (`ad.py`), so an outsider is flagged, never scored silently |
| `uncertainty` | interval, ensemble spread or none. A tool with none is shown but marked uncalibrated |
| `version`, `trained_on` | recorded in the ledger with every prediction |

Proef ships the contract and a few reference tools (RDKit descriptors, ADMET panel, the existing surrogate in
`acquisition.py`). It does not ship a closed list.

## Flow

1. The model proposes hypotheses and, with them, which registered tools and endpoints would help, with its reasoning.
2. Code validates each proposal: the tool exists, the candidate representation fits, the output unit matches the
   declaration, the candidate is in domain. A proposal that fails is dropped and the reason is shown, as for hypotheses.
3. Plan review shows predicted value, interval and in-domain status beside each candidate, labelled **prediction**.
   A person can remove a tool or add one.
4. The approved choices, tool versions and outputs are sealed. `proef campaign reproduce` replays them and does not
   call the tools or the model again.
5. Acquisition may use the predictions to choose what goes to the lab (`acquisition.py` already takes declared
   posteriors and refuses out-of-domain picks both ways).
6. The measured result is judged by the fixed rule. When prediction and measurement disagree, the disagreement is
   recorded.

## Does the hypothesis code vary by domain?

It should not fork per domain. Most of it is already generic: a statement, a candidate, a mechanism, a "refuted if"
clause, a confidence and a citation. Domain knowledge leaks into code in three places, and each should become data:

- **Assay-to-metric mapping.** `hypothesis_agent.py` decides that binding, enzymatic, cytotoxicity and counterscreen
  assays produce `ic50_um`, and treats everything else as a property read in the program's metric. That list belongs in
  the modality pack.
- **Prompt wording.** The domain examples in the proposal prompt come from the pack, so a materials program is not
  prompted with kinase examples.
- **Candidate representation.** SMILES, a sequence or a composition. The registry's `input` field handles it.

A domain pack is then data: assay types and their metrics, safety rule names, prompt examples, default tools. A new
domain is a new pack, not a code change.

Status: the first three are done (S-T8 Phase 1). The assay-to-metric rule, prompt wording, candidate kind and default assay
are fields of `ModalityPack` in `src/proef/modality.py`. Default tools are still to come with the registry.

## Guarantees that do not change

- The gate is the program's. A tool's output never becomes the pre-registered criterion.
- A prediction is recorded as a prediction, with its source and version.
- No tool call or model call is made on replay.
- An out-of-domain candidate is refused or flagged, never explored.
- A tool with no uncertainty can inform ranking but cannot move a candidate past a gate.

## Open questions

- How strongly should the plan rank by prediction when the surrogate is uncalibrated? The CS04 replay showed greedy
  ranking ties EI on a strong surrogate, so the first version should show predictions and not rank on them.
- Which reference tools are worth shipping beyond what exists?
- How are tool versions pinned so a replay stays honest after an upgrade?

## Build order

1. Registry contract and declaration (`core/contracts`), one reference tool, ledger record for a prediction.
2. Validation step in `build_plan`, plus the "prediction" column and in-domain badge in plan review.
3. Move the assay-to-metric mapping and prompt examples into the modality pack.
4. Model-proposed tool choice, validated and shown for approval.
5. Prediction versus measurement view on the finished campaign.
6. A real-data check on a case study before anything ranks on predictions.
