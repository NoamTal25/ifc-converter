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
   (HandFlipped / FacingFlipped / **OpeningDirection** — a human-readable hinge-side+swing-sense
   label, e.g. `"Right / Inward"`, `"Sliding Left"`, `"Folding"`, derived from the door's effective
   `OperationType` + FacingFlipped) + `IfcDoorLiningProperties` + per-panel `IfcDoorPanelProperties`,
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

- **Lining** = **4 solid `IfcRectangleProfileDef` bars** (head + sill full-width, two jambs spanning
  the inner height) of width `frame_thk` — **NOT** an `IfcRectangleHollowProfileDef`. Gaudi mis-renders
  the hollow profile (its inner opening renders larger than authored → a uniform pane↔frame "space";
  Blender/openIFC render it flush). Four plain bars render flush everywhere — see CLAUDE.md §6.
- **Per-type modes** select the leaf construction via recipe knobs (each emits a FIXED solid count;
  dims clamp, never drop, so `_expected_item_count` is a clean function of the knobs):
  `pocket` · `shower` · `barn` · `casing` · `combo` · `panelled` (rail-and-stile) ·
  `leaf_frame` (+`muntins` for French) · `sliding` · default (N panes + mullions). Post-steps add
  `bifold` fold-hardware, `astragal`, `hinges`, and `handles`.
- **Reusable sub-assemblies (helpers):** `_border_bars` (3- or 4-sided bar border, `sill=` toggle),
  `_panelled_leaf` (2 stiles + 3 rails + 2 recessed panels — recess = a thinner centred panel solid,
  no boolean/hollow), `_muntin_grid` (lock rail + applied divided-lite bars), `_hinge_stack`
  (n butt-hinge knuckles, one-face proud), `_build_handles` (knob / pull).
- **Scale-correctness:** every linear dimension comes from a `dims` dict **in file units** —
  `dims_in_units(unit_scale)` converts the canonical mm `CANON` table to the file's units. NO
  hard-coded mm in the build path; positional **ratios are inlined literals** (dimensionless) so they
  are never unit-converted. Clamps keep features non-degenerate on small / odd-aspect doors at FEET
  scale (frame ≤ 0.2·min(W,H); stile/rail/mullion/sash ≤ fractions of the leaf; hardware clamped
  inside its leaf; recessed-panel depth = 0.5·leaf depth). A feet-scale stress test (nominal / narrow
  / wide) confirms no degenerate dims across all 16 types.
- **Role → colour bucket** is centralised in `bucket_for` / `_ROLE_BUCKET` (+`GLASS_ROLES`), the
  single source of truth used by BOTH the golden generator and the converter: `frame` (lining /
  stiles / rails / mullions / muntins / casing / astragal / sill / divider), `slab` (wood leaf /
  plank), `metal` (handle / pull / knob / track / roller / hinge / guide / strap / track_guide /
  standoff), `glass` (glazing; also `panel` when glazed).

---

## 4. Per-type design (enriched 2026-06-26 — was a deliberately-simplified first pass)

Each type now carries realistic, previously-omitted components (all solid axis-aligned rect boxes):
- **`DOOR_SINGLE` / `DOOR_INTERIOR_SINGLE`** — rail-and-stile panelled leaf (2 stiles + top/lock/
  bottom rails + 2 recessed panels) + knob; the exterior single also shows 3 hinges (interior omits
  them for a cleaner leaf).
- **`DOOR_SINGLE_FLUSH`** — intentionally plain flush slab, but now hung-looking: 3 hinges + knob.
- **`DOOR_DOUBLE`** — two panelled leaves + an **astragal** over the meeting joint + 3 hinges/leaf + 2 knobs.
- **`DOOR_INTERIOR_DOUBLE`** — two **French** glazed leaves with a **divided-lite muntin grid**
  (mid-height lock rail + 1 vertical + 2 horizontal applied bars over a single transparent lite) +
  3 hinges/leaf + 2 knobs.
- **`DOOR_POCKET`** — opening half (3-sided lining) + leaf retracted into the pocket half + a
  single-face (inward) pull. *(Still flagged: a real pocket pull is recessed.)*
- **`DOOR_SLIDING`** — two framed-glass sashes overlapping at the centre on two depth tracks (mirrors
  the real San Juan Cypress door). ✅ verified in Gaudi.
- **`DOOR_BARN` / `DOOR_SINGLE_BARN`** — ledged plank leaf/leaves (slab + 3 horizontal battens, NO
  diagonal brace) hung from an overhead **track** (+2 end stops) by 2 **strap hangers**/leaf, with a
  floor **guide** and a bar **pull**. *(Still flagged: confirm the 2-leaf-vs-1-leaf BARN split.)*
- **`DOOR_SHOWER`** — semi-frameless: slim pivot **jamb** + low **threshold** + a large thin glass
  lite + 3 pivot **hinge blocks** + a tall **towel-bar pull** standing off the glass on 2 standoffs.
- **`DOOR_BIFOLDING_GLASS` / `…_2_PANEL` / `…_SWING_COMBO`** — per-leaf framed leaves (coplanar) +
  **fold-hinge knuckles** at each leaf joint + a **top track guide** + pull. *(Leaves kept coplanar —
  true out-of-plane articulation is deferred to avoid an in-wall converted door poking through the wall.)*
- **`DOOR_SLIDE_AND_SWING` / `DOOR_SLIDING_SWING_COMBO`** — a central divider with a **framed sliding
  sash** (front track + 2 rollers + floor guide + bar pull) on one half and a **panelled swing leaf**
  (rails + recessed panels + 2 hinges + knob) on the other.
- **`DOOR_OPENING`** — a 3-sided lining + **architrave casing** (head + 2 legs, butt-jointed) on both
  wall faces.

Adversarially reviewed (5 lenses → independent verification): two findings fixed — the barn plank
body role (`panel`→`plank`) and the combo sliding hardware (now FRONT-mounted, not proud of both
faces). All 16 goldens validate clean with matching solid counts; tester 4/4; converter verify ALL
PASS on the 4 ADUs (0 new validate errors, 0.0 mm face drift). **Awaiting Gaudi review of the 15
enriched goldens** (DOOR_SLIDING already confirmed).

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
- HandFlipped / FacingFlipped derivation from the baked placement (currently default False). Once
  FacingFlipped is real, `OpeningDirection`'s swing sense (Inward/Outward) becomes accurate — it
  currently defaults to Inward (the heuristic in `_opening_direction`).
- Fold the door stage into the unified pipeline orchestrator (walls → levels → floors → windows →
  doors), per CLAUDE.md §3.
