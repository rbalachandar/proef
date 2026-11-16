# LLM orchestration: phased plan

Status: plan. Nothing here is built. Roadmap item S-T8. Builds on [`DOMAIN-TOOLS.md`](DOMAIN-TOOLS.md) (S-T7).

## Goal

Let a model drive the discretionary parts of a campaign through tools (which hypotheses to pursue, which instrument and
tools to use, whether to re-plan, how to follow up an abstention), while the guarantees stay in code:

- a person approves the plan before anything reaches the lab, and the gates apply even then
- the gate is registered before any data and is never the model's
- a verdict comes only from the fixed rule on measured data
- every step is on the ledger and a run replays without calling the model again

The test for every phase: **can a model, by any sequence of tool calls, skip a checkpoint, move the gate, or produce a
verdict?** If yes, the phase is not done.

## What exists today (checked in the code)

- The campaign loop is code: `autoloop.run_campaign`. The model proposes hypotheses, the dose ladder and re-plans. Code
  validates, executes and judges.
- `toolkit.AgentToolkit` has 8 tools for an LLM loop. **Nothing in `src/` drives it** (only tests construct it). The MCP
  server (`mcp_server.py`) exposes 4 tools to external clients.
- Holes in that toolkit that must close before a model gets near it:
  - `record_verdict` lets the model write any verdict with free-text evidence to the ledger. It is labelled as not
    tool-verified, but it contradicts "the model never decides a verdict".
  - `propose_hypothesis` records a hypothesis without the program's gate (`_assign_criteria` is not applied), so its
    verdict would not be judged by the registered rule.
  - `submit_assay` goes through the safety gateway but has no plan-approval precondition and a fixed protocol.
- Lab selection is code (`labselect.py`), inside the operator's fence. Pause and halt hold or refuse lab submissions
  (`LabGateway`), not other tool calls.

## Phases

Each phase ends with: tests green, OpenAPI and TS types regenerated if schemas changed, golden snapshot reviewed,
README and diagrams updated for what changed, and a ROADMAP box ticked. Phases are ordered by dependency.

### Phase 0: close the holes (no new capability)
- [x] Remove `record_verdict` from the toolkit. The model can call `request_analysis(candidate, run_id)`. The engine
      applies the registered criteria to the measured data and records the verdict. Refused without a registered gate or
      for a run that is not the candidate's own.
- [x] `propose_hypothesis` (toolkit and MCP) takes the criterion from the program's gate through one shared helper
      (`autoloop.criteria_for_assay`), never from the model's number. No gate registered means the proposal is refused.
- [x] `submit_assay` refuses candidates no plan approved, bounds the dose ladder (`sanitize_protocol`), refuses a gateway
      that has no governance bundle, and records `covered_by_plan` on success. Halt still stops it.
- [x] Every toolkit and MCP call is a `tool_invocation` ledger record (tool, argument hash, result hash, status, refused).
- [x] Adversarial tests in `tests/test_toolkit_guarantees.py` (16 tests). Three guards were mutation-checked: removing
      each one fails its tests.
