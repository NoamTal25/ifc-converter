# IFC Window Converter v2 — Algorithm (golden-template-swap)

The go-forward FormX method (CLAUDE.md §2a): **classify → instantiate the FormX golden template
→ inject params → swap**. Each baked `IfcWindow` is rebuilt as a clean parametric instance of one
of FormX's defined window types, sized from the window's own measured extents, authored straight
into the source file's schema + units. Everything that is not a window is preserved exactly.

This supersedes v1 (`../IFC Window Converter/`), which authored one neutral hollow-frame+pane for
*every* window regardless of type. v2 is **type-driven**: the classification picks the geometry.

---

## Step 0 — Inputs & grounding

FormX's window taxonomy is the PDF *"IFC Standardizer: Template Gallery categorizing"*:

| FormX type | Geometry | Panel subtypes |
|---|---|---|
| `SINGLE_PANEL_WINDOW` | lining + 1 pane | FIXED · CASEMENT · AWNING · SLIDER · DOUBLE_HUNG |
| `DOUBLE_HORIZONTAL_WINDOW` | lining + vertical mullion + L/R panes | per-panel |
| `DOUBLE_VERTICAL_WINDOW` | lining + horizontal transom + T/B panes | per-panel |
| `TRAPEZOID_WINDOW` | — (gated; no parametric template yet) | — |

Grounding scan of the 4 real ADUs drove these decisions:
- **No opening hosts >1 window** → the PDF's "merge two adjacent windows into a DOUBLE" never
  occurs. The DOUBLE windows are *single* `IfcWindow`s named `…-Double`. Adjacency-merge deferred.
- **DOUBLE_HUNG** is a `SINGLE_PANEL_WINDOW` panel subtype (two stacked sashes), authored with a
  horizontal transom — *not* a compound window.
- Edge cases & dispositions: trapezoid → gate; skylight → gate (not a FormX type); `GeometricSet` /
  bodiless → gate; name with no operation keyword → default **FIXED**.

## Step 1 — Classify (`classify_window.py`)

Pure, geometry-free. Reads the family/type **Name**. Ordered keyword rules (first match wins):

1. `trapezoid` → **gate** (non-rectangular)
2. `skylight` → **gate** (not a FormX type)
3. `hung` → `SINGLE_PANEL_WINDOW` + `DOUBLE_HUNG` (golden `SINGLE-DOUBLEHUNG`, split H)
4. `double` (not `single`) → `DOUBLE_HORIZONTAL_WINDOW` (split V); both leaves = operation keyword
   found alongside (casement/slider/…), default FIXED. `vertical`/`stacked` ⇒ `DOUBLE_VERTICAL`.
5. `casement`/`awning`/`slider|sliding`/`fixed` → `SINGLE_PANEL_WINDOW` + that panel
6. no keyword → `SINGLE_PANEL_WINDOW` + **FIXED**

Returns a recipe: `{gate, reason, formx_type, predef, split, part_type, golden, panels, pset_panel, name}`.

## Step 2 — Measure (the element's OWN local frame)

- Read the local-frame vertex cloud from the geom kernel (metres → ÷ unit scale = file units).
- bbox extents → **depth = thinnest axis** (through-wall). Of the two face axes, the one whose
  world direction is most vertical (largest |world-Z| from the placement matrix) = **height**;
  the other = **width**. This orients a mullion/transom correctly under any rotation.
- **Gates (geometric):** fill-ratio < 0.95 (convex-hull silhouette ÷ bounding rect ⇒ non-rect),
  no `Body` representation, or unreadable geometry (`GeometricSet`) → preserve original, flag.

## Step 3 — Author geometry (`golden_geometry.py`, the SHARED recipe)

The same module that generates the reviewable golden IFCs (`generate_goldens.py`) authors the
in-place geometry, so a converted window is provably identical to its golden, scaled. Topologies:
- `split=None` → hollow lining + 1 centred pane
- `split='V'` → hollow lining + centred vertical mullion + L/R panes (DOUBLE_HORIZONTAL)
- `split='H'` → hollow lining + centred horizontal transom + T/B panes (DOUBLE_VERTICAL / double-hung)

Lining = `IfcRectangleHollowProfileDef` (`WallThickness` = the drivable frame border); panes/bars
= `IfcRectangleProfileDef`; all extruded along the measured depth axis, centred on the measured
box, so the rebuild lands exactly in the opening. `frame_thk`/`glaze_thk`/`bar_thk` are clamped so
a small instance still yields a valid frame + inset pane.

