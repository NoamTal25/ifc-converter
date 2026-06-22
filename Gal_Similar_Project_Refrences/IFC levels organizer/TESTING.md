# IFC levels organizer — Testing methodology

> Companion to `IFC levels organizer algorithm.md`. Describes how the **testing agent**
> (`test_levels_organizer.py`) verifies `IFC_levels_organizer_V1.py`, and why each check
> exists. Last updated: **2026-06-21**.

---

## 1. Philosophy — test independently of the thing under test

The organizer ships its own `verify()` pass, but that pass is **lenient and partly
self-fulfilling**, so it must not be the only safety net:

- it computes `ff_ok` but never uses it (`>= 0` is always true);
- `roof_in_plate` passes when there is **no roof at all** (`… or "IfcRoof" not in …`);
- it never checks that doors / windows / slabs actually land under **FF1**;
- it never checks that **TOP** is empty, that storeys are ordered low→high, or that
  the `UNIT HEATER` name rule fired;
- geometry is compared on **Z only**, not the full placement.

The testing agent therefore **re-derives every invariant from scratch** against a freshly
produced output and does not call `verify()`. A test that merely re-asserts the code's own
weak claims would pass even when the algorithm is wrong.

**Teeth check:** the harness is validated against a *negative control* — point it at a
no-op "organizer" that just copies the file, and it must report failures (it flags 7).
A test suite that cannot fail is worthless.

---

## 2. How to run

```bash
# system python3 (has ifcopenshell + numpy); the repo venv does NOT
cd "IFC levels organizer"
python3 test_levels_organizer.py            # every source under ./IFCs
python3 test_levels_organizer.py -v         # verbose — show every passing assertion
python3 test_levels_organizer.py "IFCs/FOREST ADU-W1.ifc"   # one file
```

For each source IFC (those **not** ending in `-L1`) the agent:
1. runs `organize(src, tmp)` into a **throwaway temp file** — the real `IFCs/` dir is never
   polluted and the original source is checked to be byte-for-byte untouched;
2. opens the **original** and the **output** and runs the invariant battery below;
3. exits non-zero if any invariant fails on any file.

---

## 3. The invariant battery

### STRUCTURE — the canonical storey set
- exactly 4 storeys, named exactly `GRADE` / `FF1` / `PLATE1` / `TOP`, no duplicates;
- the `IfcBuilding` aggregates exactly those 4 storeys;
- storeys order low→high and elevations are non-decreasing;
- **≤ 1** `IfcRelContainedInSpatialStructure` per storey (containment is consolidated,
  not fragmented).

### CONTAINMENT — the right elements under the right level
- **TOP** datum level is empty;
- **name rule:** every element whose name contains `UNIT HEATER` is under **GRADE**
  (and name-rule elements are excluded from the type-rule checks below, since the name
  rule intentionally overrides type);
- every *contained* `IfcWall` / `IfcWallStandardCase` / `IfcDoor` / `IfcWindow` / `IfcSlab`
  is under **FF1**;
- every *contained* `IfcRoof` is under **PLATE1**;
- nothing that was contained in a storey before is **orphaned** after.

> "contained" = currently in some `IfcRelContainedInSpatialStructure`. Products that are
> only *nested* (e.g. an `IfcSpace` aggregated under a storey) are excluded from the
> type checks on purpose — the algorithm re-parents those separately.

### CONSERVATION — nothing lost, nothing duplicated
- per-type counts identical before→after for walls, doors, windows, slabs, roofs,
  coverings, proxies, spaces, railings, furnishing;
- the **multiset of non-storey product GlobalIds** is identical before→after (storeys are
  excluded because the algorithm may create `TOP` and delete extras).

### GEOMETRY — no element ever moves
- the full **4×4 world placement matrix** (via `ifcopenshell.util.placement`) is compared
  by GlobalId for every shared product; **zero** may differ (`atol=1e-9`). This is stricter
  than the script's Z-only check — it catches X/Y/rotation drift too;
- guards that geometry was *actually* compared (shared product set non-empty), so a parse
  failure can't masquerade as "nothing moved".

### SOURCE — the original is sacred
- the source file's SHA-256 and mtime are unchanged after a run.

### IDEMPOTENCY — running on its own output is a fixed point
- re-running `organize` on the `-L1` output yields the same 4 storeys and the same
  per-storey containment. A correct canonicalizer must be stable under repetition.

---

## 4. Coverage today & gaps to extend

**Covered by the corpus in `./IFCs`:**
- 4-storey `GRADE / FINISHED FLOOR / BOTTOM OF ROOF / ROOF TOP` (SAN JUAN — TOP from a real storey);
- 6-storey with flood-datum extras `BFE` / `BFE+1` merged into PLATE1 and the `UNIT HEATER`
  name rule (FOREST ADU);
- larger real exports (14TH SF, LEXFORD office, Northam, Hudson, Turnberry).

**Known gaps — add a fixture when one appears:**
- a model with **no `TOP`-named storey** so `_ensure_top` must synthesize one (assert TOP
  was created above PLATE1 and is empty);
- a model with **no `IfcBuilding`** (graceful no-op path);
- a model with a single storey only (all roles fall back from one storey);
- a model whose roof carries `IfcCovering` fascia/soffit — assert those coverings follow
  the roof into PLATE1 via the fallback rule.

When the algorithm gains a new `NAME_TO_LEVEL` / `TYPE_TO_LEVEL` rule, add the matching
assertion in `run_checks()` and a fixture that exercises it, and update §3 here.
