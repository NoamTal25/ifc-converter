# IFC Window Converter — Algorithm & Test Specification

> **Living document.** Describes, step by step, what the window converter does and how to
> verify it. Kept in sync as the algorithm evolves. Last updated: **2026-06-22**.
>
> **Implementation:** `IFC_window_converter_V1.py`

---

## 1. Purpose

Turns a Revit-exported ADU IFC into a "FormX IFC" as far as its **windows** are concerned —
every other element (walls, doors, slabs, roof, openings, spaces, storeys, …) is left
exactly as-is. This is one stage in a multi-element pipeline (a sibling to Gal's walls
cleanup / levels organizer / floors definer).

Real exports bake each window's geometry into a frozen mesh/Brep (or a fixed bundle of
extrusions) carried on a shared `IfcRepresentationMap`. There is no live "width" for FormX's
chat manipulation ("make it wider") to grab. This stage replaces each window's body with a
**clean, parametric, axis-aligned hollow-frame + glazed pane**, sized and oriented from the
window's own measured geometry, so the result is manipulatable — while preserving the
window's identity (`GlobalId`), its placement, and every relationship it participates in.

**Design stance — match the proven FormX recipe, not a richer spec.** Gal's three working
tools, including walls cleanup, author **clean geometry + canonical Name + standard
`PredefinedType` + intact relationships, and nothing more** — no bespoke property sets, no
operation-enum type apparatus. This stage does the same. The richer "golden target" layer
(`IfcWindowType` + lining/panel operation enums + `Pset_WindowCommon`, per-style panel
topology) is **deferred**, not discarded — see §7.

## 2. How to run

```
python3 IFC_window_converter_V1.py                 # batch: INPUT_IFC_FILES_HERE → OUTPUT_IFC_FILES_HERE
python3 IFC_window_converter_V1.py <in.ifc>        # single file → OUTPUT_IFC_FILES_HERE/<in>-WIN1.ifc
python3 IFC_window_converter_V1.py <in.ifc> <out.ifc>
```

- **Input:** a Revit-exported `.ifc` (schema-tolerant — tested on IFC2X3, IFC4, IFC4X3).
- **Output:** a copy with `-WIN1` before the extension, written to `OUTPUT_IFC_FILES_HERE/`.
- **The original is never modified.** All edits happen on the copy.
- Dependency: **`ifcopenshell` only** (tested on 0.8.5) — self-contained, no project-internal
  imports. (Style labelling + skylight detection are an inline keyword scan; see Step 2.)

## 3. Core principle — only the windows change

1. **Non-window elements are untouched.** Counts of every non-window element and
   relationship type are identical before and after (see §4).
2. **Identity preserved.** Each window keeps its `GlobalId` and `ObjectPlacement`; only its
   `Representation` (and `Name` / `Description` / `PredefinedType`) change.
3. **Relationships preserved.** The `IfcRelFillsElement → IfcOpeningElement →
   IfcRelVoidsElement → IfcWall` chain and `IfcRelContainedInSpatialStructure` are never
   touched. Openings do not move, so windows stay located.
4. **Rebuilt geometry occupies the original's box.** The new frame+pane is sized and
   centred on the window's own measured extents, so its world position/footprint matches the
   original within tolerance — it still fits its hole.

## 4. Algorithm (in execution order)

For each `IfcWindow` in the copied model:

### Step 0 — Skip if already converted *(idempotency)*
If `Description == "FormX-WIN1 parametric window"`, the window was produced by a previous
run — skip it. Re-running the tool adds nothing.

### Step 0b — Rectangularity gate *(don't flatten odd shapes)*
Compute the window's **face-fill ratio**: the convex-hull area of its face silhouette ÷ its
bounding-rectangle area (~1.0 for a true rectangle, ~0.5–0.6 for a triangle/trapezoid). If it
is below `FILL_MIN` (0.95) the window is **non-rectangular** (trapezoid / triangle / gable /
arch) — the neutral rectangular template would flatten it, so **leave its original geometry
untouched** and flag it (`[keep]`). Correct-but-original beats clean-but-wrong. (Faithful
rebuild of non-rectangular windows — real profile + frame — is Phase 2.)

