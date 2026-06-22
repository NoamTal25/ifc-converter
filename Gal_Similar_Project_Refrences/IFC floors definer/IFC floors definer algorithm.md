# IFC Floors definer — Algorithm & Test Specification

> **Living document.** Describes, step by step, how the tool reorganizes and defines the
> slabs and floors. Kept in sync as the algorithm evolves. Last updated: **2026-06-21**.
>
> **Implementation:** `IFC_floors_definer_V1.py`

---

## 1. Purpose

- Check what floors are already defined. These are found as `IfcSlab`s named `Floor: xxxx`.
- Separate the interior floor from exterior floors such as decks, porches and patios.
- Add a finish-material layer on top of the floors at specific areas — e.g. a bathroom
  (found via its toilet/space) gets tile, decks/patios get concrete, the interior gets hardwood.

---

## 2. How to run

The original file is never modified; the result is written next to it with the suffix
`-F1` before the extension.

```
# process every source .ifc in IFCs/ (skips files already ending in -F1)
python3 IFC_floors_definer_V1.py

# or a single file (auto-names the output)
python3 "IFC_floors_definer_V1.py" "IFCs/SAN JUAN CYPRESS - AUG 2-W1-L1.ifc"
#   → IFCs/SAN JUAN CYPRESS - AUG 2-W1-L1-F1.ifc

# or explicit output path
python3 IFC_floors_definer_V1.py <in.ifc> <out.ifc>
```

---

## 3. Algorithm (in execution order)

### Step 0 — Rebuild the interior floor; split into main unit / deck / stair  *(implemented)*

Revit models often (a) split the interior floor into several overlapping `IfcSlab`s (a main
floor + a bathroom floor, or a stacked buildup), and (b) extend **one** slab across the
interior **and an attached exterior deck/patio (+ a stair)**. This step rebuilds the floor
and splits it into up to three element kinds:

- **Interior floor slabs** = `Floor:`-prefixed, classified interior, with an extruded body.
  Footprints are **unioned** as the TRUE polygon (`unary_union`), not a bounding box (no
  phantom over-coverage). Footprints (`_world_polygon` / `_profile_rings_2d`) honor a
  rectangle profile's full 2D placement — **`Position.RefDirection` rotation as well as
  Location** — so rotated-profile elements (notably walls) are placed correctly; ignoring
  the rotation previously flipped a wall's footprint 90°, which had split a deck covering on
  a phantom wall.
- **Unit-bounding wall envelope** = `unary_union(wall_rooms ∪ wall_footprints)` built from
  **exterior + interior walls only — `design` walls are excluded** (`_wall_class` /
  `_bounding_walls`), so the slab under a design wall counts as *exterior*. `wall_rooms` =
  polygonized wall-centerline cells (`_wall_rooms`). Then:
  - **main unit** = `union ∩ envelope` → `Floor:` (Hardwood)
  - **beyond** = `union − envelope`, keeping only pieces **≥ `MIN_DECK_AREA` (5 sqft)** —
    smaller pieces are floor-edge noise (the slab poking a few inches past the envelope) and
    **stay part of the interior floor**. Each genuine beyond piece → a **`Stair:`** if it's
    narrow (smaller plan dim `< STAIR_MAX_DEPTH`) AND steps down (top `> STAIR_DROP_MIN`
    below the main floor), else **`Deck:`** (Concrete).
- **Fresh clean slabs replace the originals** (`_create_slab_from_poly`): one slab per
  region, a single `+Z` `IfcExtrudedAreaSolid` (`IfcRectangleProfileDef` for a clean box,
  else `IfcArbitraryClosedProfileDef`/`…WithVoids`) at an identity world placement. This
  discards Revit's `IfcBooleanResult`/flipped-axis geometry. `Deck:`/`Stair:` names are set
  up front so Step 1 leaves them and Step 2 finishes them as Concrete; `Floor:` → Hardwood.
