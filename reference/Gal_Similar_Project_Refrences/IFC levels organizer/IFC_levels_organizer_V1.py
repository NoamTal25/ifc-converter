#!/usr/bin/env python3
"""
IFC_levels_organizer_V1.py — Gaudi IFC pipeline stage: LEVELS (storeys) ONLY.

Takes a Revit-exported IFC and canonicalizes its building storeys ("levels") so a
single-story building always has exactly three consistent levels:

    GRADE
    FF1     ├─ Interior Walls  ├─ Exterior Walls  ├─ Doors  ├─ Windows  └─ Floor
    PLATE1  └─ Roof

The original file is never modified.  The organized result is written next to it with
the suffix "-L1" before the extension, e.g.
    IFCs/SAN JUAN CYPRESS - AUG 2-W1.ifc  →  IFCs/SAN JUAN CYPRESS - AUG 2-W1-L1.ifc

Geometry is preserved EXACTLY.  Only the spatial-containment relationships
(IfcRelContainedInSpatialStructure) and the storey set/aggregation are changed — no
IfcObjectPlacement is ever touched, so every element keeps its world position.

Algorithm (see "IFC levels organizer algorithm.md"):
  1. Discover existing IfcBuildingStorey levels under the IfcBuilding.
  2. Classify them into the 3 canonical roles (GRADE / FF1 / PLATE1) by name + elevation;
     extra top storeys (e.g. a separate "ROOF TOP") merge into PLATE1.
  3. Rename the chosen storeys to GRADE / FF1 / PLATE1.
  4. Re-home every contained element: walls, doors, windows and floor slabs -> FF1;
     roof -> PLATE1; anything else follows its original storey's canonical role.
  5. Re-parent any IfcSpace aggregated under a removed storey, then delete the extra
     storeys so the building aggregates exactly GRADE / FF1 / PLATE1.

Usage:
    python3 IFC_levels_organizer_V1.py [input.ifc] [output.ifc]

With no arguments it processes every .ifc under the ./IFCs subdirectory and writes a
"-L1" copy of each (skipping files already ending in "-L1").
"""
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import ifcopenshell


# ══════════════════════════════════════════════════════════════════════════════
# Canonical level roles
# ══════════════════════════════════════════════════════════════════════════════

GRADE, FF1, PLATE1, TOP = "GRADE", "FF1", "PLATE1", "TOP"

# Canonical levels, low → high.  TOP is the "Top Of Roof" datum level above the plate.
LEVELS_LOW_TO_HIGH = (GRADE, FF1, PLATE1, TOP)

# Element type -> canonical level it must live under.
TYPE_TO_LEVEL = {
    "IfcWall": FF1,
    "IfcWallStandardCase": FF1,
    "IfcDoor": FF1,
    "IfcWindow": FF1,
    "IfcSlab": FF1,          # the floor
    "IfcRoof": PLATE1,
}

# Element NAME keyword -> canonical level.  Checked before TYPE_TO_LEVEL, so a name
# match always wins regardless of element type or original storey.  Names are matched
# case-insensitively as substrings (Revit names look like "Unit Heater - Cabinet:3-6 kW:967641").
NAME_TO_LEVEL = [
    ("UNIT HEATER", GRADE),
]


def _name(s):
    return (s.Name or "").upper()


def target_role(el, src_role):
    """Canonical level for an element: name rule → type rule → original storey's role → FF1."""
    name = (el.Name or "").upper()
    for kw, role in NAME_TO_LEVEL:
        if kw in name:
            return role
    role = TYPE_TO_LEVEL.get(el.is_a())
    if role is None:
        role = src_role or FF1
    return role


def _elev(s):
    return s.Elevation if s.Elevation is not None else 0.0