### Step 1 — Measure the window in its own local frame
Read the window's geometry with the IfcOpenShell kernel in **local** coordinates
(`use-world-coords = False`) and take its axis-aligned bounding box. The kernel returns
metres regardless of file units, so divide by `ifcopenshell.util.unit.calculate_unit_scale`
to get the box in the file's own units (ft / mm / m — never assumed). This empirical
measurement is the window analog of walls-cleanup reading length/thickness from Brep
vertices, and it sidesteps every exporter's local-axis convention: the box's **thinnest
axis is the through-wall depth**; the other two span the face. (Verified: the measured
extents match `OverallWidth`/`OverallHeight` exactly on the FormX ADUs.)

### Step 2 — Inline style label (Name + PredefinedType only)
A lightweight keyword scan over the window/type family Name (`_style_token`) → a coarse style
token, used **only** to (a) choose a canonical Name label (`Fixed window: <id>`, `Casement
window: <id>`, …, keeping the trailing Revit element id like Gal does) and (b) pick
`PredefinedType`. This is **cosmetic, not structural** — a wrong label never corrupts geometry
(real exports rarely populate structured operation enums anyway; style lives in the Name). The
one semantic distinction is the `"skylight"` keyword → `PredefinedType = SKYLIGHT` (a roof
window). No external classifier dependency.

### Step 3 — Author a clean parametric body
Build two swept solids filling the measured box, centred on it:
- a **hollow lining frame** (`IfcRectangleHollowProfileDef`) across the full depth;
- a **glazed pane** (`IfcRectangleProfileDef`), inset by the frame thickness and thinner in
  depth, centred.

Both are extruded along the measured **depth axis** via an `IfcExtrudedAreaSolid` whose
placement maps profile-local +Z onto that axis — so the body lands in the wall plane at any
rotation. Frame face width and glaze thickness are config constants in **metres**, converted
to file units per file. The frame is symmetric, so width-vs-height ambiguity is irrelevant.

### Step 3b — Carry forward surface styles (glass / frame appearance)
Before swapping the representation, **harvest the original window's `IfcSurfaceStyle`s** by
walking its (mapped) geometry items' `IfcStyledItem.Styles` and bucketing by transparency:
the most transparent → **glass**, the opaque → **frame**. Re-attach them to the new solids
(`IfcStyledItem` on each: glass on the pane, frame on the frame), reusing the existing style
entities verbatim — identical appearance and schema-correct for IFC2X3 (which wraps styles in
`IfcPresentationStyleAssignment`) vs IFC4/4X3 (direct). Fallback to an authored glass+frame
pair only if the window carried no styles. **Without this the rebuilt items have no styling
and viewers render them solid gray** (regression found in Blender testing, v1.1 fix).

### Step 4 — Swap representation, preserve identity
Point `win.Representation` at the new `IfcProductDefinitionShape` (reusing the window's
existing `'Body'` representation context). Then remove the old **per-window** representation
entities (product-definition-shape, shape-representation, mapped item), first detaching them
from any `IfcPresentationLayerAssignment` so none is left empty. The **shared
`IfcRepresentationMap`** is never removed — other windows may still map it. Set the canonical
Name, stamp `Description` with the idempotency marker, and set `PredefinedType`.

## 5. Output & built-in verification

After writing the `-WIN1` copy, `verify()` reopens source and output and asserts:

- **Preservation** — count of every non-window element/relationship type
  (`IfcWall`, `IfcSlab`, `IfcDoor`, `IfcOpeningElement`, `IfcSpace`, `IfcRoof`,
  `IfcRelFillsElement`, `IfcRelVoidsElement`, `IfcRelContainedInSpatialStructure`,
  `IfcRelDefinesByType`, …) is unchanged. `IfcWindow` count is unchanged too.
- **Window GlobalIds preserved** — same set before and after.
- **Openings did not move** — every `IfcOpeningElement` world placement is unchanged
  (`atol 1e-6`), so windows stay located.
