# IFC levels organizer — Algorithm & Test Specification

> **Living document.** Describes, step by step, what the levels organizer does and how to verify
> it. Kept in sync as the algorithm evolves. Last updated: **2026-06-20**.
>
> **Implementation:** `IFC_levels_organizer_V1.py`

---

## 1. Purpose

Take a Revit-exported IFC and **canonicalize its building storeys ("levels")** so a single-story
building always has exactly four consistent levels, with the right elements under each:

```
GRADE
FF1     ├─ Interior Walls   ├─ Exterior Walls   ├─ Doors   ├─ Windows   └─ Floor
PLATE1  └─ Roof
TOP     (Top Of Roof — datum level, normally empty)
```

- Check what levels are already defined (the `IfcBuildingStorey` instances under `IfcBuilding`).
- Reorganize them so they are consistent and always reduce to **GRADE / FF1 / PLATE1 / TOP**.
- All walls (interior + exterior), doors, windows and the floor slab go under **FF1**.
- The roof goes under **PLATE1**.
- **TOP** is the "Top Of Roof" datum at the roof's peak elevation. It is kept as its own level
  (above PLATE1) and is normally empty — the roof itself stays under PLATE1.

Geometry is preserved **exactly**: only the spatial-containment relationships and the storey set
are changed — no element is ever moved.

---

## 2. Algorithm (`organize(src, out)`)

1. **Copy & open.** Copy the source to the `-L1` output (`shutil.copy2`), then open the copy with
   `ifcopenshell`. The original file is never modified.

2. **Discover existing storeys** — `model.by_type("IfcBuildingStorey")`, sorted by `Elevation`.

3. **Classify into the 4 canonical roles** (name-keyword first, elevation as tiebreaker; each
   storey is claimed by at most one role). Claim order matters — **TOP is claimed before PLATE1**:
   - **GRADE** ← storey whose name contains `"GRADE"`; else the lowest-elevation storey.
   - **FF1** ← name contains `"FINISH"` / `"FF"` / `"LEVEL"`; else the storey just above grade.
   - **TOP** ← name contains `"TOP"` (`"TOP OF ROOF"` / `"ROOF TOP"`), highest match; else the
     highest remaining storey.
   - **PLATE1** ← name contains `"PLATE"` / `"CEILING"` / `"BOTTOM OF ROOF"` / `"ROOF"`, highest
     remaining match; else the next-highest remaining storey.
   - **Extras:** any storey left unclaimed (e.g. flood-datum levels `BFE`, `BFE+1`) merges into
     PLATE1 and is deleted.
   - If the TOP role still has no storey, a fresh empty `IfcBuildingStorey` named `TOP` is created
     just above PLATE1 (`_ensure_top`), reusing PLATE1's placement frame.

4. **Rename** the chosen storeys to exactly `GRADE`, `FF1`, `PLATE1`, `TOP`.

5. **Re-home every contained element** by rebuilding `IfcRelContainedInSpatialStructure`.
   The target level for each element is decided in this priority order:
   1. **Name rule** (`NAME_TO_LEVEL`, checked first, wins over everything): an element whose
      name contains a listed keyword goes to the mapped level, regardless of type or original
      storey. Current rules:
      - name contains **`"UNIT HEATER"`** → **GRADE** (e.g. `Unit Heater - Cabinet:3-6 kW:…`).
   2. **Type rule** (`TYPE_TO_LEVEL`):
      - `IfcWall` / `IfcWallStandardCase`, `IfcDoor`, `IfcWindow`, `IfcSlab` (floor) → **FF1**
      - `IfcRoof` → **PLATE1**
   3. **Fallback** — any other element follows the canonical role of *its original storey*
      (so a roof's fascia/soffit coverings follow the roof into PLATE1, and FF proxies stay in
      FF1), defaulting to FF1. Nothing is dropped.
   - One consolidated `IfcRelContainedInSpatialStructure` is written per canonical storey; old
     storey-containment rels are removed. Storeys are looked up by their **STEP `.id()`**, not
     Python `id()` (ifcopenshell hands back a fresh wrapper on every attribute access).

6. **Preserve geometry — never touch `IfcObjectPlacement`.** Elements use independent
   `IfcLocalPlacement`s, so changing only the containment relationship leaves every element's
   world position unchanged. Storey containment in IFC is purely organizational.