- **Per-region z (`_region_z`)**: each new slab's z is taken from the source slab that
  covers that region (`z_top` = top of the largest-overlap slab, `z_bottom` = min bottom of
  overlapping slabs). So a layered buildup (Turnberry: 4″/7″ base, 11″/12″ mat, 2″ topping)
  keeps each region at its **true finished-floor level** — main 1.333, deck 1.25, stair
  0.583 — instead of collapsing to the largest-area slab's level.
- Runs when there are **multiple** interior slabs **or** a beyond-envelope region; a single
  interior floor fully inside the walls (14TH SF, LEXFORD, San Juan) is left untouched.
- **Side-effects of deleting the old slabs** (handled / expected): their own
  `IfcMaterialLayerSetUsage`, slab-cut `IfcOpeningElement`, and orphaned `IfcSlabType`s go.
- **Space boundaries** on old interior slabs are **repointed** to the new main slab.

Bathrooms are handled entirely in Step 2 (toilet → space), independent of this step — so
single-interior-floor models (e.g. Forest, San Juan) skip Step 0 yet are still tiled.

### Step 1 — Floor-name normalization  *(implemented, V1)*

Revit exports floor slabs named like `Floor:Generic - 11" porch:547963`. This step
normalizes the leading prefix on **both `IfcSlab` and `IfcSlabType`** (PredefinedType
`.FLOOR.`) whose `Name` starts with `Floor:`:

1. **Add a space after the prefix colon:** `Floor:X` → `Floor: X`.
2. **Re-prefix exterior floors by type** — case-insensitive keyword match anywhere in
   the name (first match wins):
   - `deck`  → `Deck: `
   - `porch` → `Porch: `
   - `patio` → `Deck: ` (patios are labelled as decks)
   - otherwise (interior) → keep `Floor: `

Only the leading `Floor:` prefix changes; the trailing `:id` is kept verbatim (the
split is on the *first* colon, so embedded `"`/`:` are safe). For exterior floors the
matched keyword is **removed from the descriptor** (along with a leading `_`/space
separator) since it is now redundant with the new prefix, e.g.
`Floor:Generic - 11" porch:547963` → `Porch: Generic - 11":547963` and
`Floor:Generic - 21"_DECK:963956` → `Deck: Generic - 21":963956`. This step also
realizes Purpose item 2 (interior vs exterior separation) at the naming level.

3. **Reclassify stairs** (`Stair:`). An exterior surface (deck/patio/porch) is actually
   a **stair step** when its top sits **more than 1″ below the interior floor top** AND
   its footprint is **shallow (smaller plan dimension < 2 ft)**. Such slabs are re-prefixed
   `Stair:` instead of `Deck:`/`Porch:` (the keyword is still stripped, e.g.
   `Floor:Generic - 21"_DECK:963956` → `Stair: Generic - 21":963956`). This is
   **geometry-based and per slab instance** (computed from the slab's world top z and
   footprint bounds vs `_interior_top_z`). Because slabtypes are often **shared** by both
   stairs and decks, an `IfcSlabType` is relabelled `Stair:` **only when all** its exterior
   instances are stairs; mixed types keep `Deck:`. Thresholds: `STAIR_DROP_MIN` (1″),
   `STAIR_MAX_DEPTH` (2 ft). In Step 2 a stair gets the **same finish as the deck/patio it
   is attached to** (the nearest exterior `Deck:`/`Porch:` slab by footprint distance).

**Scope guard:** Step 1 changes *only* the `Name` attribute of `IfcSlab`/`IfcSlabType`.
No geometry, materials, coverings, spaces, or spatial relations are touched.

### Step 2 — Floor finishes via IfcCovering  *(implemented)*

For each floor `IfcSlab` (name prefixed `Floor:`/`Deck:`/`Porch:`/`Stair:` after Step 1),
create `IfcCovering`(s) with `PredefinedType=FLOORING` sitting flush on the slab's **top**
face, each carrying a finish `IfcMaterial`:

