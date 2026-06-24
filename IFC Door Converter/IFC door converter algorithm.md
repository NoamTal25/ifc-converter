# IFC Door Converter — Algorithm & Test Specification

> **Living document.** Defines, step by step, what the door converter does and how to verify
> it. Modeled on `../IFC Window Converter/IFC window converter algorithm.md` and the §2
> "CONVERTER RECIPE" / §3 "Building the next converter" in `../CLAUDE.md`. Kept in sync as the
> algorithm evolves. Last updated: **2026-06-24**.
>
> **Implementation:** `IFC_door_converter_V1.py` — **v2.1 shipped: modular, classification-driven,
> faithful parametric rebuild** (single frame layer = lining + panes + dividers + canonical handle,
> composed by `_assemble` from a per-class recipe; glazed doors measure their member layout 1-1).
> Built + tester 4/4, teeth-verified, on all 11 ADU doors.

---

## 1. Purpose

Turns a Revit-exported ADU IFC into a "FormX IFC" as far as its **doors** are concerned —
every other element (walls, windows, slabs, roof, openings, spaces, storeys, …) is left
exactly as-is. This is one stage in a multi-element pipeline (a sibling to the window converter
and to Gal's walls cleanup / levels organizer / floors definer).

Real exports bake each door's geometry into a frozen mesh/Brep (or a bundle of extrusions)
carried on a shared `IfcRepresentationMap`. There is no live "width" for FormX's chat
manipulation ("make it wider") to grab. This stage rebuilds each door's **Body** as a
**clean, fully parametric assembly whose form is chosen by the door's classification** (Step 2)
and **whose dimensions, colors, and properties are taken from the original** — so the result is
manipulatable *and* faithful, while preserving the door's identity (`GlobalId`), placement, and
every relationship it participates in.

**Design goal — "faithful + fully parametric" (Path A).** The output should look like the door
did before (same class, overall size, panel layout, glazing, colors, a handle in the right
place) **and** be 100% parametric. These two cannot *both* be pixel-perfect for baked input:
looking *exactly* like before means keeping arbitrary original geometry; being parametric means
regenerating the shape from parameters, which yields clean canonical shapes, not the original
mesh. So we target **faithful, not pixel-identical**:
- **Overall dimensions are matched exactly** — width / height / depth are *measured from the
  original* and reproduced (the baked door carries them in its bbox + `OverallWidth/Height`);
  the lining-member thickness is measured too where readable, else a clean default.
- **Colors and properties are preserved** — surface styles harvested per part and re-applied;
  `Pset`s carried forward untouched.
- **Part shapes are canonical** — clean swept rectangles for frame/panel/mullion; a standard
  handle from a small library. A moulded stile becomes a clean rectangle of the same extent; an
  ornate knob becomes the standard lever. This is the same promote-not-preserve tradeoff already
  shipped for windows.

**Why not literal-exact:** pixel-exactness of a moulding profile is fidelity that "make it
wider" never drives and FormX never manipulates; preserving it would require keeping the
non-parametric mesh, defeating the purpose. *(If a viewer demo ever needs literal match, the
fallback is a dual representation — keep the original mesh + attach the parametric recipe and let
FormX regenerate on edit — gated on the open question of whether FormX regenerates from a recipe
or drives geometry directly; see CLAUDE.md §1.)*

The full IFC parametric door schema (`IfcDoorType.OperationType` + `IfcDoorLiningProperties` +
N × `IfcDoorPanelProperties`) is the standardized "golden-target" for naming these parameters.
v2 authors clean parametric **geometry** matched to the original; authoring those **property
sets** as the source-of-truth recipe is an optional enrichment (promote if FormX consumes named
door parameters — see §9).

## 2. How to run

```
python3.11 IFC_door_converter_V1.py                 # batch: INPUT_IFC_FILES_HERE → OUTPUT_IFC_FILES_HERE
python3.11 IFC_door_converter_V1.py <in.ifc>        # single file → OUTPUT_IFC_FILES_HERE/<in>-D1.ifc
python3.11 IFC_door_converter_V1.py <in.ifc> <out.ifc>
```

- **Input:** a Revit-exported `.ifc` (schema-tolerant — must handle IFC2X3, IFC4, IFC4X3).
- **Output:** a copy with `-D1` before the extension, written to `OUTPUT_IFC_FILES_HERE/`.
  Stage suffix `-D1` is free and composes with `-W1`/`-L1`/`-F1`/`-WIN1`.
- **The original is never modified.** All edits happen on the copy.
- Dependency: **`ifcopenshell` only** (tested on 0.8.5) — self-contained, no project-internal
  imports. (Style labelling is an inline keyword scan; see Step 2.)

## 3. Core principle — only the doors change

1. **Non-door elements are untouched.** Counts of every non-door element and relationship type
   are identical before and after (see §5).
2. **Identity preserved.** Each door keeps its `GlobalId` and `ObjectPlacement`; only its
   **Body** `Representation` (and `Name` / `Description` / `PredefinedType`) change.
3. **Relationships preserved.** The `IfcRelFillsElement → IfcOpeningElement →
   IfcRelVoidsElement → IfcWall` chain and `IfcRelContainedInSpatialStructure` are never
   touched. Openings do not move, so doors stay located.
4. **Rebuilt geometry occupies the original's box.** The new frame+leaf is sized and centred on
   the door's own measured extents, so its world position/footprint matches the original within
   tolerance — it still fits its hole, and its bottom still meets the floor (we measure where
   the door actually is; we never *assume* a sill of 0).
5. **The `FootPrint` (2D plan) representation is left untouched.** Only `Body` is swapped. The
   FootPrint is a 2D symbol unrelated to 3D manipulability — preserving it honors "change as
   little as possible."

## 4. What real doors look like (grounding scan)

A `by_type` scan of `INPUT_IFC_FILES_HERE/*.ifc` (schema-safe `try/except`) found **11
`IfcDoor`** (no `IfcDoorStandardCase`):

| File | Schema | Units | Doors | Body geometry (under mapped items) |
|---|---|---|---|---|
| `LEXFORD_OFFICE-C1` | IFC2X3 | ft | 2 | FacetedBrep, ExtrudedAreaSolid |
| `SAN_JUAN_CYPRESS…-W1-L1` | IFC4X3 | ft | 1 | FacetedBrep |
| `Sunflower_A` | IFC2X3 | ft | 3 | ExtrudedAreaSolid, FacetedBrep |
| `Turnberry…-C1` | IFC4 | ft | 5 | ExtrudedAreaSolid, AdvancedBrep |

Confirmed facts the algorithm relies on:
- **Same opening chain as windows.** Every door fills an `IfcOpeningElement` voided into a wall
  (`IfcRelFillsElement → IfcOpeningElement → IfcRelVoidsElement → IfcWall`/`IfcWallStandardCase`),
  contained in an `IfcBuildingStorey`. The window converter's preservation pattern transfers
  directly.
- **Same three geometry kinds** the window converter already handles — Body lives on a shared
  `IfcRepresentationMap` instanced via `IfcMappedItem`, wrapping `IfcFacetedBrep`,
  `IfcExtrudedAreaSolid`, or `IfcAdvancedBrep`.
- **All files are in feet** (unit scale 0.3048) — but never assume; read the scale per file.
- `OverallWidth`/`OverallHeight` are populated on all doors; `PredefinedType` is `DOOR` or null;
  `OperationType` is sometimes set (`SINGLE_SWING_RIGHT`, `DOUBLE_DOOR_SINGLE_SWING_OPPOSITE_RIGHT`,
  `NOTDEFINED`) but inconsistent → treat as cosmetic.
- Type objects are `IfcDoorStyle` (IFC2X3) / `IfcDoorType` (IFC4/4X3) — preserved untouched.
- **A `FootPrint` (2D) representation** sits alongside `Body` on most doors — left as-is (§3.5).
- Style hint lives in the Revit family **Name** (sliding, pocket, flush, double, full-glass,
  four-fold) — cosmetic keyword scan only, like windows.

## 5. Algorithm (in execution order)

For each `IfcDoor` in the copied model:

### Step 0 — Skip if already converted *(idempotency)*
If `Description == "FormX-D1 parametric door"`, the door was produced by a previous run — skip
it. Re-running the tool adds nothing.

### Step 0b — Rectangularity gate *(don't flatten odd shapes)*
Compute the door's **face-fill ratio**: the convex-hull area of its face silhouette ÷ its
bounding-rectangle area (~1.0 for a true rectangle). If it is below `FILL_MIN` (0.95) the door
is **non-rectangular** (arched top, angled, etc.) — the neutral rectangular template would
flatten it, so **leave its original geometry untouched** and flag it (`[keep]`). Most ADU doors
are plain rectangles, so this rarely fires, but the gate is kept for safety. (Faithful rebuild
of non-rectangular doors is Phase 2.)

### Step 1 — Measure the door in its own local frame (dimensions come from the original)
Read the door's **Body** geometry with the IfcOpenShell kernel in **local** coordinates
(`use-world-coords = False`) and take its axis-aligned bounding box. The kernel returns metres
regardless of file units, so divide by `ifcopenshell.util.unit.calculate_unit_scale` to get the
box in the file's own units (ft / mm / m — never assumed). This sidesteps every exporter's
local-axis convention: the box's **thinnest axis is the through-wall depth**; the other two span
the door face (width × height). The bottom of the box is where the door meets the floor — so the
rebuild lands at the real sill, no z-assumption needed.

**This is the fidelity guarantee:** the rebuilt door's **overall width / height / depth are these
measured extents, reproduced exactly** — the output door is the same size as the original (verified
in §6 by ~0 mm world-bbox drift; a few mm on Breps is mesh-bevel noise in the *measurement*, not a
resize). Cross-check against `OverallWidth`/`OverallHeight` when present.
**Member layout — measured 1-1 for glazed doors** (`_measure_layout`): from the **transparent
(glass) sub-solids** (the same transparency signal the style harvest uses, so it's extent-
measurement, not semantic mesh inference) we read each glazed pane's real extent, cluster them into
P panes along the divide axis, and return the **pane intervals + the frame border** = (door face −
glazed-opening) / 2. The rebuild (Step 3) is a **single frame layer** — outer lining + panes +
dividers — so the measured border is the lining `WallThickness` *directly* (LEXFORD full-glass
double → ~6.4″, no doubling), and the pane widths + mullion gaps come from the measured layout
(honours uneven splits / sidelights, not even-tiling). Opaque doors have no glass to measure → they
fall back to even-tiled panes at the `FRAME_THK` default; the border is clamped to a sane fraction.

### Step 2 — Classify the door → choose its rebuild methodology
A keyword scan over the door/type family **Name** (plus the structural `OperationType` enum, see
below) maps the door into a comprehensive taxonomy **grounded in the IFC standard's own
enumerations** (`IfcDoorTypeEnum` + `IfcDoorTypeOperationEnum`, verified from the IFC4X3 spec) —
not just the handful of styles our ADUs happen to contain. **The classification is the dispatch:
it selects which parametric rebuild gets built** (Step 3), so different door classes get
different geometry — a single slab, two leaves + a central mullion, four bi-fold leaves, stacked
sectional panels, etc. It produces four outputs:
- the canonical **Name** label *(cosmetic)*,
- the **`PredefinedType`** *(cosmetic — `hasattr`-guarded; IFC2X3 `IfcDoor` has none)*,
- a **glazed-vs-opaque** flag *(cosmetic — picks which harvested style lands on the panes)*,
- a **rebuild plan** = `{panels, arrangement, hardware}` *(**structural** — drives Step 3's
  geometry; see Layer D)*.

> **Correction to an earlier assumption.** The first draft of this spec assumed "real exports
> rarely populate structured operation enums." That is **wrong for doors** — the `OperationType`
> enum *is* machine-declared (on the `IfcDoorStyle`/`IfcDoorType`, sometimes the instance): e.g.
> `DOUBLE_DOOR_SINGLE_SWING_OPPOSITE_LEFT/RIGHT` on the full-glass doubles, `SINGLE_SWING_LEFT/RIGHT`
> on the singles. So the **panel count is taken from the authoritative enum when present**, and
> falls back to the Name (`2_Panel`, `four_fold`, `double`, `pocket`, …) when it is `NOTDEFINED`.
> We still do **not** author `OperationType` back onto the output (handedness stays deferred, §9).

The scan is layered (first match wins per layer; case-insensitive over the family Name):

**Layer A — macro category → `PredefinedType` (all 5 `IfcDoorTypeEnum` values):**

| `PredefinedType` | Keyword triggers |
|---|---|
| `DOOR` *(default)* | anything door-like not matched below |
| `GATE` | "gate" (but not "boom gate") |
| `TRAPDOOR` | "trapdoor", "trap door", "hatch", "attic/floor/ceiling access" |
| `BOOM_BARRIER` | "boom", "boom gate", "barrier" |
| `TURNSTILE` | "turnstile" |

**Layer B — operation family → canonical Name label + representative operation enum**
(groups all 23 `IfcDoorTypeOperationEnum` values into 12 human-readable families; the Name keeps
the trailing Revit element id like the window converter, e.g. `Sliding door: 539050`):

| Name label | Keyword triggers | Representative `IfcDoorTypeOperationEnum` |
|---|---|---|
| Single door | "single", "flush", "hinged", "swing", "panel" *(default)* | `SINGLE_SWING_RIGHT` |
| Double-acting door | "double-acting", "double acting", "double swing" | `DOUBLE_SWING_RIGHT` |
| Double / French door | "double", "french", "pair" | `DOUBLE_DOOR_SINGLE_SWING` |
| Swing + fixed-panel door | "fixed panel", "sidelite", "sidelight", "swing fixed" | `SWING_FIXED_RIGHT` |
| Sliding door | "sliding", "slider", "barn", "patio", "bypass" | `SLIDING_TO_RIGHT` |
| Double sliding door | "double sliding", "double-sliding" | `DOUBLE_DOOR_SLIDING` |
| Pocket door | "pocket" | `SLIDING_TO_RIGHT` |
| Folding / bi-fold door | "bifold", "bi-fold", "folding", "fold", "four_fold", "accordion", "multifold" | `FOLDING_TO_RIGHT` |
| Double folding door | "double folding", "double-fold" | `DOUBLE_DOOR_FOLDING` |
| Revolving door | "revolving" | `REVOLVING` |
| Rolling / overhead door | "rolling", "roll-up", "rollup", "overhead", "garage", "sectional", "coiling", "shutter" | `ROLLINGUP` |
| Lifting / up-and-over door | "lifting", "up-and-over", "up and over", "tilt" | `LIFTING_VERTICAL_LEFT` |

The remaining `IfcDoorTypeOperationEnum` values — `SINGLE_SWING_LEFT`, `SLIDING_TO_LEFT`,
`FOLDING_TO_LEFT`, `DOUBLE_DOOR_SINGLE_SWING_OPPOSITE_LEFT/RIGHT`, `DOUBLE_DOOR_DOUBLE_SWING`,
`DOUBLE_DOOR_LIFTING_VERTICAL`, `LIFTING_HORIZONTAL`, `LIFTING_VERTICAL_RIGHT`,
`REVOLVING_VERTICAL`, `SWING_FIXED_LEFT`, plus `USERDEFINED`/`NOTDEFINED` — are the
left/right/opposite **handedness** variants of the families above. v1 does not resolve handedness
from a baked mesh, so it picks the family's representative value; the full 23-value list is
recorded here so Phase 2 can resolve handedness from the panel geometry.

**Layer C — glazing modifier (orthogonal to operation; drives the Step 3b style harvest):**
"glass", "glazed", "full glass", "storefront", "vision lite" → **glazed panes** (the most
transparent harvested style goes on the panes); otherwise **opaque panes**.

**Layer D — rebuild plan `{panels, arrangement, hardware}` (the structural output → Step 3):**
- **panels** (leaf/section count): `DOUBLE_DOOR_*` or `SWING_FIXED_*` enum → 2; any other defined
  enum → 1; else from the Name — `four_fold`/"4 panel" → 4, "3 panel" → 3, `double`/`french`/
  `2_panel`/`bi-parting` → 2, generic `folding`/`bi-fold` → 2, otherwise → 1.
- **arrangement**: `side-by-side` (leaves split the **width**, divided by vertical mullions) for
  every family except **Rolling / overhead** and **Lifting**, which are `stacked` (sections split
  the **height**, divided by horizontal rails; default 4 sections). This is the place the rebuild
  *methodology* forks; new methodologies (e.g. revolving vanes) slot in here.
- **hardware**: which canonical handle to stamp (Step 3c) and where —
  - swing / French / double-acting → **lever**, on the handed side (`*_LEFT/RIGHT` → that side;
    a `DOUBLE_DOOR_*` gets one per leaf, meeting at the mullion; handedness unknown → default right),
  - sliding / pocket / bi-fold → **flush bar pull** centred on the active leaf,
  - rolling / overhead / lifting → **none**.

**Coverage check against the 11 real ADU doors:** Single-Flush → Single door (opaque);
Double-Full-Glass → Double/French (glazed); DOOR_SLIDING → Sliding; Double-Sliding → Double
sliding; Pocket → Pocket; four_fold → Folding. All map cleanly — and the taxonomy extends well
beyond them to gates, trapdoors, revolving/rolling/overhead/lifting, etc.

### Step 3 — Author the clean parametric body per the rebuild plan
**Modular build, one frame layer.** The body is assembled from a small library of part-modules —
`_build_handles`, `_door_depth`, plus the inline lining/pane/divider builders — that an **assembler**
(`_assemble`) selects and parameterizes from the Step-2 recipe + the measured layout. Different
classes share the modules and differ only in the recipe; adding a class = a new recipe, not a new
monolith. Each part is a `(solid, role)` tuple (roles `frame` / `pane` / `divider` / `handle`);
`_apply_styles` is role-keyed.

The rebuild is **a single frame layer** — outer lining + panes + dividers (no nested per-leaf
sub-frames):
- one **hollow lining** (`IfcRectangleHollowProfileDef`) over the full face, `WallThickness` = the
  measured border (glazed) or the default (opaque);
- **panes** tiling the inner opening along the **divide axis** — widths from the measured layout
  (glazed: real pane proportions / uneven splits) or even (opaque);
- **divider bars** filling the gaps between panes (the mullion / rails) — measured or default width.

Each pane is bounded by the lining (outer sides) + dividers (inner) → it still reads as a framed
leaf, but with **one** frame layer, so the lining border equals the measured value with no doubling.
A flush single → lining + one pane; a French/double → lining + 2 panes + central mullion; a
four-fold → lining + 4 panes + 3 mullions; plus the handle(s) from Step 3c.

**Per-class recipe (what the assembler composes):**

| Class | depth | panes (divide axis) | layout | handle |
|---|---|---|---|---|
| Single flush | measured | 1 | even / measured | lever |
| Single panel / swing | measured | 1 | measured if glazed | lever (handed) |
| French / double | measured | 2 + mullion | measured if glazed | lever ×2 at mullion |
| Sliding / pocket | measured | 1 | measured if glazed | flush-pull |
| Double-sliding | measured | 2 + mullion | measured if glazed | flush-pull ×2 |
| Four-fold / bi-fold | **clamped** | N + mullions | measured if glazed | flush-pull |
| Overhead / sectional | measured | N stacked + rails | even | none |

(*layout* = pane widths + divider gaps: taken from the original's glazed sub-solids when present
(1-1), else even-tiled. Opaque doors have no glass to measure → even.)

**Axis roles are solved empirically, not assumed.** The measured box's thinnest axis is the
through-wall **depth**; of the two face axes, the one whose **world** direction is most vertical
(largest |Z| in the door's placement) is the **height**, the other the **width**. `side-by-side`
plans divide along the width (vertical mullions); `stacked` plans divide along the height
(horizontal rails). Every solid is extruded along the depth axis via an `IfcExtrudedAreaSolid`
whose placement maps profile-local +Z onto it — so the whole body lands in the wall plane at any
rotation, occupying the same box the original did (world position preserved). Frame/divider width
and pane thickness are config constants in **metres**, converted to file units per file.

### Step 3b — Carry forward surface styles (pane / frame appearance)
Before swapping the Body, **harvest the original door's `IfcSurfaceStyle`s** by walking its
(mapped) Body items' `IfcStyledItem.Styles` and bucketing by transparency: the most transparent
→ **pane glazing** (full-glass doors), the opaque → **frame**. Re-attach them to the new solids
(`IfcStyledItem` on each: pane style on every pane, frame style on the lining frame **and** the
dividers), reusing the existing style entities verbatim — identical appearance and schema-correct
for IFC2X3 (which wraps styles in `IfcPresentationStyleAssignment`) vs IFC4/4X3 (direct). If a
door has only one style, use it for both. Fallback to an authored glass+frame pair only if the
door carried no styles. **Without this the rebuilt items render solid gray** (the regression the
window converter hit in Blender).

### Step 3c — Stamp the canonical handle (from the rebuild plan's `hardware`)
The original's handles/hinges are arbitrary freeform geometry with no parametric IFC schema, so
rather than preserve baked meshes (non-parametric, won't track on resize) we author a **single
canonical handle component**, reused on every door — the same promote-not-preserve move used for
the frame. A small library keyed to `hardware`:
- **lever** — a small "rose" + a horizontal lever bar (2–3 swept solids),
- **flush bar pull** — a vertical bar on two standoffs (3 boxes),
- **none** — omit (overhead/sectional).

The handle is placed parametrically in the door's local frame: at standard lever height up from
the sill, offset in from the active leaf's leading edge, and **proud of the leaf face in the depth
axis** — authored on **both faces**. Because its placement is a function of the door's width/leaf,
it **moves and rotates rigidly with the door** (it's in the door's representation under
`ObjectPlacement`) and, since the placement is derived, it tracks the edge on resize without
stretching. It gets the opaque/frame style (or an authored metal default). This makes the handle
present and faithful in position/side without re-introducing any baked geometry.

### Step 4 — Swap the Body representation in place, preserve identity
Inside the door's existing `IfcProductDefinitionShape`, **replace only the `Body`
`IfcShapeRepresentation`** with the new one (matched by entity `.id()`, **not** Python `is` —
ifcopenshell returns a fresh wrapper per `.Representations` access, so `is` silently never matches
and would blow the list away to `()`). **Every non-Body representation — the `FootPrint` — is left
untouched.** Then remove the old per-door Body entities (old shape-representation + mapped item),
first detaching them from any `IfcPresentationLayerAssignment` so none is left with empty `Items`.
The **shared `IfcRepresentationMap`** is never removed — other doors may still map it. Set the
canonical Name, stamp `Description` with the idempotency marker (`FormX-D1 parametric door`), and
set `PredefinedType` from the classification (`hasattr`-guarded).

## 6. Output & built-in verification

After writing the `-D1` copy, `verify()` reopens source and output and asserts:

- **Preservation** — count of every non-door element/relationship type (`IfcWall`, `IfcSlab`,
  `IfcWindow`, `IfcOpeningElement`, `IfcSpace`, `IfcRoof`, `IfcRelFillsElement`,
  `IfcRelVoidsElement`, `IfcRelContainedInSpatialStructure`, `IfcRelDefinesByType`, …) is
  unchanged. `IfcDoor` count is unchanged too.
- **Door GlobalIds preserved** — same set before and after.
- **Openings did not move** — every `IfcOpeningElement` world placement is unchanged
  (`atol 1e-6`), so doors stay located.
- **Door world-bbox drift** — each door's world bounding box matches the original within
  `BBOX_TOL_M` (20 mm); confirms the rebuilt body still fills its hole and meets the floor.
  *(v2 caveat: the canonical handle protrudes proud of the leaf in the depth axis, as the original's
  did. Measure drift on the **face plane (width × height)** — the dimensions that must match — and
  allow the depth envelope to differ by the handle protrusion, or exclude handle solids from the
  drift bbox, so a faithful handle doesn't trip the gate.)*
- **FootPrint untouched** — every door that had a FootPrint representation still has it,
  referencing the same entity.
- **Surface styles present** — every rebuilt door's new Body items carry an `IfcStyledItem`
  (else they would render gray).
- **No new schema errors** — `ifcopenshell.validate` message count on the output is `≤` the
  source's (pre-existing source issues are not blamed on us).

Prints a per-file summary and `RESULT: ALL CHECKS PASSED ✓ / SEE WARNINGS ABOVE ✗`.

> **Note.** The log reports each door's ORIGINAL Body representation kind (e.g.
> `mapped/AdvancedBrep`, `mapped/SweptSolid`, `mapped/FacetedBrep`) so you can see what was
> rebuilt. The manipulability of the *output* is proven by the automated tester
> (`test_door_converter.py`), not by a score.

## 7. Test fixtures & expected results

Reference inputs are the real FormX ADUs in `../INPUT_IFC_FILES_HERE/`. Expected after build:

| Fixture | Schema | Units | Doors | Rebuild (panels) |
|---|---|---|---|---|
| `LEXFORD_OFFICE-C1` | IFC2X3 | ft | 2 | full-glass double → **2-panel** (glazed) + single-flush → 1-panel. FacetedBrep + ExtrudedAreaSolid; no FootPrint. |
| `SAN_JUAN_CYPRESS…-W1-L1` | IFC4X3 | ft | 1 | sliding → 1-panel. FacetedBrep; already through walls+levels. |
| `Sunflower_A` | IFC2X3 | ft | 3 | double-sliding → **2-panel** + 2 pocket → 1-panel. FootPrint preserved on all 3. |
| `Turnberry…-C1` | IFC4 | ft | 5 | single / pocket → 1-panel, **four-fold → 4-panel**, double-sliding & full-glass double → **2-panel**. `OperationType` drives panel count on the doubles; incl. `IfcAdvancedBrep`. |

Idempotency: re-running on a `-D1` output reports `rebuilt=0, already=N`.

## 8. Automated tester (with teeth) — built

`test_door_converter.py` + `DOOR_CONVERTER_TESTING_AGENT.md`, mirroring the window tester:
- Runs the converter on every `INPUT/` fixture in throwaway temps; re-derives the §6 invariants
  **independently** (does NOT call the converter's own `verify()`).
- **Manipulates each rebuilt door** (drive the outer frame profile to resize, move, rotate) and
  asserts the frame border stays constant, the door grows on the driven axis only, panes stay
  inside, the body moves rigidly, and the model stays valid. **Measures rebuilt geometry
  analytically** (bbox from profile dims + placement matrix) — the geom kernel returns
  nondeterministic empty meshes on freshly-authored solids (CLAUDE.md §6).
- Generalised to the panel topology: a rebuilt door = one hollow outer frame + `2·panels−1`
  fills (panes + dividers); the static check is "exactly one hollow frame + ≥1 inset pane, all
  styled" (so 1-, 2- and 4-panel doors all pass).
- **Teeth:** the same manipulability test on the baked originals MUST fail (negative control),
  plus a pinned `BASELINE_REBUILT` door count — verified by pointing the harness at a no-op
  (plain copy), which trips `[F] rebuilt count matches baseline`.
- **Status:** the tester passes **4/4 fixtures (all 11 doors)** against the v2.1 converter, teeth
  re-verified. With the single frame layer the Body has one hollow (the lining); panes/dividers/
  handles are rect fills. The contract stays "outer hollow lining + ≥1 inset pane, all parts
  contained + styled" (the test takes the largest-area hollow as the lining, robustly), and drift is
  measured on the **face plane** (§6). Still **one test for all doors** — class-agnostic contract,
  never per-class shapes.

## 9. Build status, known limitations & deferred

**Built (v2.1, modular, measured-layout):** class-driven assembly composed by `_assemble` from the
recipe — a **single frame layer** (outer lining + panes + dividers, no nested sub-frames) + a
canonical **handle** (lever / flush-pull / none, both faces) + **folding-depth clamp**. For glazed
doors the **member layout is measured 1-1** (border + pane widths + mullion from the transparent
sub-solids, `_measure_layout`); opaque doors fall back to even-tiling at the default border. French
& multi-panel doors keep their mullion, framed-leaf read, and handles. **Deferred / out of scope:**
the rest below.

- **Faithful, not pixel-identical — by design.** Part shapes are clean canonical rectangles +
  a standard handle, sized/colored/placed from the original; a moulded stile → a clean rectangle
  of equal extent, an ornate knob → the standard lever. Overall dimensions, colors, and (for glazed
  doors) the member layout match; fine profile/handle shape does not. (Dual-rep "literal-exact"
  fallback is gated on the FormX-regenerates question — §1.)
- **Glazed doors measured 1-1; opaque even-tiled.** Glazed doors get the real border + pane layout
  from the glass sub-solids (Step 1); opaque doors have no glass to decompose, so they keep the
  even-tiled default (measuring opaque member widths would need fragile per-part mesh analysis).
- **One frame layer — panes are bounded by lining + dividers**, not their own stile/rail
  sub-frame; in-pane glazing bars / muntins aren't modelled. Per-leaf sub-frames and divided-lite
  grids are a further fidelity upgrade.
- **Overall size is the drivable parameter; panes don't auto-reflow.** Driving the outer lining
  resizes the door; panes/dividers don't yet redistribute proportionally (a generator-level layer).
- **Handedness from the enum, not authored back.** Panel *count* and handle *side* come from
  `OperationType`; we don't write `OperationType`/`IfcDoorLiningProperties`/`IfcDoorPanelProperties`
  onto the output (the standard "golden-target" psets — promote if FormX consumes named params),
  nor resolve the full left/right/opposite enum tail.
- **`stacked` arrangement is untested on the corpus.** Overhead/sectional/garage map to stacked
  sections, but no ADU fixture exercises it yet.
- **Non-rectangular doors are preserved, not rebuilt** (Step 0b fill gate); faithful rebuild of
  arched/angled silhouettes is future work.
- **Brep bevels.** For Brep doors the measured face box can differ a few mm from the true hull;
  within the 20 mm tolerance. **Geometry must be readable** — an unreadable Body is `[SKIP]`ped,
  left untouched. **FootPrint is preserved, not rebuilt.**
