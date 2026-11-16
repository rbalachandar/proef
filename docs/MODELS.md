# Language models in Proef

A model is optional. Proef runs an experiment loop (design, synthesise, run, read the plate, fit, judge against the
gate, confirm with an orthogonal assay) the same way however a campaign starts. What differs is **who proposes the
hypotheses**. The **New campaign** dialog offers three ways, chosen per campaign:

| | **Start research** | **Candidate list** | **Manual plate run** |
|---|---|---|---|
| Hypotheses come from | a language model, from the problem statement | your list | your list |
| Plan review | yes, in "Ask me first" mode | not needed | not needed |
| Design, run, fit, judge, confirm | the agent (code) | the agent (code) | you submit the CSV; code judges it |
| Review | rule-based reviewer **plus** an LLM reviewer (advisory flags only) | rule-based reviewer | rule-based reviewer |
| Needs | a configured model | nothing: no key, works offline | nothing |
| Replay | `proef campaign reproduce <branch>` replays the plan you approved (without what you removed), in the same batches and order, gated or not; the model is **not** re-asked ("reproduced: the execution path, not the model's creative step"), so a *fresh* run may propose different hypotheses | `proef campaign reproduce <branch>` replays it exactly | n/a |
| Campaign header | `LLM · <model> wrote N hypotheses · reviewed` | `no LLM` | `no LLM` |
| CLI | `proef campaign auto examples/campaign_llm.json` | `proef campaign auto examples/campaign.json` | n/a |

