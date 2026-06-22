# IFC Walls Cleanup — Algorithm & Test Specification

> **Living document.** Describes, step by step, what the walls cleanup does and how to verify
> it. Kept in sync as the algorithm evolves. Last updated: **2026-06-22**.
>
> **Implementation:** `IFC_walls_cleanup_V1.py`

---

## 1. Purpose

Cleans a Revit-exported IFC into a clean "Gaudi IFC". Gaudi IFC is built by several cleanups,
each handling certain elements. **This cleanup handles the WALLS only** — every other element
(doors, windows, slabs, roof, openings, spaces, materials, storeys, …) is left exactly as-is.

The output is a tidy set of walls: each wall a clean vertical extrusion, classified and named
by location, with clean butt-jointed corners and no overlaps in plan — while preserving each
wall's true shape (thickness, height, sloped/stepped tops) and the rest of the model.

## 2. How to run

```
python3 IFC_walls_cleanup_V1.py                 # cleans every IFCs/*.ifc → *-W1.ifc
python3 IFC_walls_cleanup_V1.py <in.ifc>        # → <in>-W1.ifc
python3 IFC_walls_cleanup_V1.py <in.ifc> <out.ifc>
```

- **Input:** a Revit-exported `.ifc` (the script is schema-tolerant; fixtures are IFC4X3).
- **Output:** a copy with `-W1` inserted before the extension, e.g.
  `IFCs/SAN JUAN CYPRESS - AUG 2.ifc → IFCs/SAN JUAN CYPRESS - AUG 2-W1.ifc`.
- **The original file is never modified.** All edits happen on the copy.
- Dependency: `ifcopenshell`.

## 3. Core principle — preserve everything except the walls' plan corners

1. **Non-wall elements are untouched.** Counts of every non-wall type must be identical
   before and after (see §7).
2. **Wall shape is preserved.** A wall keeps its profile, thickness, height and any
   `IfcBooleanClippingResult` (sloped/gable/clipped top). Clips are **never dropped**;
   profiles are **never flattened or re-centred** for their own sake.
3. **Only walls divided at a corner change geometry**, and only by: (a) shortening length to
   butt a neighbour, and (b) squaring a mitered/arbitrary corner end to a rectangle when
   required for a clean joint (the clipped top is kept). **Walls not in any corner — including
   all interior walls with no junction — are left exactly as-is.**
4. **Openings stay put.** Doors/windows keep their original world position (see §7).

---

## 4. Algorithm (in execution order)

Let `walls = IfcWall ∪ IfcWallStandardCase` (de-duplicated). For each wall the script reads its
plan geometry from the **Axis** polyline (run direction + length) and the **Body** (thickness,
top profile). The Body may be a plain extrusion, a clipped extrusion, a Brep mesh, or several
solids — see §6.

### Step 0 — Convert glass/shower walls → furniture
Revit models shower screens and partition glass as thin **walls** (name contains `SHOWER` or
`GLASS`, e.g. `…INT WALL - SHOWER:834582`, `Generic Glass - 1/2"`). These are not structural
walls, so each is **re-typed to `IfcFurniture`** (an `IfcFurnishingElement` glass panel; falls
back to `IfcFurnishingElement` on IFC2X3). The re-typing is lossless — same GlobalId, placement,
Body geometry, glass material and spatial containment — and only drops wall-only data: the `Axis`
representation, the wall `PredefinedType`, and any openings. The element is renamed
`Shower glass: <id>`. **Done first**, and the converted walls are removed from `walls`, so they
are never classified, divided, merged or counted as walls. No-op for files with no glass walls.

### Step 1 — Verify vertical extrusion *(report only)*
Check each wall's base solid is extruded straight up **+Z**. This step does **not** modify
anything (no promoting, flattening or rectangularizing). Walls not extruded along +Z are
reported and left as-is.