| Floor kind | Material | Thickness |
|---|---|---|
| interior (`Floor:`) | Hardwood | 1/2″ |
| exterior (`Deck:`/`Porch:`; patios map to `Deck:`) | Concrete | 2″ |
| bathroom (toilet → space, see below) | Ceramic Tile | 1/2″ |
| `Stair:` | same as attached deck/patio | (inherited) |

Hardwood and tile share the same 1/2″ thickness, so their tops are **coplanar** — the
bathroom is a flush tile region in a level floor (distinguished by material, no step).

Mapping lives in the `FINISH_BY_PREFIX` / `TILE_FINISH` config at the top of the script
(thickness in feet, made unit-aware via `calculate_unit_scale`).

**Bathrooms (toilet → room → footprint).** Bathrooms are found **geometrically**, not by
slab name: `detect_bathroom_polys()` locates **toilet** fixtures
(`IfcSanitaryTerminal`/`IfcFlowTerminal`/`IfcFurnishingElement`/`IfcFurniture` whose
Name/ObjectType matches `TOILET_KEYWORDS` = `toilet`/`wc`/`water closet`/…), then resolves
the room around each toilet by one of two methods:

- **Preferred — enclosing `IfcSpace`:** the space containing the toilet (via
  `IfcRelContainedInSpatialStructure`, or by world-XY-in-footprint); its swept-solid
  footprint is the wall-bounded room outline Revit already encodes. (Works whether or not
  the bathroom is a distinct slab; e.g. Forest's bathroom is a region of the single
  CRAWSPACE floor, Hudson's was a separate slab — both resolve to the same space footprint.)
- **Fallback — derive from walls** (`_wall_rooms` / `_wall_room_poly`) when the file has
  **no `IfcSpace`** (e.g. IFC2X3 exports). Wall **Axis centerlines** are extended by
  `WALL_ROOM_EXTEND` (1 ft, so near-meeting corners connect) and `polygonize`d into rooms;
  the cell containing the toilet, trimmed to the inner wall faces (minus the wall
  footprints), is the bathroom. Centerlines run continuously through doorways, so rooms
  stay sealed — unlike wall *footprints*, which leak through door openings.

For each interior `Floor:` slab, bathrooms are clipped to its footprint; the **Hardwood**
covering is then `floor − ∪bathrooms` and each bathroom region is laid as its own **Ceramic
Tile** covering filling exactly that notch.

**Walls are excluded from every covering.** All finishes (interior Hardwood, bathroom Tile,
deck/patio/porch Concrete, stair) have the **footprints of ALL walls subtracted** —
exterior + interior **and design** (`_wall_footprint_mask(model, _all_walls(model))`) —
before the covering profile is built, so no finish runs under any wall; each covering's
perimeter stops at the inner wall face. (Note the two distinct wall sets: Step 0's *unit
envelope* uses exterior+interior only — design walls excluded so their area classifies as
exterior — while the *covering trim* here uses **all** walls.) Subtracting interior
partitions naturally splits a floor finish into one covering per room (shapely `difference`,
each piece an `IfcArbitraryClosedProfileDef`/`…WithVoids`). Verified: `covering ∩ walls ≈ 0`
across all 7 test files (incl. San Juan's design walls); hardwood + tile cover the floor's
open (wall-free) area with no overlap.

**Placement (handles any extrusion axis):** the covering reuses the slab's
`ObjectPlacement`; its profile is a computed world-XY polygon (the relevant slab/space/room
footprint minus walls, and minus bathrooms for the interior). The slab's two extrusion end
faces are transformed to world coordinates; the higher-Z one is the **top**. The covering profile is placed there (reusing the slab solid's
`Axis`/`RefDirection`) and extruded **world-up** by the finish thickness — so it is flush
with the slab top regardless of axis orientation (the new interior slab is a clean `+Z`
prism, but exterior slabs may still carry Revit's flipped axis). Geometry is built in file
units with raw `model.createIfc*` (no `edit_object_placement`, which assumes metres).

**Relationships:** the covering is appended to the slab's existing storey
`IfcRelContainedInSpatialStructure`, associated to its material via `IfcRelAssociatesMaterial`,
and linked to the slab via a new `IfcRelCoversBldgElements`. Re-running is idempotent (a slab
already carrying a FLOORING covering is skipped).

**Design note — coverings are separate elements, not nested under the slab.** A floor finish is
a first-class `IfcCovering`, not a structural sub-part of the slab. The slab↔finish relationship
is modeled as *connectivity* (`IfcRelCoversBldgElements`), **not** *decomposition*
(`IfcRelAggregates`/`IfcRelNests`). So each covering lives at the storey level (a sibling of its
slab) with an explicit covers-link — the same convention as this file's existing fascia/soffit
coverings, which are storey-contained. This keeps finishes independently queryable for finish
schedules and material take-offs; aggregating them under the slab would misuse decomposition
semantics and hide them from standard covering queries. (Viewers can still present a covering
grouped beneath the slab it covers, driven by the `IfcRelCoversBldgElements` link.)

---

## 4. Output & built-in verification

`verify()` re-opens source and output and asserts:
- `IfcSlab` / `IfcSlabType` counts changed by exactly the net merged away in Step 0
  (0 when nothing was rebuilt; can be 0 even after a rebuild that replaces N interior slabs
  with N new ones — e.g. 1 Floor + 1 Deck).
- No `Name` still starts with the bare, space-less `Floor:` prefix.
- `IfcCovering` count grew by exactly the number of finish coverings Step 2 created
  (returned by `add_floor_finishes`, so multi-piece hardwood / multiple bathrooms count
  correctly); each new `FLOORING` covering has a material association, a slab link, and its
  base plane is flush (≤ 1e-4) with the linked slab's top face (computed analytically from
  placement matrices, so it does not depend on the geometry mesher).
- `PRESERVE_TYPES` (walls, doors, windows, spaces, storeys, building, storey-containment,
  **and `IfcRelSpaceBoundary`**) are count-stable before/after — never dropped. Space
  boundaries survive a merge because Step 0 repoints them onto the kept floor.
- `SLAB_COUPLED_TYPES` (`IfcMaterialLayerSet`, `IfcMaterialLayerSetUsage`,
  `IfcOpeningElement`) must be stable **only when Step 0 did not rebuild** (keyed off the
  `rebuilt` flag, **not** the net slab-count delta — a rebuild can be net-zero yet still drop
  the replaced slabs' usages/openings). When Step 0 rebuilds, the drop in the old slabs'
  material layer set / usage and any slab-cut opening is expected and reported (not a failure).
- (`IfcCovering`, `IfcMaterial`, `IfcRelAssociatesMaterial` are excluded from preservation —
  Step 2 adds to them — and are checked explicitly instead.)

---

## 5. Test fixtures & expected results

**Current run — all 7 `IFCs/*-W1-L1.ifc` files PASS** (last run 2026-06-21):

| File | Schema | Interior floor | Deck split off | Bathroom source |
|---|---|---|---|---|
| 14TH SF | IFC4X3 | 1 (untouched) | — | IfcSpace |
| FOREST ADU | IFC4X3 | 1 untouched (CRAWSPACE) | — (slivers < 5 sqft kept interior) | IfcSpace #480 |
| HUDSON ADU | IFC4X3 | 2 → 1 merged, 214 sqft | — | IfcSpace #79 |
| LEXFORD_OFFICE | **IFC2X3** | 1 (untouched) | — (1 Deck patio slab) | **wall network** |
| Northam Ave | IFC4X3 | 216 sqft (rebuilt) | **214 sqft Deck** (2 design walls) | none (no toilet) |
| SAN JUAN | IFC4X3 | 1 (untouched) | — (1 Porch) | none (no toilet) |
| Turnberry ADU | IFC4 | 558 sqft @ z=1.333 | **Deck 120 + Stair 10** | wall network |

Coverings are wall-trimmed, so an interior floor with partition walls (or an L-shaped
bathroom split by a wall, e.g. Hudson's two tile pieces) yields several covering pieces;
`Floor:` slabs get Hardwood, split-off `Deck:` slabs get Concrete. Only beyond-envelope
regions ≥ `MIN_DECK_AREA` (5 sqft) split off — floor-edge noise slivers (Forest) stay
interior. Each region's z is taken from its covering source slab, so layered buildups keep
their true level (Turnberry main z=1.333, deck 1.25, stair 0.583); the Forest CRAWSPACE is
left untouched at z=2.417 (full depth to grade).

Detailed cases:

`IFCs/SAN JUAN CYPRESS - AUG 2-W1-L1.ifc` (schema `IFC4X3_ADD2`):

| Before | After |
|---|---|
| `Floor:Generic - 11 7/16":386819` (IfcSlab) | `Floor: Generic - 11 7/16":386819` |
| `Floor:Generic - 11 7/16"` (IfcSlabType) | `Floor: Generic - 11 7/16"` |
| `Floor:Generic - 11" porch:547963` (IfcSlab) | `Porch: Generic - 11":547963` |
| `Floor:Generic - 11" porch` (IfcSlabType) | `Porch: Generic - 11"` |

Expected: 2 interior + 2 exterior (Porch) renamed; no merge (single interior floor); no
toilet → no bathroom, so 2 finishes (full Hardwood + Concrete); verification `PASS`;
original input byte-unchanged.

`IFCs/HUDSON ADU-W1-L1.ifc` (schema `IFC4X3_ADD2`): two overlapping interior floors —
`Floor:…_ FLOOR` (#8511, main) and `Floor:…_ BATHROOM` (#116013, L-shape inside it) —
plus a `…DECK` and a roof.

The main slab #8511's body is an `IfcBooleanResult DIFFERENCE` — a rectangle with the
bathroom L-shape cut through it (Revit's hole for the separate bathroom floor).

Expected: Step 0 **deletes both interior slabs and creates one fresh rectangular slab**
(`IfcRectangleProfileDef`, 11 × 19.5 ft, z = [0, 0.7813]); net `IfcSlab` 4 → 3,
`IfcSlabType` 4 → 2; the bathroom void `IfcOpeningElement` (12 → 11) and the old material
usages (4 → 2) drop with the deleted slabs. Deck renamed `Deck:`. Finishes = Hardwood on
the floor **minus** the bathroom (one covering with an L-shaped void) + Ceramic Tile
filling the bathroom, **both 1/2″** so their tops are coplanar (level floor), + Concrete on
the deck (`IfcCovering` 3 → **7** — hardwood split per room by the interior wall, plus tile
+ deck). The bathroom is detected via the **toilet `#8366` → space #79** (footprint 41.45
sqft). All coverings have the walls subtracted (perimeter at the inner wall faces). The
Bathroom space's floor `IfcRelSpaceBoundary` is repointed to the new slab. Verification
`PASS`; original input byte-unchanged.

`IFCs/FOREST ADU-W1-L1.ifc` (schema `IFC4X3_ADD2`): one interior `CRAWSPACE` floor
(top z = 2.4167 ft), one patio, and a set of `…_DECK` surfaces at descending heights.

Forest has a **toilet** (`IfcFlowTerminal #97954` "WC_FLOOR_MOUNTED") in `IfcSpace #480`
(LongName "Bathroom", footprint 53.64 sqft) but **no** `bath`-named slab.

Expected: no merge (single interior floor). Patio → `Deck:`. **Stair detection** relabels
the 7 narrow descending surfaces (top 7–22″ below the floor, ~0.92 ft deep) as `Stair:`,
while the 3 wider/at-level surfaces (≥ 2 ft deep or ≤ 1″ below) stay `Deck:`. Slabtypes:
the `14"` and `7"` types (all instances stairs) → `Stair:`; the `21"` type (1 stair + 2
decks) stays `Deck:`. **Bathroom detected via the toilet → space #480**: a Ceramic Tile
covering (53.64 sqft) is laid over it and deducted from the crawspace Hardwood. The single
interior CRAWSPACE floor is **left untouched** (no merge; the ~10 sqft of floor edge poking
past the wall envelope is **< `MIN_DECK_AREA`, so no spurious `Deck:`/`Stair:` slivers are
split off** — it stays the interior floor at z=2.417, full depth to grade). Finishes:
Hardwood (floor − bath − walls, splits per room) + Ceramic Tile (bath) on the crawspace,
Concrete on the 3 real decks, **Concrete on the 7 stairs** — **`IfcCovering` 6 → 20**.
Verified: `covering ∩ walls ≈ 0`, all flush. Verification `PASS`.

`IFCs/LEXFORD_OFFICE-W1-L1.ifc` (**IFC2X3**) and
`IFCs/Turnberry…ADU…-W1-L1.ifc` (IFC4): each has a **toilet but no `IfcSpace`** → the
bathroom is derived from the **wall network** (centerlines extended + polygonized). LEXFORD
also exercises IFC2X3 (`IfcSanitaryTerminal` absent from schema → `_safe_by_type`).
**Turnberry** is the 3-way-split + per-region-z case: walls = 4 Exterior / 17 Interior / **1
Design**; the floor splits into `Floor:` (main unit, 558 sqft @ z=1.333, Hardwood), `Deck:`
(120 sqft @ z=1.25, Concrete — includes the area under the design wall), and `Stair:` (10
sqft @ z=0.583, Concrete). main ∩ deck ∩ stair ≈ 0. (`Northam` has spaces but no toilet →
no bathroom; with its 2 **design** walls correctly labelled upstream it splits cleanly into a
216 sqft `Floor:` + a 214 sqft `Deck:` — no stray slivers. Note: this rebuild is net-zero in
`IfcSlab` count yet still drops the replaced slabs' material usages, which is why `verify`
keys slab-coupled checks on the `rebuilt` flag, not the count.) All `PASS`.

**Diagnostic flags:** `FLOORS_DEFINER_FINISH=0` skips Step 2 (bare slabs);
`FLOORS_DEFINER_DROP_INTERIOR_SLAB=1` deletes the merged interior slab entirely.

---

## 6. Known limitations

- **Wall classification depends on upstream naming.** `_wall_class` reads the wall `Name`
  (`Exterior wall:` / `Interior wall:` / `Design wall:` from the walls-cleanup stage). A wall
  that should be a feature but is labelled `Exterior` is treated as unit-bounding, so its area
  is pulled into the interior (e.g. Northam originally mis-labelled a design wall `Exterior`,
  splitting off slivers; relabelling it `Design wall:` upstream fixed it with no code change).
- **Interior/deck split relies on the wall envelope.** A floor region is "deck" iff it
  lies beyond `wall_rooms ∪ wall_footprints` **by ≥ `MIN_DECK_AREA` (5 sqft)**. This is
  robust when walls have `Axis` centerlines that (after the `WALL_ROOM_EXTEND` snap) enclose
  the interior. The 5 sqft floor lets floor-edge noise (the slab poking a few inches past
  the envelope, e.g. Forest) stay interior while keeping genuine decks/stairs (smallest real
  one ≈ 10 sqft); a real deck/stair smaller than 5 sqft would be missed, and a gross wall-
  network gap could still mis-split a large interior area.
- **Stacked slab buildups** (e.g. Turnberry: 4″/7″ base, 11″/12″ mat, 2″ topping) are
  handled by per-region z (`_region_z` uses the top of the dominant source slab per region),
  so the finished floor sits at its true level. Caveat: within one region the **dominant**
  slab's top wins, so a small higher patch (a 2″ topping / shower pan) inside a region is
  represented at the region's dominant level, not its own.
- **No toilet → no bathroom.** Bathrooms are keyed off a toilet fixture; a model with no
  matching toilet (Northam, San Juan) gets a full hardwood interior with no tile region.
- **No `IfcSpace` → wall-derived room** for the bathroom (same wall-network assumptions as
  above). Brep/mesh walls (no extruded footprint) are skipped by the wall mask.