def classify_storeys(storeys):
    """Map existing IfcBuildingStorey instances onto the 4 canonical roles.

    Returns (role_storey, extras) where role_storey is {GRADE/FF1/PLATE1/TOP: storey-or-None}
    and `extras` is the list of leftover storeys to merge into PLATE1 and delete.
    Classification is name-keyword first, elevation as the tiebreaker; each storey is
    claimed by at most one role.
    """
    by_elev = sorted(storeys, key=_elev)
    role_storey = {GRADE: None, FF1: None, PLATE1: None, TOP: None}
    claimed = set()

    def claim(role, keywords, pick_highest=False):
        cands = [s for s in by_elev if s.id() not in claimed
                 and any(k in _name(s) for k in keywords)]
        if not cands:
            return
        s = cands[-1] if pick_highest else cands[0]
        role_storey[role] = s
        claimed.add(s.id())

    # 1) keyword matches.  Order matters: claim TOP ("TOP OF ROOF"/"ROOF TOP") before PLATE1
    #    so the roof-bearing plate (which may also say "ROOF", e.g. "BOTTOM OF ROOF") doesn't
    #    grab the top-of-roof datum.
    claim(GRADE, ("GRADE",))
    claim(FF1, ("FINISH", "FF", "LEVEL"))
    claim(TOP, ("TOP",), pick_highest=True)
    claim(PLATE1, ("PLATE", "CEILING", "BOTTOM OF ROOF", "ROOF"), pick_highest=True)

    # 2) elevation fallbacks for any unfilled role, never reusing a storey.
    free = [s for s in by_elev if s.id() not in claimed]
    if role_storey[GRADE] is None and free:
        role_storey[GRADE] = free.pop(0)          # lowest remaining
    if role_storey[FF1] is None and free:
        role_storey[FF1] = free.pop(0)            # next above grade
    if role_storey[TOP] is None and free:
        role_storey[TOP] = free.pop(-1)           # highest remaining
    if role_storey[PLATE1] is None and free:
        role_storey[PLATE1] = free.pop(-1)        # next highest remaining

    # 3) everything still unassigned merges into PLATE1.
    assigned = {s.id() for s in role_storey.values() if s is not None}
    extras = [s for s in storeys if s.id() not in assigned]
    return role_storey, extras


def _storey_role_lookup(role_storey, extras):
    """STEP id(storey) -> canonical role.  Extras resolve to PLATE1 (merged into it).

    Keyed on the entity's STEP id (`.id()`), NOT Python `id()`: ifcopenshell hands back
    a fresh wrapper object on every attribute access, so `id(rel.RelatingStructure)`
    would never match the storey objects we classified.
    """
    lookup = {}
    for role, s in role_storey.items():
        if s is not None:
            lookup[s.id()] = role
    for s in extras:
        lookup[s.id()] = PLATE1
    return lookup


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline
# ══════════════════════════════════════════════════════════════════════════════