7. **Merge & remove extra storeys.** Re-parent any `IfcSpace` aggregated under a removed storey
   onto FF1, drop the extras from the `IfcBuilding` `IfcRelAggregates`, and delete the orphaned
   storeys so the building aggregates exactly `GRADE` / `FF1` / `PLATE1` / `TOP`, ordered
   low → high.

8. **Write** the model back to the `-L1` output.

---

## 3. Usage

```bash
# process one file (writes the -L1 copy next to it)
python3 IFC_levels_organizer_V1.py "IFCs/SAN JUAN CYPRESS - AUG 2-W1.ifc"

# explicit output path
python3 IFC_levels_organizer_V1.py input.ifc output.ifc

# no args → process every .ifc under ./IFCs (skipping files already ending in -L1)
python3 IFC_levels_organizer_V1.py
```

**Output naming:** `-L1` is inserted before the extension, keeping the previous extension —
`NAME.ifc → NAME-L1.ifc` (so a walls-cleanup `…-W1.ifc` becomes `…-W1-L1.ifc`). This matches the
sibling pipeline stages (`-W1` walls cleanup, `-C1` floors).

**Python:** requires `ifcopenshell` (use the system `python3` at
`/Library/Frameworks/Python.framework/Versions/3.10/`; the repo `venv` does not have it).

---

## 4. Verification

The script runs a built-in `verify()` pass after writing, which reopens the output and checks:

- **Storeys:** exactly four, named `GRADE` / `FF1` / `PLATE1` / `TOP`, all aggregated under `IfcBuilding`.
- **Containment:** walls / doors / windows / floor slabs under FF1; roof under PLATE1; TOP empty; nothing orphaned.
- **Counts preserved:** total `IfcWall`/`IfcDoor`/`IfcWindow`/`IfcSlab`/`IfcRoof`/`IfcCovering`/
  `IfcBuildingElementProxy`/`IfcSpace` identical before vs after (nothing lost).
- **Geometry preserved:** every element's absolute world-Z (walking `PlacementRelTo`) is unchanged —
  reports `0 elements moved`.

### Reference result — `SAN JUAN CYPRESS - AUG 2-W1.ifc`

Input had 4 storeys: `GRADE` (0.0), `FINISHED FLOOR` (0.95), `BOTTOM OF ROOF` (9.95), `ROOF TOP` (10.62).

| Level | After |
|---|---|
| `GRADE` | empty (its 2 slabs moved up to FF1) |
| `FF1` | 6 walls, 6 windows, 1 door, 2 floor slabs, 1 proxy; `IfcSpace` nested here |
| `PLATE1` | 1 roof, 2 coverings (`BOTTOM OF ROOF`) |
| `TOP` | empty datum (`ROOF TOP`, 10.62) |

All counts preserved, 0 elements moved → **ALL CHECKS PASSED**.

### Reference result — `FOREST ADU-W1.ifc` (name-rule + extras example)

Input had 6 storeys: `GRADE` (0.0), `BFE` (0.5), `BFE+1` (1.5), `FINISHED FLOOR` (2.42),
`PLATE` (13.08), `TOP OF ROOF` (15.87). `TOP OF ROOF` maps to `TOP`; the two below-FF flood-datum
levels (`BFE`, `BFE+1`) are extras that merge into `PLATE1`.

| Level | After |
|---|---|
| `GRADE` | 1 mechanical fastener + **1 proxy** (`Unit Heater - Cabinet`, placed here by the name rule) |
| `FF1` | 7 walls, 11 slabs, 7 doors, 5 windows, 6 proxies, 2 railings, 2 furniture |
| `PLATE1` | 1 roof, 3 coverings |
| `TOP` | empty datum (`TOP OF ROOF`, 15.87) |

All counts preserved, 0 elements moved → **ALL CHECKS PASSED**.

> _Wall count note:_ the `-W1` input now carries **7 walls** (down from an earlier 15). FOREST's
> Revit export had split walls into stacked FF→plate / plate→roof pairs; the upstream
> walls-cleanup stage combined each pair into a single wall, so the levels organizer simply
> inherits the cleaned set.
