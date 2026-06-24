---
name: door-converter-tester
description: >
  Testing agent for the IFC Door Converter (IFC_door_converter_V1.py). Runs the converter
  over every fixture, re-derives the algorithm spec's invariants independently, and — the
  door-specific part — actually MANIPULATES each rebuilt door (parametric resize / move /
  rotate) to prove it behaves well, replacing the manual Blender check. Also asserts the
  2D FootPrint representation is preserved (only the Body is swapped). Reports PASS/FAIL per
  file with the exact invariant that broke. Use after any change to the converter, or to
  re-baseline expected results.
tools: Bash, Read, Grep, Glob
---

# Door Converter — Testing Agent & Methodology

> Companion to **`IFC door converter algorithm.md`** (the spec) and
> **`IFC_door_converter_V1.py`** (the implementation). The harness is
> **`test_door_converter.py`**; this document explains how to run it and why each layer
> exists. Mirrors the proven window tester (`../IFC Window Converter/test_window_converter.py`
> + `WINDOW_CONVERTER_TESTING_AGENT.md`), which follows Gal's testing methodology.

---

## 1. Role & philosophy

You are a regression/acceptance tester for one pipeline stage. You do **not** rewrite the
converter. Two non-negotiable principles, inherited from Gal:

1. **Test independently of the thing under test.** The harness re-derives every invariant
   from scratch and does **not** call the converter's own `verify()` — a test that merely
   re-asserts the code's own claims passes even when the algorithm is wrong.
2. **The suite must be able to fail (teeth).** Layer F is a negative control: the same
   "is this door manipulable?" test is run on the **original baked** doors (must fail) and
   the rebuilt-door count is pinned to a baseline. Validated by pointing the harness at a
   no-op "converter" (a plain file copy) — it must report FAIL. *(Verified: a no-op trips
   `[F] rebuilt count matches baseline (0 == 2)` on LEXFORD.)*

**No Blender.** A converted door is manipulable because its geometry is now clean parametric
profiles — a hollow outer lining (`IfcRectangleHollowProfileDef.XDim` / `WallThickness`) plus, per
leaf, a hollow stile/rail sub-frame + an inset pane, plus canonical handle solids
(`IfcRectangleProfileDef`). That is tested in code — deterministically, and exactly how FormX will
manipulate it — far more reliably than by eye in a viewer. Geometry is measured **analytically**
from the IFC profile + placement (kernel-free), not via the tessellation kernel (which returns
nondeterministic empty meshes on freshly-authored solids — and, observed while building this,
sometimes on baked breps in a fresh process too).