"Autonomy" is a separate setting that applies to the first two: how much happens without a person saying yes.
**Ask me first** (you approve the model's plan, which also covers its lab submissions; with your own candidate list each submission asks), **Preview only** (plan and explain, submit nothing),
or **Run on its own** within the safety gate (no approvals, so no plan review).

## The plan review

In **Start research** with *Ask me first*, the campaign pauses after the model proposes and shows you the plan: each
family's claim, mechanism, the one observation that would refute it, the model's confidence, whether its citation
resolved, and every variant with its prediction. You can **remove individual hypotheses**, **decline the plan**, or
approve what is left, with an optional note. Removed hypotheses are never tested; they are listed under "Eliminated
before testing" with the reason, and your decision is sealed in the ledger under your name. Removing everything counts
as declining. A declined plan submits nothing to the lab and the campaign reads "plan declined".

Two more things appear in the plan: the **dose ladder** the model designed for each variant (3 to 8 concentrations,
0.001 to 1000 µM, cleaned and de-duplicated; anything unusable falls back to the campaign default, and replicates stay a
property of the lab), and the **critic's notes**.

## The critic: a second agent

Before you see the plan, a second model reads it and raises concerns (near-duplicate hypotheses, a claim the gate's metric
cannot test, a "refuted if" nothing could observe, an unearned confidence). It is **advisory by construction**: it cannot
remove a hypothesis, change a gate or approve anything, and if it fails the plan is shown without notes. Its notes and the
model that wrote them are recorded (`plan_critique`). It uses the main model unless you give it its own, which is better,
because two copies of one model share blind spots:

```sh
export PROEF_LLM_CRITIC_MODEL=another-model     # also _API, _BASE_URL, _TOKEN, _PRESET, _SESSION_HEADER; each falls back to the main setting
```

## Research trees

**Split into sub-problems** asks the model to split the objective, recursively. The program's **Split depth** (1 to 3) and
**Sub-problems** (2 to 16) limits cap the tree and the server re-checks them whatever the browser sends; at most 4 children per
node. You keep the real ones and edit each gate before starting. Starting seals your decision (`research_tree`), shares the
program's lab runs down the tree by priority (a leaf never spends more than its slice), and runs one campaign per leaf. The
program page shows the tree and rolls it up each time it is read: a parent is achieved only when its required children are,
and abstentions count as undecided, never as failure.

## Replay

`proef campaign reproduce <branch>` rebuilds a campaign from its ledger and compares verdicts one by one. It reuses the
recorded hypotheses, their families, the scores the original run selected on, the approval mode and the number of rounds,
and it leaves out anything removed at plan review; a declined plan reports `not-reproducible: nothing was run`. Replay runs
on a clean, in-memory flywheel, so it neither depends on nor changes your real one. Campaigns recorded before the number of
rounds was captured replay as a single round.

## Limits

A program sets hard caps that every campaign inherits: rounds, lab runs, hypotheses, families, variants per family, and
hypotheses taken to the lab per round, how deep a split may go and how many sub-problems a tree may have. A campaign may ask for less, never more; exhausting a cap is recorded, not
silent. Programs created before limits existed get the defaults (3 rounds, 20 lab runs, 12 hypotheses, 3 × 3, 3 per round).

## What the model does in Start research

Three jobs, and it **never decides a verdict**.

| Job | When it runs | Providers | What is recorded |
|---|---|---|---|
| **Propose hypotheses** | a campaign with `"hypothesis_source": "agent"` | any | the model on every hypothesis (`predicted_by`), plus the prompt and response, sealed as a `tool_invocation` |
| **Critique the plan** | before you review it (the critic) | any | `plan_critique`: the critic model and its notes (advisory) |
| **Review the verdicts** | automatically, when a model took part in the campaign | any | `review_report_llm`: model, prompt hash, advisory flags (flags only, schema-enforced) |

Other features that use a model have their own transport and speak the **Anthropic Messages API only** today:
the LLM acquisition policy (`AnthropicLLMPolicy`, used by the benchmarks) and the vision readout
(`proef.vision`). The optional advisory toxicity pass (`PROEF_ADVISORY_LLM=1`) uses whatever provider you
configure below.

## Configure a provider

Everything comes from the environment. The key is never a command-line flag.

| Variable | Meaning |
|---|---|
| `PROEF_LLM_MODEL` | model name |
| `PROEF_LLM_TOKEN` | API key (not needed for a local server) |
| `PROEF_LLM_BASE_URL` | endpoint base, e.g. `https://api.anthropic.com` or `https://host/v1` |
| `PROEF_LLM_API` | `anthropic` (default, Messages API) · `openai` (`/chat/completions`) · `openai-responses` (`/responses`) |
| `PROEF_LLM_SESSION_HEADER` | optional header carrying a stable session id, for gateways that require one |
| `PROEF_LLM_PRESET` | shortcut for a known gateway; explicit settings above always win |

The old `BIONEXUS_LLM_*` names still work.

```sh
# Anthropic
export PROEF_LLM_MODEL=<model> PROEF_LLM_TOKEN=<key>

# Any OpenAI-compatible endpoint (OpenRouter, vLLM, a hosted gateway, ...)
export PROEF_LLM_API=openai PROEF_LLM_BASE_URL=https://host/v1 PROEF_LLM_MODEL=<model> PROEF_LLM_TOKEN=<key>

# A local server — no key
export PROEF_LLM_API=openai PROEF_LLM_BASE_URL=http://localhost:11434/v1 PROEF_LLM_MODEL=llama3

# OpenCode Go: one preset, and your key stays in OPENCODE_API_KEY
export PROEF_LLM_PRESET=opencode-go PROEF_LLM_MODEL=gpt-6-luna
```

**The `opencode-go` preset** fills in the endpoint, the `x-opencode-session` header and the key variable, and
picks the right API for the model: GPT and Grok models are served by the Responses API, the others by chat
completions. The provider also sends its own `User-Agent` (some gateways refuse generic HTTP-library agents)
and backs off on `429` and `5xx`.

There is **no offline stub**. With no usable model, a candidate-less launch is refused:

```
agent hypothesis source needs a model: no model configured: set PROEF_LLM_MODEL and PROEF_LLM_TOKEN ...
```

and the workbench returns the same message as a `409` instead of starting a campaign that cannot work.

## Run each mode

**Candidate list**: [`examples/campaign.json`](../examples/campaign.json) lists its candidates; no key, no network model:

```sh
proef campaign auto examples/campaign.json --yes
proef campaign reproduce <branch>        # replays it and diffs the verdicts (works for LLM runs too: it replays the recorded hypotheses)
```

**Start research**: [`examples/campaign_llm.json`](../examples/campaign_llm.json) has no candidates; the model proposes them:

```sh
export PROEF_LLM_PRESET=opencode-go PROEF_LLM_MODEL=gpt-6-luna
proef campaign auto examples/campaign_llm.json --yes
```

A real run (the lab is **simulated**, so the numbers say nothing about real EGFR chemistry):

```
family 'reversible_hinge_atp' (confidence 0.82): Gefitinib-like 4-anilinoquinazolines bind wild-type EGFR ...
family 'c797_covalent_atp' (confidence 0.72): Cys797-directed acrylamide inhibitors bind the EGFR ATP pocket ...
citation check: 10.1016/S0092-8674(02)00942-6=invalid, 10.1158/2159-8290.CD-14-0337=verified
WARNING: 1 fabricated/unresolvable citation(s) penalized in portfolio scoring
agent proposed 4 variant hypotheses
round 1: testing ['COV-C797-OSI-01', 'REVQ-GEF-01'] (2 candidates remain)
[agent] hypothesis ... (REVQ-GEF-01): VALIDATED (preliminary, pending orthogonal confirmation), IC50 7.079 uM ...
[agent] gate honesty: COV-C797-OSI-01 methods agree but a borderline CI straddles threshold 20.0 -> inconclusive
```

In the workbench, open a program and choose **Start research** (the server needs the variables above in its
environment; without them the option is disabled and says why).

## What keeps a model honest

- **Verdicts are not the model's.** Its hypotheses are judged by the same fixed rule, against criteria
  registered before any data, with the same interval and abstention logic as everyone else's.
- **Output is validated.** The reply must be schema-valid JSON, within operator limits (`max_families`,
  `max_variants_per_family`), or it is rejected.
- **Citations are checked.** DOIs the model cites are resolved. In the run above, one was unresolvable and
  was flagged and penalised in scoring, not trusted.
- **Everything is on the record.** Model, prompt, response and prompt hash are sealed in the hash-chained
  ledger, so a result can be traced back to what the model was asked and what it said.
- **No silent fallback.** Unconfigured means an error, never a canned answer.

## Troubleshooting

| Symptom | Cause |
|---|---|
| `403 Forbidden` | the gateway refuses generic HTTP-library User-Agents; use the provider in this repo (it sends its own) |
| `400 MissingSessionID` | the gateway needs a session header: set `PROEF_LLM_SESSION_HEADER`, or use a preset |
| a GPT-family model is rejected on `/chat/completions` | some gateways (OpenCode Go does) serve GPT and Grok models only through `/responses`: use `PROEF_LLM_API=openai-responses`, or the preset, which chooses per model |
| `no model configured` | one of `PROEF_LLM_MODEL` / `PROEF_LLM_TOKEN` / `PROEF_LLM_BASE_URL` is missing |
| header says `no LLM` on a campaign you expected to use a model | the campaign had candidates, so the model was never asked, or the server was started before the variables were set |