- **Window world-bbox drift** — each window's world bounding box matches the original within
  `BBOX_TOL_M` (20 mm); confirms the rebuilt body still fills its hole.
- **Surface styles present** — every rebuilt window's new items carry an `IfcStyledItem`
  (else they would render gray).
- **No new schema errors** — `ifcopenshell.validate` message count on the output is `≤` the
  source's (pre-existing source issues are not blamed on us).

Prints a per-file summary and `RESULT: ALL CHECKS PASSED ✓ / SEE WARNINGS ABOVE ✗`.

> **Note.** The log reports each window's ORIGINAL representation kind (e.g.
> `mapped/AdvancedBrep`, `mapped/SweptSolid`) so you can see what was rebuilt. The
> manipulability of the *output* is what matters, and it's proven by the automated tester
> (`test_window_converter.py`), not by a score.

## 6. Test fixtures & expected results

Reference inputs are the real FormX ADUs in `../FormX Designs IFC/` (HUDSON, LEXFORD,
Thomas). All currently pass `RESULT: ALL CHECKS PASSED`.

| Fixture | Schema | Units | Windows | Notes |
|---|---|---|---|---|
| `FORMX_HUDSON_ADU` | IFC4X3 | ft | 5 | mapped SweptSolid + FacetedBrep; one shared map (2 windows). Source has 2 pre-existing validate errors (unchanged). |
| `LEXFORD_OFFICE-C1` | IFC2X3 | ft | 7 | legacy schema; FacetedBrep + swept; validate 0→0. |
| `Thomas_..._ADU` | IFC4 | ft | 11 | `IfcAdvancedBrep` windows + 2 **skylights** (depth axis = Z, flat in roof) correctly forced to `PredefinedType=SKYLIGHT`. Max bbox drift ~7 mm (Brep bevels). |

Idempotency: re-running on a `-WIN1` output reports `rebuilt=0, already=N`.

## 7. Deferred — Phase 2 "golden-spec" enrichment (do not forget)

Intentionally **not** in v1, to be promoted only when FormX confirms it consumes the
semantics, or a viewer test shows it's needed (see CLAUDE.md §7/§8):

- `IfcWindowType` + `IfcWindowLiningProperties` + `IfcWindowPanelProperties` (operation enums).
- `Pset_WindowCommon` authoring, carrying forward real surviving values (U-value, IsExternal).
- Per-style **panel topology** (V/H mullions, multi-pane splits) and the full 12-style golden
  vocabulary, archived in `../Old Context/FormX 6.22 IFC Generated/`. Promoting this layer means
  feeding a style token into geometry instead of just the Name. `WIN-HUNG-DBL_H` is abstract (a
  `SPECS` entry with no golden `.ifc`) — handle then.

## 8. Known limitations

- **One neutral template (rectangular only).** Every *rectangular* window becomes a
  single-pane frame+pane regardless of true operation/panel layout — a double casement renders
  without its mullion (fidelity lives in the §7 parking lot). **Non-rectangular windows
  (trapezoid/triangle/gable/arch) are detected by the face-fill gate (Step 0b) and left as
  their original geometry** rather than flattened; faithful clean rebuild of those (extrude
  the real silhouette profile) is Phase 2.
- **Object-level scaling in Blender is not parametric.** Scaling a converted window in Blender
  rigidly stretches the whole mesh (frame thickness included) and does not update
  `OverallWidth` or regenerate proportioned geometry — that live behaviour is the Phase-2
  parametric layer (§7), not a v1 feature.
- **Brep bevels.** For `IfcAdvancedBrep` windows the axis-aligned box can differ a few mm
  from the true hull; within the 20 mm tolerance, but the rebuilt body is a clean rectangle.
- **Geometry must be readable.** A window whose representation the kernel cannot evaluate is
  skipped and flagged (`[SKIP]`), left untouched — never guessed.
- **Classifier is name-driven.** Style labels are best-effort (English/German family names);
  wrong labels are cosmetic in v1. Skylights are caught by a direct name override.
