# IFC Door Converter v2 — Algorithm & Living Spec

**Method:** golden-template-swap (CLAUDE.md §2a). *classify → instantiate FormX's golden template →
inject the baked door's measured params (dimensions + colours) → swap the Body.* This is the door
analog of the proven window converter v2, built module-for-module. The prior measure-and-rebuild
door converter lives at `reference/IFC Door Converter v1/` (kept as reference).

**Self-contained:** ifcopenshell only. `python3.11` / ifcopenshell 0.8.5. Suffix `-D2`; idempotency
marker `Description == "FormX-D2 parametric door"`.

---

## 1. Pipeline (per `IfcDoor`, in a copied model)

1. **Classify** the door from its `IfcRoot.Name` → one of FormX's 16 door types (`classify_door.py`,
   PDF rules tuned to the real ADU names). The type selects the **golden** + the geometry **recipe**
   (panels / mullions / head-rail / barn-track / handle), the **OperationType**, and the per-panel
   properties.
2. **Measure** the door's own local-frame bbox from the kernel (file units). Axis roles are
   empirical: **thinnest = through-wall depth**; of the two face axes, the **more-vertical one
   (max world |Z|) = height**, the other = width. Orientation-agnostic.
3. **Clamp depth for folding types** (bi-fold / folding-combo): a folded door's measured depth is the
   folded *projection*, not the leaf — clamp to `MAX_FOLD_DEPTH_M = 0.20 m` (§6 gotcha).
4. **Harvest the door's own surface styles** (bucket by transparency → glass / opaque) *before*
   swapping — so the converted door **takes on the baked door's real colours**.
5. **Author the body** through the SHARED recipe `golden_door_geometry.build_door_items(...)` — the
   *same code* that authors the goldens — sized to the measured W·H·D and scaled to file units via
   `dims_in_units(scale)`. Returns role-tagged `(solid, role)` parts.
6. **Apply styles by role** (panel→glass when the door had glass, else opaque; everything else
   opaque), then **swap the Body shaperep only** (matched by `.id()`), preserving `FootPrint`,
   `GlobalId`, `ObjectPlacement`, and the opening→fill→void→host chain + spatial containment.
7. **Author the FormX param apparatus at the OCCURRENCE level** (never a 2nd `IfcDoorType`):
   `Pset_DoorCommon` (Overall/Rough W·H, Depth, Reference, IsExternal) + `FormX_Door_Window`
   (HandFlipped / FacingFlipped) + `IfcDoorLiningProperties` + per-panel `IfcDoorPanelProperties`,
   each via `IfcRelDefinesByProperties`. Re-stamp `OverallWidth/Height`, canonical `Name`, the
   idempotency `Description`, and `PredefinedType=DOOR` on the occurrence (IFC4+).
   **Preserve handedness:** the occurrence `OperationType` is only set to the class-canonical value
   to fill a `NOTDEFINED` gap — a meaningful Revit op (`SINGLE_SWING_RIGHT`,
   `DOUBLE_DOOR_SINGLE_SWING_OPPOSITE_RIGHT`, …) is kept, so the occurrence never contradicts its
   preserved `IfcDoorType` and the real handedness survives.
8. **Gate** non-rectangular (face-fill < 0.95) and bodiless doors → preserve untouched + flag.
9. **`verify()`** (built-in) + a separate **7-layer tester with teeth** (`test_door_converter_v2.py`).
   Layer G is door-specific: it pins the rebuilt **FormX-type multiset** per fixture + each door's
   recipe-implied solid count, so a misclassification (the structural core of the method — a wrong
   class is a wrong golden) is caught even though it preserves counts and manipulability.

---

## 2. FormX door taxonomy (the PDF DOORS section) — 16 types

Single source of truth: **`door_types.py`** (consumed by both `generate_goldens.py` and
`classify_door.py`). Each type → a golden `.ifc` + a geometry recipe.