def organize(src_path, out_path):
    src_path, out_path = str(src_path), str(out_path)
    print(f"\n{'=' * 70}\n{Path(src_path).name}  →  {Path(out_path).name}\n{'=' * 70}")

    # Work on a copy — the original is never modified.
    shutil.copy2(src_path, out_path)
    model = ifcopenshell.open(out_path)

    buildings = model.by_type("IfcBuilding")
    if not buildings:
        print("  no IfcBuilding found — nothing to organize")
        model.write(out_path)
        return
    building = buildings[0]

    storeys = list(model.by_type("IfcBuildingStorey"))
    print(f"Storeys found: {len(storeys)}")
    for s in sorted(storeys, key=_elev):
        print(f"    Elev={_elev(s):>10.4f}  {s.Name!r}")

    role_storey, extras = classify_storeys(storeys)
    _ensure_top(model, role_storey, building)
    print("\n[Step 2/3] Canonical mapping (name + elevation):")
    for role in LEVELS_LOW_TO_HIGH:
        s = role_storey[role]
        label = repr(s.Name) if s else "(created)"
        print(f"    {role:7s} ← {label}")
    if extras:
        print(f"    extras → merged into {PLATE1}: {[e.Name for e in extras]}")

    storey_role = _storey_role_lookup(role_storey, extras)

    # Step 5 — recompute the target canonical role for every contained element.
    # role -> list of element instances destined for that role's storey.
    target_elements = defaultdict(list)
    rels = list(model.by_type("IfcRelContainedInSpatialStructure"))
    for rel in rels:
        struct = rel.RelatingStructure
        if not struct.is_a("IfcBuildingStorey"):
            continue  # spaces etc. keep their own containment
        src_role = storey_role.get(struct.id())
        for el in rel.RelatedElements:
            target_elements[target_role(el, src_role)].append(el)

    # Step 4 — rename chosen storeys to the canonical names.
    for role in LEVELS_LOW_TO_HIGH:
        if role_storey[role] is not None:
            role_storey[role].Name = role

    # Re-home: rebuild one IfcRelContainedInSpatialStructure per canonical storey.
    # Drop all existing storey-containment rels first, then write fresh consolidated ones.
    print("\n[Step 5] Re-homing elements by type")
    for rel in rels:
        if rel.RelatingStructure.is_a("IfcBuildingStorey"):
            model.remove(rel)

    owner = building.OwnerHistory
    for role in LEVELS_LOW_TO_HIGH:
        storey = role_storey[role]
        els = target_elements.get(role, [])
        if storey is None or not els:
            if els:
                print(f"    WARNING: {len(els)} elements destined for missing {role}")
            continue
        # de-dup while preserving order
        seen, uniq = set(), []
        for e in els:
            if e.id() not in seen:
                seen.add(e.id())
                uniq.append(e)
        model.create_entity(
            "IfcRelContainedInSpatialStructure",
            GlobalId=ifcopenshell.guid.new(),
            OwnerHistory=owner,
            Name=f"{role} container",
            RelatedElements=uniq,
            RelatingStructure=storey,
        )
        cnt = Counter(e.is_a() for e in uniq)
        print(f"    {role:7s}: {dict(cnt)}")

    # Step 7 — re-parent spaces under removed storeys, then delete extra storeys.
    keep = {role_storey[r].id() for r in LEVELS_LOW_TO_HIGH if role_storey[r]}
    for agg in list(model.by_type("IfcRelAggregates")):
        relating = agg.RelatingObject
        if relating.is_a("IfcBuildingStorey") and relating.id() not in keep:
            # move its aggregated children (e.g. IfcSpace) onto FF1
            ff = role_storey[FF1] or role_storey[GRADE] or role_storey[PLATE1]
            for child in agg.RelatedObjects:
                _aggregate_under(model, ff, child, owner)
            model.remove(agg)

    # Rebuild the building -> storey aggregation to exactly the canonical storeys (low → high).
    canon = [role_storey[r] for r in LEVELS_LOW_TO_HIGH if role_storey[r]]
    for agg in list(model.by_type("IfcRelAggregates")):
        if agg.RelatingObject == building:
            agg.RelatedObjects = canon
            break

    # Delete the now-orphaned extra storeys.
    for e in extras:
        model.remove(e)

    model.write(out_path)
    print(f"\nWritten: {out_path}")

    verify(src_path, out_path)


def _ensure_top(model, role_storey, building):
    """Guarantee a TOP ("Top Of Roof") level exists.  If no storey mapped to TOP, create an
    empty one just above PLATE1 (datum level), reusing the base storey's placement frame so
    geometry is unaffected."""
    if role_storey[TOP] is not None:
        return
    base = role_storey[PLATE1] or role_storey[FF1] or role_storey[GRADE]
    if base is None:
        return
    base_pl = base.ObjectPlacement
    rel_to = base_pl.PlacementRelTo if base_pl else None
    z = _elev(base) + 1.0
    loc = model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, z))
    axis = model.create_entity("IfcAxis2Placement3D", Location=loc)
    pl = model.create_entity("IfcLocalPlacement", PlacementRelTo=rel_to, RelativePlacement=axis)
    role_storey[TOP] = model.create_entity(
        "IfcBuildingStorey",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=building.OwnerHistory,
        Name=TOP,
        ObjectPlacement=pl,
        Elevation=z,
    )