- [x] `tool_call` records render in the diary and the campaign activity feed (kind `tool`).
- [x] `HypothesisTracker.propose` no longer invents `ic50 <= 0.0`. With no gate and no prediction the criterion is a
      recorded placeholder, a verdict on it abstains, and the proposal record states its `criteria_source` (explicit,
      prediction, placeholder; the program's gate replaces it in memory). Raising an error was tried and broke the
      portfolio flows that propose before the gate is assigned.
- [x] MCP `run_assay` needs candidates a person approved (`PROEF_MCP_APPROVED`) and bounds the ladder.

#### Phase 0 hole review (what was found, and how)

Found by reading the toolkit as an adversary, then by writing each attack as a test:
1. `record_verdict` let a model write any verdict to the ledger.
2. `propose_hypothesis` recorded a criterion of `ic50_um <= 0.0` when no value was predicted, so any measurement would
   have been judged a refutation, and with a predicted value the model's number became the gate. Same flaw in MCP.
3. `submit_assay` had no plan-approval precondition and accepted any dose ladder.
4. A toolkit could be built on a gateway with no governance, so no halt would apply. Found while writing the halt test.
5. MCP tool calls were not recorded, and its lab was not labelled simulated.
6. Caught by the existing tests, not by an attack: wrapping the executors for the ledger initially broke positional
   calls. A reminder to run the old suite after every guard.

7. Found while closing the open items: the protein-design adapter built an ipTM criterion and never passed it.
8. Found by the full suite after the guards went in: two older tests (golden campaign, MCP halt) depended on the
   old behaviour. The golden diff was only the new `criteria_source` key.

Open follow-up: a second reader for the toolkit before Phase 3 gives a model more tools.

### Phase 1: domain packs as data
- [x] The dose-response assay list has ONE definition (`modality.DOSE_RESPONSE`). The hypothesis agent, the plan, the
      toolkit, MCP, the campaign loop and the simulator all read it through the pack (`metric_for_assay`,
      `criteria_for_assay(assay, gate, modality)`). The agent's own hardcoded copy is gone.
- [x] Prompt wording comes from the pack: role, mechanism hint, candidate kind and example. The default pack renders the
      exact text used before, so recorded prompts and the golden snapshot did not change.
- [x] Candidate representation is declared by the pack (`candidate_kind`: compound, sequence, composition or sample).
- [x] The assay a hand-listed candidate is tested by (`default_assay`) moved from `launch.py` into the pack.
- [x] Materials and biologic packs carry their own data. `tests/test_domain_packs.py` (12 tests) shows a hypothesis in
      each domain gets the right metric and the program's gate, and that prompts carry no drug-discovery wording for a
      materials program. Four guards mutation-checked.
- [x] `CampaignConfig.modality`, carried by `build_cfg` and the CLI (the `replan` lesson: a new key has to be carried on
      purpose).

#### Phase 1 hole review
1. An unknown modality fell back to small molecule silently. A typo such as "materials" gave a materials program
   drug-discovery wording and dose-response rules. The API now refuses it (422 with the valid list), stores the pack's
   own name, and the engine refuses it too.
2. The workbench hardcodes the modality options. A test now fails if the list differs from the packs.
3. `launch.py` had the same domain rule as the agent (small molecule means binding, else custom), a second leak. Moved.
4. `build_cfg` would have dropped `modality`, as it did `replan`. Carried and tested.
5. Pack text is code, not user input, so it is not an injection path. Re-check when packs become user-supplied.
6. Fixed afterwards: biologic and agrochemical candidate lists defaulted to the `custom` property read, against the packs'
   own notes (biologic runs are binding dose-responses, agrochemical uses the standard dose-response engine). They now
   default to `binding` and `cytotoxicity`.
7. Found when fixing the duplicate-compound case: the campaign loop submitted one run per hypothesis, so a compound
   proposed under two families was run twice and the second plate was never read. Now one measurement per
   (candidate, assay), recorded as `shared_measurement`.

### Phase 2: menu-based choice of instrument and tools
- [ ] Registry contract for prediction tools (see DOMAIN-TOOLS): endpoint, unit, input, modality, domain, uncertainty,
      version.
- [ ] Build the menu from the operator's pool (`labselect.build_pool`) and the registry. One source for the menu and for
      the validator.
- [ ] The model picks per hypothesis from the menu, with a reason, or says nothing fits.
- [ ] Validation in code: in the pool, capabilities cover the assay, biosafety level, in-domain, simulated versus
      validated flagged. A failed pick is shown with its reason and never silently fixed.
- [ ] Plan review shows pick, validation result and predictions (labelled as priors). "No instrument in the fence can
      serve this" is its own state.
- [ ] The approved picks are sealed. Replay uses them.

### Phase 3: the model drives discretionary decisions inside the current loop
- [ ] Which abstentions to follow up, what to re-plan, how many replicates to request within bounds. Each is a
      proposal with a validation result, shown to the person under the same plan-review component.
- [ ] Autonomy levels apply: `observe` exposes no submit tools, `gate` needs approval per proposal, `act` runs inside
      the gates.
- [ ] Budgets cover model calls and tool calls (`ResourceBudget`), and a campaign pause or global halt stops tool calls
      too, not only lab submissions.

### Phase 4: tool-calling orchestrator inside a state machine
- [ ] The engine becomes an explicit state machine (plan, review, gates, run, analyze, verdict, decide). The model
      chooses among the actions the current state allows. Preconditions are enforced in code, not in the prompt.
- [ ] Replay: the recorded tool-call sequence is replayed, and the model is not asked again.
- [ ] A kill switch and wall-clock bound the model cannot reach.

### Phase 5: evidence it helps
- [ ] Eval tasks comparing the model-driven order with the fixed loop (hits per lab run, abstention handling, cost).
- [ ] A real-data case study before any default changes. The model-driven mode stays opt-in until it wins.
- [ ] Count checkpoint violations across all eval runs. The number must be zero.

## Connections across phases (check these every time)

| Concern | Where it bites | What to do |
|---|---|---|
| **Replay** | every phase that adds a model decision | seal the decision as a record AND teach `reproduce.py` to replay it, or the phase is unfinished |
| **One proposal shape** | phases 2 and 3 add picks, predictions, follow-ups | one `Proposal` schema (source, validated, reasons) and one plan-review component, not three |
| **Menu and validator drift** | phase 2 | both read `build_pool` and the registry. A test asserts a pick outside the menu is rejected |
| **Verdict reading** | phases 0, 3, 4 add record types | new records must not change verdict derivation. Add a test per record type, payload-driven and recency-wins |
| **Gate assignment** | phase 0 and every new way to create a hypothesis | all paths go through `_assign_criteria`. A test creates a hypothesis each way and asserts the recorded threshold |
| **Pause and halt** | phases 3 and 4 | non-lab tools must hold or refuse too. Test: halt mid-run, assert no further tool call executes |
| **Simulated labelling** | phase 2 menu | the menu and the plan show simulated versus real. The model may not prefer simulated silently |
| **Autonomy levels** | phases 0, 3, 4 | the tool list exposed to the model depends on the level. `observe` must expose no submit tool |
| **Schemas and clients** | any API change | export OpenAPI, run `npm run gen:api`, update tests, review the golden snapshot diff |
| **Ledger views** | new record types | diary, activity feed, `proef prove`, `verify_chain` and the record tab must render them |
| **Budgets** | phases 3 and 4 | model-call and tool-call counts enter `ResourceBudget` and the plan limits |
| **Docs and diagrams** | end of each phase | update README, architecture, flows, and `scripts/render_diagrams.py` |
| **CI quota** | all | new tests run offline and fast. Live-model checks are manual |
| **Existing agents** | phases 3, 4 | critic, reviewer, AutoAnalysisAgent keep their roles and stay advisory |

## End-of-phase hole review (required)

Phase 0 started because a read of the toolkit found three ways around the guarantees (`record_verdict`, no gate on
`propose_hypothesis`, no approval on `submit_assay`) in code that tests had passed. Passing tests did not mean the
guarantees held, because no test tried to break them. So every phase ends with a review whose only job is to find holes:

1. List every new way a model, a tool or an API caller can reach the ledger, the gate, the lab or a verdict.
2. For each, write the attack as a test (skip approval, change the gate, write a verdict, act after a halt, act while
   paused, replay with a different model) and see it fail closed. A test that passes without the guard is not a test.
3. Read the new code as an adversary, not as its author. Prefer a second reader or a fresh session.
4. Re-run the earlier phases' adversarial evals.
5. Record what was found and fixed in the ROADMAP under the phase.

## Risks

- A model that is good at sounding convincing can pass a validation that only checks form. Validators must check
  facts (in the pool, in domain), not wording.
- More model calls means more variance. Keep the fixed loop as the default and the comparison baseline.
- The prediction-versus-measurement disagreement is useful signal. Do not hide it to make the model look better.

## Order of work

0, 1, 2, 3, 4, 5. Phase 0 can start now and is small. Phase 1 is independent of 0 and can run in parallel, but Phase 2
needs 1 and the registry. Phase 4 needs 0, 2 and 3.
