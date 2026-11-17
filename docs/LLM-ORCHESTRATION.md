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

#### Independent review of Phases 0 to 2 (done, `/code-review high`, before Phase 3)
A reviewer with no stake in the code found nine problems, two of them regressions this work introduced. All are fixed, each
with a test (mutation-checked unless noted):
1. The lab check returned `cannot_run` for `custom` and `plate_reader`, which no capability list can declare. Every
   materials or property campaign with agent hypotheses would have run nothing. Generic assays are no longer checked
   against a capability list. Found by the reviewer, not by the author's own re-read.
2. The check hard-coded a biosafety ceiling of 2 and ignored the campaign's `max_bsl`, and blamed the assay for what was a
   BSL problem. It now uses the campaign's ceiling and names the real cause.
3. `request_analysis` judged raw wells: no interval (a point estimate was classified as certain), no check that the run was
   complete, and a model could re-judge a hypothesis by resubmitting it. Now: a Hill fit or replicate readings with an
   interval, abstention when none can be made, completed runs only, one verdict per hypothesis.
4. Shared measurement judged a variant from another variant's run, dropping its own designed test. A variant whose designed
   test differs now waits for its own run (`shared_measurement_declined`).
5. A rebuild from the ledger (`resume`) restored the placeholder criterion, so a plate submitted later abstained. The gate
   assigned to each hypothesis is now a record (`criteria_assigned`) and `resume` applies it.
6. The instrument menu was built for every campaign, so a lab whose capabilities were not ready stopped runs that never
   used it. Built only for the agent path, and a failure there is reported, not fatal.
7. A malformed `PROEF_MCP_GATE` crashed the MCP server. Now treated as unset.
8. An MCP call that raised left no ledger record, unlike the toolkit. Now recorded.
9. `in_domain` and `interval` from a tool were copied unvalidated: a string could break plan review for the whole plan.
   Now validated, and a tool returning the wrong type is a status.

Lesson for later phases: the author's own re-read found two holes in this code and the independent one found nine, including
the most serious. Keep an independent review at the end of every phase from here on.

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

Finding at the start of Phase 2: the engine selects ONE lab per campaign, at the start, before any hypothesis exists. A
per-hypothesis instrument pick therefore cannot route a run to a different instrument. Multi-instrument routing is the
open S-T5 item and is not part of this phase. Phase 2 validates the pick, states where the run will really go, and blocks
what the campaign's lab cannot serve.

**2A: instruments (done)**
- [x] The menu is the operator's pool as data (`labselect.build_menu`, `menu_text`), labelled simulated, real or
      hardware-validated. One source for the prompt and the validator.
- [x] The model names an instrument per variant, or leaves it empty. The prompt lists the menu only when there is one, so
      prompts without a pool are unchanged. A re-plan is shown the same menu.
- [x] `check_pick` validates on facts and never reads the reasoning: in the pool, declares the assay, BSL, is where the
      campaign runs. Statuses: ok, unstated, unverified, invented, lacks, other_instrument, cannot_run. A bad pick is
      reported, never fixed silently.
- [x] The check rides in the sealed proposal record (`lab_check`) and in the plan, per variant.
- [x] Plan review shows "Runs on" for each hypothesis, the model's pick when it differs, and "Will not be submitted" for
      what the campaign's lab cannot serve.
- [x] The submit loop refuses a `cannot_run` hypothesis and records `lab_cannot_serve`. It never reaches the lab.
- [x] Replay carries the recorded check, so it does not submit what the original refused to.

**2B: prediction tools (done)**
- [x] Registry contract (`src/proef/predictors.py`): endpoints with units, input kind, modalities, uncertainty, version.
      Tools are registered, not built in. The operator enables the ones a campaign may use (`prediction_tools`), the same
      kind of fence as the instrument pool.
- [x] The model proposes tools per variant (and the candidate's SMILES). Code validates on facts (registered and enabled,
      applies to the modality, input present and valid) and RUNS them. Failures are statuses, never fatal.
- [x] Results keep only declared endpoints with declared units and finite values. Each says `kind: prediction`,
      `evidence: false`, carries the tool version and the input it used, and is marked uncalibrated when the tool reports
      no uncertainty.
- [x] Sealed in the proposal record (`predictions`) and shown in plan review as a prior, with the structure so a person
      can check it is the compound named.
- [x] Reference tool: `admet-physchem` (molecular weight, logP, TPSA, Lipinski). It is not a potency model and says nothing
      about the gate.
- [x] `prediction_tools` is carried by `build_cfg` and the CLI (the `replan` and `modality` lesson).

#### Phase 2B hole review
1. A prediction must never become a criterion or a verdict. Tested: a tool reporting a value far below the gate leaves the
   criterion at the program's gate and the status `proposed`.
2. The model's SMILES is its claim about the candidate. It is kept with the prediction and shown to the person, and it is
   NOT written onto the hypothesis, where structure-keyed logic (series, applicability domain) would trust it.
3. A tool the operator did not enable cannot run even if registered, and a materials program is not offered a
   small-molecule tool.
4. Free text in the SMILES field (prompt-injection style) is rejected by the input check and truncated in the record.
5. Provenance gap found while reviewing: the first version did not record the input, so a prediction could not be audited.
   Now recorded.
6. Replay uses the recorded predictions and needs no tool. Tested by making any tool call raise during a replay.
7. Third-party tools run in-process with this process's access. Fixed to fail closed: a tool must be marked `trusted` to run,
   only the reference tool is, and any other is refused (`untrusted_tool`) unless the operator sets
   `PROEF_ALLOW_UNSANDBOXED_TOOLS=1`. Real isolation still needs the sandbox lanes (S-T3 1d/1e).