def _aggregate_under(model, parent, child, owner):
    """Add `child` to an existing IfcRelAggregates under `parent`, or create one."""
    for agg in model.by_type("IfcRelAggregates"):
        if agg.RelatingObject == parent:
            if child not in agg.RelatedObjects:
                agg.RelatedObjects = list(agg.RelatedObjects) + [child]
            return
    model.create_entity(
        "IfcRelAggregates",
        GlobalId=ifcopenshell.guid.new(),
        OwnerHistory=owner,
        RelatedObjects=[child],
        RelatingObject=parent,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Verification
# ══════════════════════════════════════════════════════════════════════════════

_COUNT_TYPES = ("IfcWall", "IfcWallStandardCase", "IfcDoor", "IfcWindow",
                "IfcSlab", "IfcRoof", "IfcCovering", "IfcBuildingElementProxy",
                "IfcSpace")


def _world_z(el):
    """Absolute Z of an element's local placement origin, walking PlacementRelTo."""
    z, pl = 0.0, el.ObjectPlacement
    guard = 0
    while pl is not None and pl.is_a("IfcLocalPlacement") and guard < 50:
        rp = pl.RelativePlacement
        if rp and rp.Location and rp.Location.Coordinates:
            coords = rp.Location.Coordinates
            if len(coords) == 3:
                z += coords[2]
        pl = pl.PlacementRelTo
        guard += 1
    return round(z, 6)


def verify(src_path, out_path):
    print("\n[Verify]")
    before = ifcopenshell.open(src_path)
    after = ifcopenshell.open(out_path)

    # Storeys
    expected = sorted(LEVELS_LOW_TO_HIGH)
    sts = list(after.by_type("IfcBuildingStorey"))
    names = sorted(s.Name for s in sts)
    ok_storeys = names == expected
    print(f"  storeys: {names}  [{'OK' if ok_storeys else 'EXPECTED GRADE/FF1/PLATE1/TOP'}]")

    # Aggregation under building
    bldg = after.by_type("IfcBuilding")[0]
    agg_children = []
    for agg in after.by_type("IfcRelAggregates"):
        if agg.RelatingObject == bldg:
            agg_children = sorted(c.Name for c in agg.RelatedObjects)
    ok_agg = agg_children == expected
    print(f"  building aggregates: {agg_children}  [{'OK' if ok_agg else 'MISMATCH'}]")

    # Containment per storey
    print("  containment:")
    contained = {}
    for rel in after.by_type("IfcRelContainedInSpatialStructure"):
        st = rel.RelatingStructure
        if st.is_a("IfcBuildingStorey"):
            contained[st.Name] = Counter(e.is_a() for e in rel.RelatedElements)
    for role in LEVELS_LOW_TO_HIGH:
        print(f"    {role:7s}: {dict(contained.get(role, {}))}")
    roof_in_plate = contained.get(PLATE1, {}).get("IfcRoof", 0) >= 1 \
        or "IfcRoof" not in [t for c in contained.values() for t in c]
    ff = contained.get(FF1, {})
    ff_ok = ff.get("IfcWall", 0) + ff.get("IfcWallStandardCase", 0) >= 0
    print(f"    roof under {PLATE1}: {'OK' if roof_in_plate else 'NO'}")

    # Element counts preserved (nothing dropped)
    print("  counts (before → after):")
    all_ok = True
    for t in _COUNT_TYPES:
        nb, na = len(before.by_type(t)), len(after.by_type(t))
        if nb == 0 and na == 0:
            continue
        ok = nb == na
        all_ok &= ok
        print(f"    {t:28s} {nb} → {na}  [{'OK' if ok else 'CHANGED!'}]")

    # Geometry preserved — sample element world Z before/after by GlobalId
    bz = {e.GlobalId: _world_z(e) for e in before.by_type("IfcProduct")
          if e.ObjectPlacement}
    moved = 0
    for e in after.by_type("IfcProduct"):
        if e.GlobalId in bz and e.ObjectPlacement:
            if _world_z(e) != bz[e.GlobalId]:
                moved += 1
    print(f"  geometry: {moved} elements moved in Z  "
          f"[{'OK — none moved' if moved == 0 else 'CHANGED!'}]")

    overall = ok_storeys and ok_agg and roof_in_plate and all_ok and moved == 0
    print(f"\n  RESULT: {'ALL CHECKS PASSED ✓' if overall else 'SEE WARNINGS ABOVE'}")


def _out_path_for(src_path):
    """Insert '-L1' before the extension: 'NAME.ifc' → 'NAME-L1.ifc'."""
    p = Path(src_path)
    return p.with_name(f"{p.stem}-L1{p.suffix}")


def main():
    if len(sys.argv) >= 3:
        organize(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2:
        organize(sys.argv[1], _out_path_for(sys.argv[1]))
    else:
        ifc_dir = Path(__file__).parent / "IFCs"
        srcs = sorted(p for p in ifc_dir.glob("*.ifc")
                      if not p.stem.endswith("-L1"))
        if not srcs:
            print(f"No source .ifc files found in {ifc_dir}")
            sys.exit(1)
        for src in srcs:
            organize(src, _out_path_for(src))
    print("\nDone.")


if __name__ == "__main__":
    main()