### Step 2 — Classify & rename
Classify each wall by **location** from how many of its two long faces are open to the exterior
(ray-cast from each face midpoint against the other walls' footprints):

| Exposed long faces | Class      | Rename to              |
|--------------------|------------|------------------------|
| 1                  | `exterior` | `Exterior wall: <id>`  |
| 0                  | `interior` | `Interior wall: <id>`  |
| 2                  | `design`   | `Design wall: <id>`    |

`<id>` is the trailing Revit element id from the original name (e.g. `Exterior wall: 628797`).
A **skew** wall (long faces not within 15° of an axis) falls back to the Revit name keywords
(`EXT`/`INT`/`DESING|DESIGN`).

**Projecting-stub refinement.** A short stub wall (length ≲ thickness) initially classed
`exterior` but whose footprint **protrudes past the envelope of the long perimeter walls** is
re-classified `design` (a free-standing projecting fin). This catches fins that the exposed-face
test mislabels — e.g. two fins on the same line each ray-cast into the *other* across the open
gap and read that face as enclosed. Only short stubs are considered and only `exterior→design`
is ever changed, so real (long) perimeter walls are never affected (e.g. Northam's two 1-ft
`Generic-12"` fins below the south edge → `design`).

### Step 3 — Detect outer faces
Compute the building centroid (mean of wall-axis midpoints). For each wall, the long face
**farther from the centroid** is its **outer face**. (Used by the corner division.)

### Step 4 — Divide walls (clean corners, no overlaps)

Corners are resolved **per storey** — walls stacked on different floors (which share a plan
footprint) never count as meeting each other.

**4a. Extend perimeter walls to their outer corners (pre-pass).** Walls modelled in Revit as
clean butt joints only *touch* at corners, so an overlap-only divider would leave them as
Revit arranged them — and one wall could end up owning *both* corners while its neighbour owns
none. To prevent this, each perimeter (exterior/design) wall is first **extended** so both ends
reach its outer corners (the outer-face line of the perpendicular neighbour met at each end).
This makes every corner overlap by one thickness, so the pinwheel below always engages and
**every perimeter wall ends up owning exactly one corner**. (Mitered/Brep/multi-solid walls are
squared/rebuilt first so they can be extended; on a start-end extension openings are
compensated, as for trims.)

**4b. Pinwheel division.** Find every pair of same-storey **perpendicular** walls whose
footprints overlap (a corner / T-junction). At each, one wall **owns** the corner (its body
fills the corner square and reaches the outer corner) and the other **butts** it (its corner-end
is trimmed back to the owner's near face). Ownership rules, in priority order:

1. **Perimeter-to-perimeter corners → alternating pinwheel.** Each wall is oriented along a
   consistent counter-clockwise loop around the centroid; it owns one loop-end corner and
   butts the other. Going around, ownership alternates and **every perimeter wall is trimmed
   exactly once** (a 9×13 box → 8.5 / 12.5 / 8.5 / 12.5). The loop is derived from each wall's
   **position**, not its Revit export orientation, so it is orientation-agnostic.
   - There are two mirror pinwheels (butt at the loop-forward vs loop-backward end). The
     script picks the one that (a) never trims a **continued end**, then (b) trims more
     **far** ends. A *continued end* is where the wall runs into a collinear neighbour on the
     same line (e.g. an exterior wall continued by a design wall) — trimming it back would let
     the perpendicular wall protrude past it, so that end must reach the junction.
2. **Pairs involving an interior wall (interior corner / T-junction) → "longer wall owns".**
   The interior/shorter wall is trimmed to butt the wall it meets. (The pinwheel applies only
   to perimeter-to-perimeter corners.)
3. **No clean loop role → fallback "longer wall owns"** (ties broken by lower entity id).

Trimming details:
- A trim only shortens the wall's run length. A **far-end** trim leaves the placement origin
  (and clipped top) untouched. A **start-end** trim shifts the origin along the run by the trim
  (~one thickness); the wall's **openings are compensated** so doors/windows stay at their
  original world position, and the clipped top is nudged by that small amount.
- A mitered/arbitrary corner end being trimmed is first **squared** to a clean rectangle,
  keeping any clip.

### Step 5 — Merge vertically-stacked segments of the same wall
In a multi-storey export one physical wall is often modelled as two vertically-stacked segments
that share the same plan axis **and the same Revit wall id**: a **lower** segment (a full
rectangle, floor→plate, carrying the wall's openings) and an **upper** gable segment
(plate→roof, clipped by the sloped roof plane). These are combined into **one full-height wall**:

1. **Group walls by wall id** (`<id>`). Only a clean **stacked pair** is merged — exactly two
   walls, **same id**, on different storeys (different base Z), with coincident plan footprints.
   Anything else is left untouched.
2. The **lower** (smaller base Z) is kept; its base extrusion `Depth` is grown by the upper's
   depth so it spans floor→roof.
3. The upper's roof clip(s) are **deep-cloned onto the lower**, re-anchored world-fixed by the
   full placement transform `T = inv(M_lower)·M_upper` (so every cutting plane lands at the
   exact same world position even if per-storey division shifted the two segments apart). Only
   fresh entities are created — shared points/planes are never mutated.
4. Any opening hosted by the upper is transferred to the lower (FOREST uppers have none; the
   lower keeps its existing voids/fills).
5. The upper wall is removed (detached from its storey containment and deleted).

This is the only step that changes the wall **count** (a pair → one wall). No-op for
single-storey files (no stacked pairs) — only FOREST is affected (15 → 8 walls).

### Step 6 — Normalize wall class
Finally, any deprecated **`IfcWallStandardCase`** is replaced with a plain **`IfcWall`** (done
last, so it doesn't dangle the references used above). The two share identical attributes, so the
new wall keeps the same GlobalId/Name/placement/representation and every inverse reference
(containment, voids, fills, materials, property sets) is redirected to it. No-op for files that
already use `IfcWall` (only Lexford, an IFC2X3 export, has `IfcWallStandardCase`).

---

## 5. Wall classification recap

- **Exterior** — one long face on the building envelope; forms the perimeter; participates in
  the pinwheel.
- **Interior** — both long faces enclosed; kept from overlapping; butts neighbours via
  "longer owns"; otherwise untouched.
- **Design** — both long faces exposed (a free-standing/projecting wall); butts an exterior
  wall where it meets one; otherwise standalone and untouched.

## 6. Body-type handling (how each wall's geometry is read & rebuilt)

| Body type                              | Read as                              | When divided at a corner |
|----------------------------------------|--------------------------------------|--------------------------|
| Single `IfcExtrudedAreaSolid` (rect)   | length, thickness, +Z                | trim run dimension |
| Single extrusion, mitered/arbitrary    | length, thickness from profile       | **square** to rectangle, then trim (clip kept) |
| `IfcBooleanClippingResult` (clipped)   | base extrusion read **through** clip | trim/extend base; **clip kept & world-fixed** — on a shift **every** cutting plane in the nested clip chain (a hip/gable end has several roof facets plus inert base cuts) is counter-translated so the sloped top stays on the fixed roof; on an extension each bounded polygon is grown to cover the new length (no uncut full-height sliver). New points are assigned (shared points never mutated) |
| Profiled (run×height) extrusion        | thickness = depth; top from polygon  | re-interpolate top over new length |
| `IfcFacetedBrep` mesh (1+ items)       | length/thickness/top from vertices   | **rebuild** as single extrusion; top preserved (flat→rectangle, stepped/sloped→run×height profile). Miter-tip artifacts stripped so the captured top is the real roofline |
| **Multi-solid** (>1 extruded solid)    | length from Axis, thickness, flat top | **rebuild as a single full-length solid**; preserved `IfcOpeningElement`s re-cut the door/window hole (e.g. a sliding-door wall with two jamb pieces) |

---

## 7. Output & built-in verification

For each input the script writes the `-W1` copy and prints:

- **Overlaps** — any pair of wall footprints penetrating more than ¼". The check is
  **storey-aware**: pairs on different storeys, or whose Z ranges don't overlap, are not
  flagged (so walls stacked on different floors are not false positives). Expected: `none`.
- **Preservation** — counts of every non-wall element type (`IfcDoor`, `IfcWindow`, `IfcSlab`,
  `IfcRoof`, `IfcOpeningElement`, `IfcSpace`, `IfcRelVoidsElement`, `IfcRelFillsElement`,
  `IfcMaterialLayerSetUsage`, `IfcBuildingStorey`, `IfcFurnishingElement`,
  `IfcBuildingElementProxy`, `IfcCovering`) before vs after. Expected: all unchanged — **except
  `IfcFurnishingElement`, which rises by the number of glass walls converted to furniture**
  (Step 0); that delta is expected and reported.
- **Conversion** — each glass wall converted in Step 0 is confirmed to resolve (by GlobalId) to a
  furniture element (never a wall) that keeps a Body representation and a placement.
- The final wall list with cleaned names.

---

## 8. Acceptance criteria (what a testing agent should assert)

For **every** output `*-W1.ifc`:

1. **Original untouched** — the source file's bytes/mtime are unchanged; output is a separate
   `-W1` file.
2. **Non-wall preservation** — for each non-wall type, `count(before) == count(after)`, except
   `IfcFurnishingElement` which equals `before + (glass walls converted in Step 0)`.
3. **No same-storey overlaps** — no pair of walls sharing a storey and overlapping Z ranges has
   footprint penetration > 0.02 ft (¼"). (Cross-storey/stacked walls are allowed.)
4. **Openings fixed** — every `IfcOpeningElement`'s world placement is unchanged
   (Δ < 1e-4 ft) vs the original, so all doors/windows stay located.
5. **Wall count** — walls are cleaned in place and never split/added. The count equals the
   original **minus glass walls converted to furniture** (Step 0) **minus vertical merges**
   (Step 5, each stacked same-id pair → one wall). FOREST goes 15 → **7** walls (−1 glass, −7
   merged) plus 1 new furniture element.
6. **Names** — every (remaining) wall is renamed `Exterior wall: <id>` / `Interior wall: <id>` /
   `Design wall: <id>`, with `<id>` matching the original trailing element id. Glass walls
   converted in Step 0 are instead named `Shower glass: <id>` (and are furniture, not walls).
7. **Vertical extrusions** — every wall body's base solid extrudes along +Z.
7b. **Wall class** — the output contains no `IfcWallStandardCase`; all walls are `IfcWall` (with
    GlobalIds preserved and all relationships intact).
8. **Clean corners** — at each perimeter corner the two walls butt (touch, no penetration);
   no 45° miter remains at a divided corner.
9. **Pinwheel ownership** — for a rectangular perimeter, **each exterior wall owns exactly one
   corner** (reaches exactly one outer corner; butts at its other end). No wall owns two corners
   while another owns none. Checked per storey.
10. **Shape preservation** — walls not divided at any corner are geometrically unchanged (same
    body, profile, clip). Clipped tops are retained on divided walls too.
11. **Top bound (roof) unchanged** — re-dividing changes wall lengths/ownership, but the overall
    top surface must match the original. Sample (X, Y) across the footprint; the max top z over
    all walls covering each point (each wall's top z from its clip plane — bounded OR unbounded
    `IfcHalfSpaceSolid` — evaluated analytically) must equal the original within ~0.02 ft. This
    catches lifted tops, uncut slivers, shifted models, and bad top extrapolation. **All seven
    fixtures currently measure 0.0000 ft.**
12. **Vertical merge (multi-storey)** — for a stacked same-id pair, the output has exactly one
    `IfcWall` for that id, on the lower storey, spanning floor→roof; no segment remains on the
    upper (plate) storey and that storey's containment no longer lists it. The merged wall's
    grafted roof clip plane(s) match the original upper segment's roof plane **in world
    coordinates** (offset along the normal < 0.01 ft), and the lower's openings are unchanged
    (criterion 4). FOREST: 7 pairs merged, all roof-plane offsets **0.0000 ft**.
13. **Glass → furniture (Step 0)** — every wall whose Revit name contains `SHOWER`/`GLASS` is, in
    the output, an `IfcFurniture` (or `IfcFurnishingElement` on IFC2X3) named `Shower glass: <id>`,
    keeping the same GlobalId, world placement, Body geometry, glass material and storey
    containment, and is no longer an `IfcWall`. The six fixtures convert: FOREST 834582; 14TH
    628962, 664259; HUDSON 591587; Turnberry 1698782, 1699201. Lexford/Northam/San Juan: none.

Helpful internal functions for assertions: `classify_walls(walls)`, `_get_wall_info(wall)`
(returns `ws`,`we`,`x_dir`,`axis_len`,`thickness`,`body_kind`,`top_profile`,`flat`),
`_footprint(info)`.

## 9. Test fixtures & expected results (`IFCs/`)

Every fixture must end with **overlaps: none**, **preservation intact**, **openings moved = 0**,
and a **pinwheel perimeter** (each exterior wall owns exactly one corner, per storey).

| Fixture | Walls | Classified | Key expectations |
|---------|-------|-----------|------------------|
| `SAN JUAN CYPRESS - AUG 2` | 6 | 4 ext + 2 design | Pinwheel exterior lengths **8.5 / 8.5 / 12.5 / 12.5 ft**; the two design walls (3.0 ft) standalone & untouched. |
| `Northam Ave, San Carlos` | 6 | 4 ext + 2 design | 4 mitered walls squared + pinwheel to **11.45 / 11.45 / 17.45 / 17.45 ft**; wall 1291421's miter removed; the two 1-ft `Generic-12"` fins (1350987, 1351039) project south past the perimeter → classified **design** (projecting-stub refinement) and otherwise untouched. |
| `HUDSON ADU` | 9 → **8** | 4 ext + 3 int + 1 design (+1 glass→furniture) | Exterior pinwheel; interior–interior overlap resolved (interiors butt); clipped interior tops preserved. Glass wall **591587 → `IfcFurniture` "Shower glass: 591587"** (Step 0). |
| `FOREST ADU` | 15 → **7** | 4 ext + 3 int (+1 glass→furniture, after merge) | Glass wall **834582 → `IfcFurniture`** (Step 0). Multi-storey: exterior walls were already touching, now **extended to corners + pinwheeled per storey**; then the **7 vertically-stacked same-id pairs** (lower floor→plate + upper plate→roof gable) are **merged into one full-height wall each** (Step 5), so 15 walls → 7 (−1 glass, 7 merged). Grafted gable-roof clips stay world-fixed (all roof-plane offsets 0.0); the lower segment's openings are preserved; no wall remains on the PLATE storey. Interior walls keep their `Clipping` bodies (not flattened). |
| `14TH SF - MAR 28 V4` | 9 → **7** | 4 ext + 2 int + 1 design (+2 glass→furniture) | Glass/shower walls **628962, 664259 → `IfcFurniture`** (Step 0). Brep walls 628797/628799 rebuilt as single extrusions (tops preserved, flat 9 ft); multi-solid sliding-door wall 628798 rebuilt as one solid with its door opening preserved; interiors butt; the 628797↔692873 continued line stays flush (628798 doesn't protrude). |
| `LEXFORD_OFFICE` | 7 | 4 ext + 2 int + 1 design | Walls modelled already-butting; **extended + pinwheeled** so each exterior wall owns one corner (previously 2205475 owned both top corners, 2205477 owned none). No glass walls. |
| `Turnberry_927_TURNBERRY_ADU-DEC_2_2025` | 22 → **20** | 4 ext + 1 design + 15 int (+2 glass→furniture) | Glass walls **1698782, 1699201 → `IfcFurniture`** (Step 0). Exterior perimeter extended + pinwheeled (each owns one corner); large interior/partition set preserved; all non-wall elements intact. |

## 10. Implementation notes & limitations

- **Repositioning preserves the roof.** Clipped (sloped-top) walls keep their exact roof cut:
  **every** cutting plane in the (possibly nested) clip chain is held world-fixed and any
  bounded polygon is grown to the new length. (A hip/gable end has several roof facets nested as
  `BooleanClippingResult(BooleanClippingResult(base, hs1), hs2)…`; counter-shifting only the
  outermost would let the inner facets ride along and lift ~1″ off the roof — so the whole chain
  is walked.) Profiled (Brep-derived) walls that get extended are top-anchored to the neighbour's
  roof height at the corner. Net: the top bound is unchanged (0.0000 ft on all fixtures).
- **The vertical merge keeps the roof world-fixed.** When two stacked segments are combined
  (Step 5), the upper's roof clips are re-anchored onto the lower by the full transform
  `T = inv(M_lower)·M_upper`, not a Z-only shift — so even when per-storey division left the two
  segments with slightly different XY origins, the grafted roof plane lands exactly on the
  original roof (FOREST: 0.0000 ft on all 7 merged walls).
- **Glass → furniture is lossless re-typing.** Step 0 reuses the same technique as the
  `IfcWallStandardCase → IfcWall` normalisation: copy the wall's attributes
  (`get_info(recursive=False)`), `create_entity("IfcFurniture", …)`, and redirect every inverse
  reference to the new element, then remove the old wall. The new element shares the original
  `ObjectPlacement`, Body `Representation` and glass material, so geometry/placement/material are
  unchanged; only the wall-only `Axis` representation, `PredefinedType` and any openings are
  dropped. Because the converted wall object is removed, it must be **filtered out of the working
  `walls` list before removal** (touching a deleted entity crashes ifcopenshell).
- **Never mutate a shared `IfcCartesianPoint` in place.** Revit exports share points heavily —
  e.g. the origin `(0,0,0)` is shared by the building placement, many wall placements, and
  clip-plane positions. Editing one in place would move the whole model. All repositioning
  (placement origin, opening compensation, clip-plane shift) therefore **assigns a fresh point**.
- A few thin/short walls can be geometrically ambiguous for the exposed-face classifier (and a
  very short appendage wall may not pick up a corner — e.g. one 1-ft wall in Northam).
- The `ifcopenshell` geometry kernel may tessellate some fixtures (e.g. 14TH, which has a
  degenerate top-clip plane in the source) to empty in a headless harness; this affects the
  **original** files too and is not introduced by the cleanup. Validate plan geometry from the
  IFC profile/placement data (as the script does), not only from kernel tessellation.

## 11. To-do / backlog

- [ ] **De-duplicate grafted roof-clip planes on vertical merge (Step 5).** When a stacked pair
  is merged, the upper's roof clip is grafted onto the lower even if the lower already carries an
  identical cutting plane — leaving a redundant duplicate plane on each merged wall (observed on
  all 7 FOREST merged walls: the lower's own `(0,0,1)` cut plus an identical grafted copy). The
  cut is idempotent so the geometry/roof is correct (top bound 0.0000 ft), but the merge should
  skip grafting a plane the lower already has, to keep the body clean. *Low priority — cosmetic.*
