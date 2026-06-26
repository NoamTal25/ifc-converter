# Window Converter v2 — Testing Agent Spec

How to validate the v2 window converter. Mirrors the v1 / Gal methodology: **analytical, no
Blender** — manipulability is a code-testable property once geometry is a parametric profile.

## Run it

```bash
cd "IFC Window Converter v2"
python3.11 test_window_converter_v2.py        # all INPUT_IFC_FILES_HERE/ fixtures
python3.11 test_window_converter_v2.py -v      # verbose (every passing assertion)
python3.11 test_window_converter_v2.py <f.ifc> # one fixture
```
Exit 0 = all fixtures pass; non-zero = a failure. Each fixture is converted into a throwaway temp
(never pollutes `OUTPUT_IFC_FILES_HERE/`); invariants are re-derived from scratch (the tester does
**not** call the converter's own `verify()`).

## What it asserts (6 layers)

- **A — conservation.** Counts of every non-window element/relationship type unchanged
  (incl. `IfcRelDefinesByType` — v2 never mints a type). Window GlobalId multiset identical.
  fill/void edge count identical.
- **B — preservation.** Openings unmoved; rebuilt windows keep their exact `ObjectPlacement`;
  `FootPrint` preserved where present; source file byte-identical after the run.
- **C — manipulable state.** Each rebuilt window is ≥5 plain `IfcRectangleProfileDef` swept solids
  (a 4-bar lining + ≥1 inset pane + optional mullion/transom), with **no**
  `IfcRectangleHollowProfileDef` (Gaudi mis-renders it — §6), every Body item styled, and at least
  one solid strictly inset on the face plane (a pane within the border).
- **D — manipulate.** Parametric resize drives the **shared recipe** (the function that authored
  every rebuilt window: build at W vs 1.5·W for single + both split topologies) → lining grows ~1.5×
  on the driven axis only, frame border held constant. Move (rigid, size preserved, GlobalId intact).
  Rotate (geometry untouched). No new validate errors after each edit. Bbox is computed
  **analytically** from profile + placement (the geom kernel returns nondeterministic empty
  meshes on freshly-authored solids — §6).
- **E — idempotency.** Re-running the converter on its own output leaves rebuilt windows unchanged.
- **F — teeth (negative control).** The SAME manipulable test on the ORIGINAL baked windows MUST
  return 0 (a brep has no drivable width). Rebuilt count MUST equal the pinned baseline, so a
  no-op / silent regression cannot pass vacuously.

## Pinned baselines (`BASELINE_REBUILT`)

| Fixture | Rebuilt | Why not all |
|---|---|---|
| LEXFORD_OFFICE-C1 | 6 | 1 trapezoid gated |
| SAN_JUAN_CYPRESS…-W1-L1 | 5 | 1 skylight gated |
| Sunflower_A | 4 | 1 bodiless "Square Opening" skipped |
| Turnberry…-C1 | 8 | — (6 single + 2 double-horizontal) |

Update a baseline **only** with an intended change (new fixture, taxonomy change). A new fixture
without an entry falls back to the looser "rebuilt ≥ 1".

## Ground truth beyond the tester

The automated suite proves structure + manipulability + non-regression. The **viewer check is the
final ground truth** the team wants: open the `golden_templates/*.ifc` and the `-WIN2` outputs in
an IFC viewer and confirm frame / mullion / pane topology + colours read correctly.
