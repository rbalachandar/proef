# Workbench redesign — spec

Status: approved scope (Nov 12, 2026) · first flow: **Campaign cockpit**.
Decisions: new `/api/v1` layer over the unchanged engine; React + Vite + TS
with Tailwind + Radix primitives + visx charts; a real end-to-end tool, not a
demo build; no static demo mode.

## 1. Who and what for

A bench or computational scientist running real campaigns. Five jobs:

| Job | Question | Surface |
|---|---|---|
| Trust the data | "Is this plate any good, and what does it say?" | **Analyze** |
| Decide what's next | "What do we test next, and why?" | **Campaign** (why-this-plate) |
| Respond | "What needs me right now?" | **Decisions** |
| Defend | "Can I stand behind this result?" | **Evidence** |
| Steer | "Are we winning against the gate?" | **Programs** |

Principles: uncertainty is the main visual idea (every estimate is drawn with
its interval against the gate); abstention is a first-class, dignified state,
never an error; the system explains itself (every pick, refusal and verdict has
a "why" one click away); nothing happens without a record.

## 2. Information architecture

```
Programs ─ program cards (progress vs gate, needs-you count, changes since last visit)
Decisions ─ approvals · abstentions to review · QC failures · refusals   (decide inline)
Campaigns ─ list ─ Campaign cockpit (see §3)
Analyze ─ drop file → detect → confirm → results → "make it a verdict" → export
Evidence ─ provenance chain · Verify · anchor status · report export
Lab ─ instruments & fence · simulated/real badges · halt switch
```

Global chrome: left rail (the six sections), top bar with the **Halt** button,
a ledger indicator ("● ledger verified", red and loud if it fails; record count and anchor state in its tooltip and on Evidence), ⌘K command
palette, light/dark theme.

## 3. Campaign cockpit (first flow)

```
┌ objective · status chip · autonomy · gate "IC50 ≤ 2 µM" · budget 7/20 runs · chain ✓ ┐
│ FUNNEL  proposed 5 → selected 3 → tested 3 → judged 3  (validated 1 · refuted 2 · abstained 0)
├───────────────────────────────┬────────────────────────────────────────────────────┤
│ ROUNDS (timeline)             │ HYPOTHESES (table, sortable)                       │
│  ● Round 1  H1 H2 AD1         │  cand │ verdict │ estimate ±CI vs gate (interval   │
│    why this plate ▸           │       │         │ plot, gate as a vertical line)   │
│  ● Round 2  H3 M1             │  ...                                               │
│    why this plate ▸           │  row click → side drawer: statement, mechanism,    │
│  ○ Round 3  (planned)         │  refuted-if clause, curve, evidence run, derivation│
├───────────────────────────────┴────────────────────────────────────────────────────┤
│ ACTIVITY  live stream: "run sim-42-003 submitted" · "H1 VALIDATED (preliminary)" …  │
└────────────────────────────────────────────────────────────────────────────────────┘
```

**Why-this-plate drawer** (per round): the policy and seed; each pick with its
reason (EI arithmetic when acquisition is on, portfolio score otherwise); each
refusal with the gate that refused it (AD veto, diversity, budget, safety).

**States are never colour-only:** validated ✓, refuted ✕, abstained ◐
(interval straddles the gate), refused ⊘, in lab ◷, proposed ·.

Actions: Approve/Decline pending submissions (inline), Halt, re-run review,
export report, open Evidence, archive.

## 4. Design system

Tailwind tokens + Radix primitives (Dialog, Popover, Tooltip, Tabs, Select,
Dropdown, Command) styled as a small in-repo component set. Type: Inter for
text, JetBrains Mono (tabular figures) for numbers and ids. Neutral ink with
one accent; semantic verdict palette validated for colour-blind safety.
visx for the interval-vs-gate plot, funnel, dose–response curves and plate map.
Light and dark themes; keyboard-first; WCAG AA contrast.

## 5. API v1 (resource model)

Generated OpenAPI → generated TypeScript client (no hand-written types).