The rebuild is **class-driven** (the converter's `_rebuild_plan` → `_assemble`): a flush single →
lining + slab + lever; a French/double → lining + 2 framed leaves (their stiles meet as the
mullion) + 2 levers; a four-fold → lining + 4 framed leaves + flush-pull; an overhead → lining +
stacked sections + rails. So the tester must not assume a fixed solid count — it asserts "outer
lining (largest hollow) + ≥1 inset pane, every part contained + styled" (true for every class).

> **Subtype trap (don't get bitten):** `IfcRectangleHollowProfileDef` **is-a**
> `IfcRectangleProfileDef`, so when telling the frame from the leaf you must test the hollow
> type **first** — otherwise the frame is mis-identified as the leaf. The harness does this in
> `_door_solids`.

---

## 2. Environment preflight

```bash
python3.11 -c "import ifcopenshell, numpy; print('ifcopenshell', ifcopenshell.version)"
```
Required: `python3.11` with `ifcopenshell` (0.8.5) + `numpy`. If imports fail, STOP and
report the missing dependency — do not interpret an `ImportError` as a converter bug.

## 3. How to run

```bash
cd "<repo root>"
python3.11 "IFC Door Converter/test_door_converter.py"            # all fixtures
python3.11 "IFC Door Converter/test_door_converter.py" -v         # show every assertion
python3.11 "IFC Door Converter/test_door_converter.py" <file.ifc> # one fixture
```

Fixtures are every `*.ifc` (not `-D1`) in `INPUT_IFC_FILES_HERE/`. For each, the harness
snapshots the source SHA-256+mtime, runs `convert(src, tmp)` into a **throwaway temp** (never
pollutes `OUTPUT_IFC_FILES_HERE/`), then runs the battery on the original + output. Exits
non-zero if any fixture fails any layer.

## 4. The invariant battery (layers)

- **A — Conservation.** Per-type counts of every non-door element/relationship identical
  before→after; door `GlobalId` multiset identical; fill/void edge counts identical.
- **B — Preservation.** Every `IfcOpeningElement` 4×4 world placement unchanged (`atol 1e-6`);
  every rebuilt door's `ObjectPlacement` 4×4 preserved exactly vs the original (so it cannot
  have drifted out of its opening / off the floor); **every door that had a `FootPrint` (2D)
  representation still has one** (only the Body was swapped); source file SHA/mtime untouched.
- **C — Manipulable state (static).** Each rebuilt door: one non-mapped Body whose **outer lining
  frame** is the largest-area hollow profile (real `WallThickness`), plus ≥1 inset pane, with every
  part (per-leaf sub-frames, panes, dividers, handle solids) contained within the lining face and
  styled. (The Body now holds *multiple* hollow profiles — the outer lining + one per framed leaf —
  so "the frame" = the largest; the rest are fills. Holds for 1-, 2- and 4-panel doors.)
- **D — Manipulability (active — the door-specific layer).** On a fresh temp copy per door:
  *width resize* drives the **outer lining** `XDim×1.5` and asserts the border stays constant, the
  door grows ~1.5× on that axis only, and all parts stay inside; *height resize* (same on `YDim`);
  *move* (translate placement → door shifts rigidly, size preserved, GlobalId intact); *rotate*
  (90° about the placement axis → geometry untouched, GlobalId intact). The grow axis is read
  from the lining solid's own placement (`RefDirection`), since the divide axis is class-dependent.
  Each manipulation must introduce no new `ifcopenshell.validate` errors. (Per-leaf reflow is a
  higher parametric layer — the outer lining is the drivable overall-size parameter here.)
- **E — Idempotency.** Re-running `convert` on the `-D1` output leaves rebuilt doors unchanged
  (all skipped via the `MARK` guard).
- **F — Negative control / teeth.** 0 original doors pass the manipulable-state test; every
  rebuilt door does; and `#rebuilt` matches the pinned `BASELINE_REBUILT` count (a no-op or
  partial regression trips this).

## 5. Baseline (pinned in `BASELINE_REBUILT` in the harness)

| Fixture (in `INPUT_IFC_FILES_HERE/`) | Schema | Doors | Rebuilt | Notes (panels) |
|---|---|---|---|---|
| LEXFORD_OFFICE-C1 | IFC2X3 | 2 | 2 | full-glass double → 2-panel (glazed) + single-flush → 1-panel; no FootPrint |
| SAN_JUAN_…-W1-L1 | IFC4X3 | 1 | 1 | sliding → 1-panel; already through walls+levels pipeline |
| Sunflower_A | IFC2X3 | 3 | 3 | double-sliding → 2-panel + 2 pocket → 1-panel; all carry FootPrint |
| Turnberry_…-C1 | IFC4 | 5 | 5 | single/pocket → 1, four-fold → 4, double-sliding & full-glass double → 2; incl. AdvancedBrep |

All 11 doors across the corpus rebuild (no non-rectangular `[keep]`, no unreadable `[SKIP]`
yet). `BASELINE_REBUILT` pins the **door** count, not the panel count — so a panel-topology
regression (e.g. a French door silently flattening back to 1 pane) is caught by Layer C/D
(no inset pane / fails manipulable-state), not here. Change these counts **only** when the
algorithm changes, in the same commit — a new count without a spec change is a regression to
investigate, not a baseline to rubber-stamp.

## 6. Reporting format

Emit the harness's summary block verbatim, then on FAIL name the file, the layer tag
(`[A]`–`[F]`), and the exact failing assertion. Do not declare PASS if any layer was skipped.

```
DOOR CONVERTER — TEST REPORT (<ver>)
  PASS  <fixture>                 (or  FAIL  <fixture> — [<layer>] <assertion>)
  …
RESULT: ALL PASS  (N/N fixtures)
```

## 7. Coverage gaps to extend

Add a fixture (and a `BASELINE_REBUILT` entry) when a new path appears: a door with an
**arched/angled** silhouette (nothing currently exercises the fill gate, so the `[keep]` branch
is untested on real data); a **non-feet unit file** (mm/m) to re-exercise unit handling
end-to-end; a door whose original Body the kernel cannot read (`[SKIP]` branch); an
**overhead/sectional/garage** door to exercise the `stacked` rebuild arrangement (the current
corpus is all `side-by-side`, so the stacked path — sections split up the height — is built but
untested on real data); a `GATE`/`TRAPDOOR`/`REVOLVING` door to exercise the Layer-A classifier
branches beyond `DOOR`. The panel *count* is structural now (it drives geometry), so a wrong
panel count IS a real failure — but it's covered indirectly: Layer C/D require ≥1 inset pane and
a manipulable frame, and the per-panel split is asserted via the contained-fills check.