| FormX type | geometry archetype | panels | glazed | handle | extras | OperationType |
|---|---|---|---|---|---|---|
| DOOR_SINGLE | single-swing | 1 | — | lever | — | SINGLE_SWING_LEFT |
| DOOR_INTERIOR_SINGLE | single-swing | 1 | — | lever | — | SINGLE_SWING_LEFT |
| DOOR_SINGLE_FLUSH | single-swing (flush) | 1 | — | lever | — | SINGLE_SWING_LEFT |
| DOOR_DOUBLE | double-swing | 2+mullion | — | 2 levers | — | DOUBLE_DOOR_SINGLE_SWING |
| DOOR_INTERIOR_DOUBLE | double-swing | 2+mullion | ✓ | 2 levers | — | DOUBLE_DOOR_SINGLE_SWING |
| DOOR_POCKET | pocket-slide | 1 | — | pull | (slides into wall) | SLIDING_TO_LEFT |
| DOOR_SLIDING | sliding | 1 | — | pull | head rail | SLIDING_TO_LEFT |
| DOOR_BARN | barn (2-leaf) | 2+mullion | — | pull | track + 2 rollers | DOUBLE_DOOR_SLIDING |
| DOOR_SINGLE_BARN | barn (1-leaf) | 1 | — | pull | track + 2 rollers | SLIDING_TO_LEFT |
| DOOR_SHOWER | shower glass | 1 | ✓ | pull | slim frame | SINGLE_SWING_LEFT |
| DOOR_BIFOLDING_GLASS | bifold multi | 4+3 mullions | ✓ | pull | flat panels | DOUBLE_DOOR_FOLDING |
| DOOR_INTERIOR_BIFOLDING_2_PANEL | bifold | 2+mullion | — | pull | flat panels | DOUBLE_DOOR_FOLDING |
| DOOR_SLIDE_AND_SWING | combo | 2+mullion | — | 2 levers | split leaf | USERDEFINED (`SLIDE_AND_SWING`) |
| DOOR_SLIDING_SWING_COMBO | combo | 2+mullion | — | 2 levers | split leaf | USERDEFINED (`SLIDING_SWING_COMBO`) |
| DOOR_BIFOLDING_SWING_COMBO | combo bifold+swing | 3+2 mullions | — | pull | flat panels | DOUBLE_DOOR_FOLDING |
| DOOR_OPENING | cased opening | 0 (lining only) | — | none | — | NOTDEFINED |

### Classification rules (`classify_door.py`, PDF priority order; tuned to real names)
`OPENING` · `POCKET` · `BARN(±SINGLE)` · bi-fold (`BIFOLD_DOOR_2` / `four_fold` / generic `fold`) ·
`SLIDING+PLY+GEM`→combo · `SINGLE+FLUSH` · `INTERIOR+DOUBLE` · `INTERIOR+SINGLE` ·
`DOUBLE+EXTERIOR` · `SHOWER` · `SLIDE+SWING` · `SLIDING/SLIDER/BYPASS/PATIO` · bare `DOUBLE` →
default `DOOR_SINGLE`. Glazed = type default OR name contains "glass"/"glazed". `four_fold` → the
4-panel bifold golden (a tuning beyond the PDF, which has no four-fold rule).

**Substring-collision guards** (the bare-keyword rules are footgun-prone on arbitrary Revit names):
- **`opening`** maps to `DOOR_OPENING` only when no leaf-implying word is present (swing/flush/
  sliding/pocket/barn/fold/panel/single/double/…) — so "Sliding_Glass_Opening" / "Self-Opening-
  …-Single" stay leafed doors, not a leafless cased opening.
- **glazing descriptors** ("double glazed", "single pane", …) are scrubbed before the leaf-count
  rules, so "…-Double-Glazed-…" doesn't inflate a single door to a double (while a real
  "Interior-Double-Full Glass" keeps its leaf `double`).

**DEFERRED (PDF, needs geometry):** "two doors side-by-side sharing one opening's bbox →
`DOOR_BIFOLDING_SWING_COMBO`" is an adjacency rule the name alone can't decide. No such pair occurs
in the grounding ADUs; left for a future adjacency pass.

---

## 3. Geometry recipe (`golden_door_geometry.py`)

The single shared recipe, used by BOTH the golden generator and the converter → a converted door is
provably identical to its golden, scaled. Coordinate convention: profile **X=width, Y=height**,
extruded along **depth** (through-wall); body centred on `center`; `depth_dir`/`width_dir` supplied
by the caller (axis-aligned for the golden, measured axes for an instance).