```
GET  /api/v1/campaigns                       list (filter: program, status)
GET  /api/v1/campaigns/{id}                  cockpit model (header, funnel, rounds+why, hypotheses, reviewer)
GET  /api/v1/campaigns/{id}/hypotheses/{h}   one hypothesis: card + evidence + derivation
GET  /api/v1/campaigns/{id}/activity         recent human-readable events
GET  /api/v1/campaigns/{id}/events           SSE stream
GET  /api/v1/campaigns/{id}/provenance       chain + verification result
POST /api/v1/campaigns/{id}/archive
GET  /api/v1/inbox                           unified action queue
POST /api/v1/inbox/{item}/decision           approve / decline (identity from the token)
GET  /api/v1/programs, /programs/{slug}      program + gate progress
GET  /api/v1/instruments, /halt, POST /halt
POST /api/v1/analyses, GET/POST /analyses/{id}/…   (Analyze flow)
```

Write endpoints keep the existing guard (local Host/Origin, optional per-person
tokens). One event stream. Errors are RFC 7807-style `{type, title, detail}`.

## 6. What gets removed

- the Wizard flow (`/api/wizard/*`, `Wizard.tsx`): a hand-run experiment becomes a
  campaign with `autonomy: observe` and manual result upload (same cockpit);
- every `/api/*` route replaced by `/api/v1/*`, and the hand-written
  `client.ts` + duplicated types;
- the per-screen ad-hoc CSS in `web/src/styles` (replaced by tokens);
- duplicated read-model code paths once the v1 models read from `views/` only.

## 7. Phases (each ends green and committed)

Status: **1 done** (API v1 campaigns) · **2 done** (foundation: Tailwind tokens +
Radix primitives, shell with Halt / trust strip / ⌘K, generated client,
`/api/v1/system/status`, campaigns list, dark + light) · **3 done** (campaign
cockpit: outcome bar, interval-vs-gate forest plot on a log axis, rounds timeline
with why-this-plate, evidence drawer with the recorded dose–response curve + QC,
plain-language verdict explanation and next step, live activity) · **4 done** (decisions: Inbox, approvals on the campaign, sealed human review of
abstentions, follow-up / new-campaign / new-program dialogs with `gate` autonomy by
default, agents + full trace per actor, deterministic "what next"; legacy Workspace /
Program / Experiment / Wizard / Approvals screens and ~800 lines of old API removed) ·
**5 done** (Evidence: verify / anchor / exports; Lab: honest instrument badges, authority policy, SOP sign-off; Analyze: drop a plate file → curves with IC50 ± CI, QC, abstentions → your decision → attach as evidence; legacy Analysis screen, `/api/analyses` and the legacy CSS layer removed) · 6 next: polish (Playwright e2e, accessibility, README screenshots), Lab instrument Test-connection / configure.

1. **API v1 for campaigns**: pydantic response models, `/campaigns`, hypothesis,
   activity, events, provenance; OpenAPI export; tests on the golden campaign.
2. **Frontend foundation**: Tailwind + Radix primitives, tokens, app shell
   (rail, top bar, Halt, trust strip, ⌘K), generated client, routing.
3. **Campaign cockpit**: header/funnel, rounds timeline + why-this-plate,
   hypothesis table with interval plot, drawer, activity stream.
4. **Inbox + Halt** (approve/decline inline), then **Evidence** (verify, export).
5. **Today** (program board) and **Lab**.
6. **Analyze** rebuilt on the same system; Wizard removal.
7. Polish: accessibility pass, Playwright end-to-end tests, performance, README
   screenshots, delete the old routes and client.


## 8. Removed in phase 4 (and where it went)

- The LLM-drafted **program brief** and the problem-tree **program verdict / termination
  policy** were API+UI features of the old workspace. The engine modules remain
  (`problem_tree`, `proef campaign plan`), but they are no longer surfaced; the new
  program page shows deterministic **What next** recommendations instead. Restore the
  model-drafted brief on top of v1 if/when a model is configured for the workbench.
- `/api/wizard/*`, `/api/programs*`, `/api/experiments*`, `/api/approvals*`,
  `/api/instruments`, `/api/taste`, `/api/events` (SSE): replaced by `/api/v1/*` (the UI
  polls). `/api/analyses/*` → `/api/v1/analyses/*` (Analyze rebuilt).

## 9. Realigned to the original flow (Nov 14, 2026)