## Step 4 — Styles, params, swap

- **Surface styles** — harvest the original window's `IfcSurfaceStyle`s (bucket by transparency:
  glass = most transparent, frame = opaque), re-attach glass to panes and frame to lining+bars.
  Fallback default pair if the original carried none. (Derive-from-baked, not baked into goldens.)
- **FormX param apparatus**, all at the **occurrence level** (never a second `IfcWindowType` —
  these windows are already typed by Revit and `IfcRelDefinesByType` is `[0:1]`; we link via
  `IfcRelDefinesByProperties`, which is many-per-element):
  - `IfcWindowLiningProperties` + `IfcWindowPanelProperties` (parametric window detail), and
  - `Pset_WindowCommon` — the PDF contract: `OverallWidth/Height`, `Depth`, `RoughWidth/Height`,
    `PanelType` (or `PanelTypeLeft/Right` · `Top/Bottom`), `SplitWidth`/`SplitHeight`,
    `HandFlipped`, `FacingFlipped` (default False until a source signal exists).
- **Swap the Body only** — replace the `Body` `IfcShapeRepresentation` in place, matched by
  `.id()` (ifcopenshell returns fresh wrappers — `is` never matches, §6), preserving `FootPrint`,
  `GlobalId`, `ObjectPlacement`, and the opening→fill→void→host chain + spatial containment.
  Old per-window rep is cleaned up (de-referenced from any presentation layer first; the shared
  `IfcRepresentationMap` is never touched).
- Canonical `Name` = the FormX type (+ trailing Revit id); `PredefinedType`/`PartitioningType`
  set where the schema supports them; `Description = "FormX-WIN2 parametric window"` for idempotency.

## Step 5 — Per-schema adapter (`schema_adapter.py`)

The single locus of IFC2X3 / IFC4 / IFC4X3 divergence (and the flagged place a per-schema
standardized procedure may later live): surface-style wrapping (`IfcPresentationStyleAssignment`
in 2X3 vs direct), `PredefinedType`/`PartitioningType` availability, property-set value types,
and `IfcWindowType` vs `IfcWindowStyle`. Every author-side helper degrades gracefully (skip + log,
never abort).

## Step 6 — verify() + tester

Built-in `verify()` re-opens src+out and asserts: non-window counts/GlobalIds/fill-void edges
unchanged; openings unmoved; rebuilt windows keep placement; face-plane world-bbox drift ≤ 20 mm;
every new item styled; `validate` errors ≤ source. The separate tester
(`test_window_converter_v2.py`) re-derives invariants independently and **manipulates** each
rebuilt window (kernel-free analytic bbox), with teeth (baked originals must fail) + pinned
baselines.

---

## Files

| File | Role |
|---|---|
| `generate_goldens.py` → `golden_templates/*.ifc` | Authors the 7 reviewable golden templates |
| `golden_geometry.py` | **Shared** parametric recipe (goldens + converter) |
| `classify_window.py` | Name → recipe |
| `schema_adapter.py` | Per-schema quirks |
| `IFC_window_converter_V2.py` | Main converter (`-WIN2`) + `verify()` |
| `test_window_converter_v2.py` | Automated manipulability tester |

## Results (all 4 real ADUs)

| Fixture | Schema | Windows | Rebuilt | Gated/Skipped |
|---|---|---|---|---|
| LEXFORD_OFFICE-C1 | IFC2X3 | 7 | 6 | 1 trapezoid |
| SAN_JUAN_CYPRESS…-W1-L1 | IFC4X3 | 6 | 5 | 1 skylight |
| Sunflower_A | IFC2X3 | 5 | 4 | 1 bodiless "Square Opening" |
| Turnberry…-C1 | IFC4 | 8 | 8 (6 single + 2 double-H) | 0 |

`verify()` ALL CHECKS PASSED on all 4 · 0 new validate errors · tester 4/4 (394 checks), teeth verified.

## Open / deferred

- Adjacency-merge of two single windows into one DOUBLE (no fixture needs it; flag if seen).
- `HandFlipped`/`FacingFlipped` derivation from a real source signal (default False today).
- Skylight + trapezoid parametric templates (add goldens + un-gate when FormX defines them).
- Per-schema instantiation may want divergent procedures (`schema_adapter` is the locus).