8. A hanging tool could freeze plan review. Each call now has a time limit and is abandoned on timeout (status `error`).
9. A flood of output keys, a long model-supplied tool list, or a long tool name cost nothing and are bounded (declared
   endpoints only, at most 8 tools per variant, names at most 80 characters).
10. Predictions must never rank anything. A test now fails if any module outside the allowed few reads `predictions`, and
    another shows selection is identical whatever the predictions say. Using them for acquisition needs an
    applicability-domain and calibration review first.
11. Found re-reading Phases 0 to 2 together: the toolkit and MCP built a hypothesis's criterion without the modality, so a
    materials program with a model naming a chemistry assay would have got an IC50 criterion. Both now take the modality
    (`PROEF_MCP_MODALITY` for MCP).
12. Found in the same read: a model returning a list or dict where an instrument name belongs would have crashed the
    campaign, and a long string was stored unbounded in the sealed record. Coerced and capped at 80 characters.
13. Known and unchanged: the reference tool reports no uncertainty, so it cannot rank, and the registry is process-global.

### Phase 3: the model drives discretionary decisions inside the current loop

Findings at the start of Phase 3 (checked in the code):
- A follow-up today is a person's choice on an abstention (`human_review`: follow up, accept, drop) and a rule-based "what
  next" (`NextStep`). No model proposes one. The model already proposes new hypotheses after a round (re-plan), reviewed by
  a person.
- Replicates are a lab property (`replicates=3` in the design and the simulator), not something a protocol can ask for.
  `ProtocolSubmission.constraints` is a free dict, so a request can ride there without a contract change.
- Halt and pause are enforced at the lab gateway only. A halted or paused campaign still calls the model (proposals, the
  critic, the reviewer), and nothing caps model calls. `ResourceBudget` counts lab runs and hypotheses only.

Split:

**3A: governed model calls (first, because 3B and 3C add model calls)**
- [x] Every model call in a campaign goes through a guard: halt refuses, a campaign or program pause holds until resumed,
      a `max_model_calls` budget refuses when exhausted. Each call is a ledger record (role, count), each refusal too.
- [x] A refused critic or reviewer is skipped and recorded, a refused proposal fails the campaign loudly, a refused
      re-plan continues with the plan it has.
- [x] The cap is the program's (`max_model_calls`, default 200, shown in the program limits) and a research tree shares it
      across its sub-problems like lab runs. Carried by `campaign_doc`, `build_cfg` and the CLI.

**3B: model-proposed follow-ups on abstentions (done)**
- [x] A validated suggestion shape: kind, action, subject, parameters, the model's rationale (shown, never trusted) and
      `validation {ok, status, reasons}`, the same form as the lab check and the predictions (`followup.py`).
- [x] After each round the model suggests, per abstained hypothesis, one of a closed set: follow up, accept, drop. Code
      validates: the action, that the hypothesis is one of the abstained ones listed, replicates between 3 and 12, a ladder
      of 3 to 8 doses that brackets the hypothesis's own gate (in the assay's metric), a lab run left in the budget (each
      valid follow-up spends one), one proposal per hypothesis and one follow-up per candidate.
- [x] Sealed as `followup_proposal` records. The abstention card in the Decisions page and the campaign drawer shows
      "Model suggestion, not a decision", what it proposes, whether it ran, and why one failed its checks.
- [x] Off by default (`followups`), opt-in in the launch dialog, carried by `campaign_doc`, `build_cfg` and the CLI.

**3C: executing a validated follow-up (done)**
- [x] Only a valid follow-up runs. Accept and drop are a person's recorded decision and the engine never writes one (tested:
      no `human_review` record from a campaign).
- [x] Under `act` it becomes a hypothesis for the next round with its own designed ladder and replicate request, judged by
      the program's gate. Under `gate` it goes through plan review labelled "follow-ups after round N", with the
      already-tested warning, and a decline stops it. Under `observe`, and in the last round, it is a suggestion only.
- [x] The replicate request travels in `ProtocolSubmission.constraints`. A lab honors it only if it declares support
      (the simulator does). Otherwise the follow-up runs with the ladder, the validation says so, and the plan shows it.
- [x] The toolkit's tool list follows the autonomy level: under `observe` the lab tools are not offered and not callable.
- [x] Replay: a campaign that ran follow-ups reports "not reproducible", the same as a re-plan. Replaying them would need the
      recorded proposals to rebuild a second hypothesis on the same candidate, which the replay's candidate-keyed rebuild
      cannot represent yet. Recorded as a known limit.

#### Phase 3 hole review (author's pass, before the independent review)
1. A model naming a hypothesis that is not abstained, or inventing an id: rejected and recorded.
2. A ladder that does not bracket the gate, replicates out of bounds or not a whole number: rejected.
3. Two follow-ups validated against the same last run: the second is `no_budget`.
4. The proposal record's `kind` was overwritten by the proposal's own kind, so the card and replay could not find it.
   Found by the first end-to-end test. Fixed (`proposal_kind` carries the other).
5. Validation checked the program-level metric name, so a dose-response ladder was never checked against the gate in the
   assay's metric. Found by the same test. It now uses the hypothesis's own criterion.
6. Model calls for follow-ups pass the 3A guard, so halt, pause and the call budget apply. Tested: a refused call leaves the
   abstentions to a person and the campaign finishes.
7. Known and open: replay of follow-ups (above), and follow-ups are suggested for abstained hypotheses only, not for a
   preliminary claim that needs replicates.

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