Phase 4 made the *campaign* (which needs candidates) the centre of the launch flow and dropped the problem-tree and the
LLM program brief from the workbench. That moved away from the original idea: **define a problem with limits, press
Start, an agent proposes hypotheses and designs the experiments, runs them under the person's approval, and loops.**
Restored, in order:

1. **Program limits** — rounds, lab runs, hypotheses, families, variants per family, breadth. Hard caps every campaign
   inherits; a campaign may use less, never more; the engine receives a real `ResourceBudget`.
2. **Start research + plan review** — the primary action on a program. A model proposes families of hypotheses; in
   *Ask me first* the campaign pauses and shows the plan (mechanism, refuted-if, confidence, citation status, every
   variant). The person removes hypotheses, declines, or approves; removals are recorded with a reason and never tested.
3. **Wording** — "autonomy" means only how much runs without approval. The ways to run are *Start research*, *I have
   candidates* and *Manual plate run*.

4. **One approval covers the plan** — an approved plan also approves its own lab submissions (sealed as `covered_by_plan`;
   the safety gate still binds; removed hypotheses are never covered). Your own candidate list still asks per submission.
5. **Re-plan after each round** (opt-in) — the model proposes the next hypotheses from the results (refuted ideas are not
   repeated); the person approves them through the same plan gate (`plan_decision` with a `stage`). Replay reports such
   runs as not reproducible, because that step needs the model.
6. **Research trees** — a model proposes a recursive split (`POST /programs/{slug}/decompose`, a proposal only); the person
   keeps the real sub-problems and edits each gate; `POST /programs/{slug}/trees` re-validates the tree against the program's
   **Split depth** and **Sub-problems** limits (the engine's `ProblemTree` guardrails), seals the decision (`research_tree`),
   allocates the lab-run budget down the tree by priority, and runs one campaign per leaf. `GET …/trees` rolls the leaves up
   each time (a parent is achieved only when its required children are; abstention is undecided, not failure).
7. **A critic agent** — a second model reads the plan before the person and raises advisory concerns (`plan_critique`); it
   can use its own model (`PROEF_LLM_CRITIC_*`). It cannot change, remove or approve anything.
8. **Model-designed dose ladders** — each variant may carry a validated, bounded ladder (3 to 8 doses, 0.001 to 1000 µM) shown in
   the plan and sent to the lab; unusable designs fall back to the default. Replicates remain the lab's property.
9. **Lab "Check driver"** — starts a driver in simulation mode and reads its handshake, recorded as an action; it never touches
   a physical device and never marks an instrument hardware-validated.

10. **Analyze handles measurements, not only plates** — a second analysis profile (`measurements`) for tables of values per
   sample (properties that are not dose series). Per sample: n, mean, sd and a 95% interval of the mean (the repo-wide
   `fitting.replicate_ci`: Student-t below 10 readings, a deterministic bootstrap from 10; none below 3), then the same call
   rule as everywhere (a straddling interval abstains). No plate means no Z′, stated on the page and in the report. The profile
   is chosen from the file's columns, or by the person. Campaign **Submit results** reads the same tables; a plain table is
   never fed to the plate reader (which took a replicate column for well coordinates), a property verdict now uses all of a
   sample's replicates rather than the first row, and a property reading with no interval is inconclusive, not decisive.

Still open: configuring a real device's address from the workbench; the model choosing replicates and controls; replay of
campaigns recorded before rounds were captured replays as one round.

Naming (Nov 15, 2026): the home page is **Programs** (it is the list of programs against their gates, not a date) and the queue is **Decisions** (everything in it is something a person must decide). Routes `/` and `/inbox` are unchanged.

Pause (Nov 15, 2026): a routine, scoped control beside the global Halt. **Halt** (top bar) is the emergency stop: global,
always reachable, and it REFUSES submissions. **Pause** is per program or per campaign and HOLDS them: the lab gateway
waits and releases the submission on resume, so a campaign never loses rounds to a pause; running plates finish. A
program pause holds every campaign of that program (the campaign process knows its program), a campaign pause only that
campaign, and Halt overrides both. Pausing and resuming are sealed in the ledger as human decisions (`pause`, `resume`, and
`paused_hold` / `pause_released` at the gateway). API: `POST /programs/{slug}/pause`, `POST /campaigns/{id}/pause`,
`GET /pauses`.