- **Lining** = `IfcRectangleHollowProfileDef` (full W·H, full depth); `WallThickness` = the single
  **drivable frame-border parameter** → "make it wider" = set the lining `XDim`.
- **Panels** = inset `IfcRectangleProfileDef` leaves; **mullions** between panels; **head rail**
  (sliding) / **barn track + 2 rollers** (barn); **handles** (lever / pull) as boxes proud of both
  faces. `panels=0` → lining only (cased opening).
- **Scale-correctness:** every linear dimension comes from a `dims` dict **in file units** —
  `dims_in_units(unit_scale)` converts the canonical mm `CANON` table to the file's units. NO
  hard-coded mm in the build path (a 130 mm lever is the right fraction of a foot at feet scale, not
  130 feet). Clamps keep features non-degenerate on small / odd-aspect doors (frame ≤ 0.2·min(W,H);
  mullion ≤ 0.5·inner_w/n so panes stay positive; handles clamped inside their leaf).

---

## 4. First-pass simplifications & viewer-review items (refine after architectural review)

Decision #3 (clean & simplified first pass) — recorded so we tune after viewer review:
- **Bi-fold / combo panels are flat & coplanar** (NOT articulated/folded). "side_by_side" just
  divides the inner width into N panels with mullions.
- **Barn** = leaf + a straight overhead track bar + 2 roller tabs (rollers overlap the track band so
  they read as connected). **DOOR_BARN is modelled as 2 leaves on one track** vs DOOR_SINGLE_BARN's
  1 leaf — *confirm the 1-leaf-vs-2-leaf interpretation of the PDF "BARN" / "BARN+SINGLE" split.*
- **Sliding** = leaf + a head-rail bar across the top of the opening.
- **Lining is a full 4-sided hollow rectangle** (real frames are usually 3-sided / no sill).
- **DOOR_POCKET's pull is proud of both faces**; a real pocket pull is recessed.
- **DOOR_OPENING** = a cased opening (lining frame only, no leaf/handle).

---

## 5. Grounding reality (the 4 ADUs)

11 `IfcDoor` across IFC2X3 / IFC4 / IFC4X3 (all feet), each filling an `IfcOpeningElement` voided
into a wall. Real names → types: `Single-Flush`→DOOR_SINGLE_FLUSH (×2); `Door-Interior-Double-Full
Glass`→DOOR_INTERIOR_DOUBLE glazed (×2); `…Pocket_door…`→DOOR_POCKET (×3); `DOOR_SLIDING` &
`Door-Double-Sliding`→DOOR_SLIDING (×2); `Door-Interior-Double-Sliding-2_Panel`→DOOR_INTERIOR_DOUBLE
(PDF INTERIOR+DOUBLE priority); `four_fold…`→DOOR_BIFOLDING_GLASS (×1). The other 10 PDF types are
**catalog-only** (golden authored + reviewable, but no real instance yet).

---

## 6. Result (current)

- Goldens: 16/16 author clean — **0 `ifcopenshell.validate` errors each**, all carry
  `Pset_DoorCommon` + `FormX_Door_Window` + `IfcDoorType` (correct OperationType) + per-panel props,
  0 unstyled items.
- Converter: **all 11 doors rebuilt** across the 4 ADUs (2 / 1 / 3 / 5), `verify()` ALL CHECKS
  PASSED, **0 new validate errors**, 0.0 mm face-bbox drift, GlobalIds/placements/FootPrint
  preserved, idempotent.
- Tester: **4/4 fixtures, 276 checks, teeth verified** — no baked original was already manipulable;
  rebuilt counts AND the FormX-type multiset match the pinned baseline (forcing every door to one
  type, which previously slipped through, now FAILS layer G).

## 7. Open / next
- User + FormX-architecture viewer review of the 16 goldens & the `-D2` outputs (the agreed ground
  truth); refine the §4 simplifications per feedback.
- Adjacency pass for `DOOR_BIFOLDING_SWING_COMBO` (side-by-side doors).
- HandFlipped / FacingFlipped derivation from the baked placement (currently default False).
- Fold the door stage into the unified pipeline orchestrator (walls → levels → floors → windows →
  doors), per CLAUDE.md §3.
