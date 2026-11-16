# Flows

## 1. A campaign, with the people in it

```mermaid
sequenceDiagram
    autonumber
    participant P as Person
    participant E as Engine
    participant M as Model
    participant G as Gates
    participant L as Lab adapter
    participant R as Ledger

    P->>E: program: problem, gate, limits
    E->>R: gate registered before any data
    E->>M: propose families of hypotheses
    M-->>E: hypotheses (refuted if, citation)
    E->>M: critic reads the plan
    E->>P: plan review
    P-->>E: approve, remove or decline
    E->>R: plan_decision (and what was removed)
    loop each round
        E->>G: submit the selected hypotheses
        G->>L: run, if every check passes
        L-->>E: raw files
        E->>E: fit, QC, 95% interval vs the gate
        E->>R: verdict (validated, refuted or abstained)
    end
    E->>P: abstentions and confirmations to decide
    P-->>E: follow up, accept or drop
    E->>R: human_review
```

With **Re-plan after each round** on, the engine asks the model for new hypotheses from the results and sends them to
the person the same way before the next round.

## 2. Between a plan and a lab run

```mermaid
flowchart LR
    S["Submission"] --> RR["Readiness review"]
    RR -->|blocked| X1["Redesign, reason recorded"]
    RR --> SE["Safety engine"]
    SE -->|refused| X2["Refused, rule-set version recorded"]
    SE --> HP["Halt and pause"]
    HP -->|halt| X3["Refused"]
    HP -->|pause| W["Held until resumed"]
    W --> HP
    HP --> AZ["Authorization and SOPs"]
    AZ -->|needs a person| X4["Queued for approval"]
    AZ --> LAB["Lab adapter"]
    LAB --> RAW["Raw files kept as the evidence"]
```

A plan approval covers that plan's own submissions (`covered_by_plan`). The safety engine still checks every one.

## 3. From a device export to a verdict

```mermaid
flowchart LR
    D["Instrument writes a file"] --> M["DeviceMonitor notices it"]
    M --> A["Analysis: plate or measurements"]
    A --> Q{"QC ok, enough replicates?"}
    Q -->|no| AB["Abstain"]
    Q -->|yes| CI{"95% interval vs the gate"}
    CI -->|fully inside| V["Validated"]
    CI -->|fully outside| RF["Refuted"]
    CI -->|straddles| AB
    V --> OC["Orthogonal assay confirms or refutes the direction"]
    AB --> H["A person decides"]
```

## 4. Where it runs

```mermaid
flowchart TB
    subgraph Local["One machine"]
        SPA["Workbench SPA"] --> API["FastAPI app"]
        CLI["proef CLI"] --> ENG["Campaign engine"]
        API -->|"starts a campaign subprocess"| ENG
        ENG --> DB[("SQLite ledger and registries")]
        API --> DB
    end
    ENG --> LAB["Lab adapter: simulator, plate reader share, Opentrons"]
    ENG --> MODEL["Model provider (optional)"]
    ENG -.->|"opt-in"| SBX["Sandbox: local or Docker"]
```
