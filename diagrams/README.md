# Diagrams

The four diagrams in the README, as self-contained SVG (no external fonts or scripts):

- `architecture.svg`: people, engine, gates, interfaces and the ledger
- `campaign-flow.svg`: one campaign from problem to verdict
- `gates.svg`: the checks between an approved plan and a lab run
- `verdict-rule.svg`: how the 95% interval against the gate becomes validated, abstained or refuted

They are generated, not hand-edited: change `scripts/render_diagrams.py` and run

```sh
python scripts/render_diagrams.py
```

`docs/flows.md` keeps longer Mermaid flows that GitHub renders inline.
