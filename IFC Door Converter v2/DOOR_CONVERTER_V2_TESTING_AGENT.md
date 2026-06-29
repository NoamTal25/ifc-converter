# Door Converter v2 — Testing Agent Spec

Subagent brief for validating `IFC_door_converter_V2.py`. Mirrors the window v2 testing agent.
Everything runs with **`python3.11`** (ifcopenshell 0.8.5); plain `python3` lacks ifcopenshell.

## What "correct" means
The converter swaps each baked `IfcDoor`'s **Body** for a clean parametric golden-template rebuild
(sized to the door's measured extents, coloured from its harvested styles) and **changes nothing
else**. A door is "done" when it is (a) **manipulable** — its lining is a drivable parametric profile
— and (b) embedded exactly as before (identity, placement, opening chain, FootPrint, styles all
preserved), introducing no new schema errors.

## Run it
```
python3.11 "IFC Door Converter v2/test_door_converter_v2.py"        # all fixtures
python3.11 "IFC Door Converter v2/test_door_converter_v2.py" -v     # verbose (per-check)
python3.11 "IFC Door Converter v2/test_door_converter_v2.py" <one.ifc>
```
Fixtures = the real ADUs in `INPUT_IFC_FILES_HERE/`. Each is converted into a **throwaway temp**;
inputs are never mutated (asserted by sha256 + mtime). Exit 0 = all pass.

## The 7 layers (all re-derived independently of the converter's own `verify()`)
- **A — Conservation.** Counts of every other element/relationship type unchanged; door GlobalId
  multiset identical; fill/void edge count identical. `IfcRelDefinesByType` count invariant (the
  converter never mints a type).
- **B — Preservation.** Openings unmoved; rebuilt doors keep their exact placement; `FootPrint`
  preserved where present; source file byte-identical after the run.
- **C — Manipulable state.** Every rebuilt (marked) door is in the clean parametric state: ≥4 plain
  `IfcRectangleProfileDef` swept solids (a 4-bar lining + any panels/mullions/rails/track/handles),
  with **no** `IfcRectangleHollowProfileDef` (Gaudi mis-renders it — §6), all Body items styled.
  Also asserts every rebuilt door carries a non-empty `FormX_Door_Window.OpeningDirection` label.
- **D — Manipulate.** Parametric resize drives the **shared recipe** (the function that authored every
  rebuilt door: build a single-leaf + a 2-leaf type at W vs 1.5·W) → lining grows along that axis only,
  border held constant (measured on the 'frame' bars + 'panel's, so barn track / proud handles don't
  skew it). Move (rigid shift, size preserved, GlobalId intact). Rotate (geometry untouched, stays
  valid). Each manipulation introduces no new validate errors.
- **E — Idempotency.** Re-running the converter on its own output leaves rebuilt doors unchanged.
- **F — Negative control (TEETH).** The SAME manipulable-state test on the ORIGINAL baked doors must
  return **0** (a baked brep / mapped item has no clean rect-only swept solids) — proving the suite can
  detect a non-working converter. Plus: every rebuilt door is manipulable, and the rebuilt count
  matches the pinned `BASELINE_REBUILT` per fixture (a no-op / silent regression trips this).
- **G — Classification (TEETH against misclassification).** Pins the rebuilt **FormX-type multiset**
  per fixture (`BASELINE_TYPES`, independent ground truth) + each rebuilt door's recipe-implied solid
  count. Classification *is* the structural core of golden-template-swap (a wrong class → a wrong
  golden → wrong geometry), and A–F can't see it (every single-leaf rebuild satisfies them). Forcing
  every door to one type — which passes A–F — FAILS here.

## Pinned baselines
`BASELINE_REBUILT` (counts): LEXFORD 2 · SAN_JUAN 1 · Sunflower 3 · Turnberry 5 (= all 11 doors).
`BASELINE_TYPES` (FormX-type multiset, layer G): e.g. Turnberry = {SINGLE_FLUSH 1, POCKET 1,
BIFOLDING_GLASS 1, INTERIOR_DOUBLE 2}. Update either only with a deliberate, explained change.
Current: **4/4 fixtures, 276 checks.**

## Gotchas the harness already handles (CLAUDE.md §6)
- **Analytic bbox, not tessellation** — the geom kernel returns nondeterministic empty meshes on
  freshly-authored solids, so corners are computed from profile dims + the placement matrix.
- **Kernel returns metres** regardless of file units → divide by unit scale.
- **Face-plane drift only** — proud handles + the folding-depth clamp deliberately change the
  through-wall envelope; drift is measured on the two largest-extent axes.
- **Frame/lining = 4 solid bars, NOT `IfcRectangleHollowProfileDef`** (§6). Gaudi mis-renders the
  hollow profile (uniform pane↔frame "space"), and the web "openIFC" viewer skips it entirely;
  4 plain bars render flush everywhere. The tester therefore rejects any hollow profile.

## If a check fails
Read the `FAIL [layer] …` line. A/B fail = something other than doors changed (preservation bug).
C/D fail = topology/parametric regression (the rebuild isn't clean/drivable). F-teeth fail
(`n_before != 0`) = the manipulable test got too loose and would pass a no-op — fix the test, not
the converter. A baseline-count mismatch = classification or gating changed — confirm it's intended,
then re-pin.
