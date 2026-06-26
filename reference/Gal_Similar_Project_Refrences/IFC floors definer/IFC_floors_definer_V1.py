#!/usr/bin/env python3
"""
IFC_floors_definer_V1.py — Gaudi IFC pipeline stage: FLOORS.

Takes a (walls-cleaned) Revit-exported IFC and works on the FLOORS, leaving every
other element (walls, doors, windows, spaces, coverings, materials, storeys, …)
exactly as-is.  This is one stage of the "Gaudi IFC" pipeline.

The original file is never modified.  The result is written next to it with the
suffix "-F1" before the extension, e.g.
    IFCs/SAN JUAN CYPRESS - AUG 2-W1-L1.ifc  →  IFCs/SAN JUAN CYPRESS - AUG 2-W1-L1-F1.ifc

Pipeline (see "IFC floors definer algorithm.md" for full detail):

  Step 0 — rebuild interior floor; split into main unit / deck / stair.  Union the
    interior floor slabs (true union, not bbox); split by the **unit-bounding** wall
    envelope (exterior + interior walls; DESIGN walls excluded, so their area is exterior)
    into: the main unit (`Floor:`, → Hardwood), the beyond-envelope deck incl. under a
    design wall (`Deck:`, → Concrete), and any narrow stepped-down piece (`Stair:`).  Each
    region becomes a fresh clean ``+Z`` slab whose z is taken PER REGION from the source
    slab covering it (so a layered buildup keeps its true finished-floor level).  Originals
    deleted, space boundaries repointed.  A single interior floor fully inside the walls is
    left untouched.
  Step 1 — normalize floor names on ``IfcSlab`` AND ``IfcSlabType``:
      1. add a space after the prefix colon:  ``Floor:X`` → ``Floor: X``.
      2. re-prefix EXTERIOR floors by type (keyword in the name, case-insensitive):
         "deck" → ``Deck: ``, "porch" → ``Porch: ``, "patio" → ``Deck: ``;
         otherwise (interior) keep ``Floor: ``.  The matched keyword is dropped from
         the descriptor.  Only the leading prefix changes; the trailing ``:id`` is kept.
      3. reclassify exterior surfaces that are actually STAIRS → ``Stair: `` — a slab
         whose top is > 1" below the interior floor top AND whose footprint is shallow
         (< 2 ft) is a stair step (geometry-based, per slab instance; a slabtype is
         relabelled only when all its exterior instances are stairs).
  Step 2 — add finish coverings (IfcCovering FLOORING) flush on each floor's top:
      interior → Hardwood, exterior deck/patio → Concrete, stair → its attached deck's
      finish.  A BATHROOM (the room around a toilet fixture — its enclosing IfcSpace, or,
      when no space exists, the room derived from the wall network) is laid as a Ceramic
      Tile covering and deducted from the interior Hardwood.  EVERY covering has the wall
      footprints subtracted, so no finish runs under a wall (perimeter stops at the inner
      wall face).  Interior and tile share thickness, so their tops are coplanar.

Everything not floor-related (walls, doors, windows, spaces, storeys, roof, …) is left
as-is.  The original file is never modified; output is written to the "-F1" copy.

Env flags: FLOORS_DEFINER_FINISH=0 skips Step 2; FLOORS_DEFINER_DROP_INTERIOR_SLAB=1
deletes the merged interior slab (diagnostics).
"""

import os
import re
import shutil
import sys
from pathlib import Path

import numpy as np
from shapely.geometry import LineString, MultiPolygon, Point, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import polygonize, unary_union

import ifcopenshell
import ifcopenshell.api
import ifcopenshell.guid
import ifcopenshell.util.element
import ifcopenshell.util.placement as ifc_placement


# ── Classification ──────────────────────────────────────────────────────────────

# Keyword (lower-case, matched anywhere in the name) → output prefix.
# Order matters only if a name contained more than one keyword (first wins).
EXTERIOR_PREFIXES = [
    ("deck",  "Deck"),
    ("porch", "Porch"),
    ("patio", "Deck"),   # patios are labelled as decks
]

PREFIX = "Floor:"

# Output prefixes a floor name can carry after the rename step (and thus get a finish).
PREFIX_SET = ("Floor:", "Deck:", "Porch:", "Patio:", "Stair:")

# Step 2 — floor finishes.  Maps the classified prefix word → (material name,
# finish thickness).  Thickness is in the FILE's length unit (feet for the San Juan
# model: 0.0625 ft = 3/4", 0.16667 ft = 2").  Made unit-aware in add_floor_finishes().
FINISH_BY_PREFIX = {
    "Floor": ("Hardwood", 0.041667),  # interior, 1/2"
    "Porch": ("Concrete", 0.16667),   # exterior, 2"
    "Deck":  ("Concrete", 0.16667),   # decks (and patios, mapped to Deck)
}
DEFAULT_FINISH = ("Hardwood", 0.041667)

# Bathrooms are detected by a TOILET fixture → its enclosing IfcSpace → the space
# footprint (the wall-bounded room outline). That region is laid as a tile covering and
# deducted from the surrounding hardwood. Toilets are matched by name/ObjectType keyword.
TOILET_KEYWORDS = ("toilet", "wc", "water closet", "watercloset", "water_closet")
TILE_FINISH = ("Ceramic Tile", 0.041667)   # 1/2"

# Fallback when a bathroom has no IfcSpace: wall axis centerlines are extended by this
# much (feet) so near-meeting corners connect, then polygonized into rooms.
WALL_ROOM_EXTEND = 1.0

# Stair detection: an EXTERIOR surface (deck/patio/porch) is reclassified as a stair and
# labelled "Stair:" when its top sits more than STAIR_DROP_MIN below the interior floor
# top AND its footprint is shallow (smaller plan dimension < STAIR_MAX_DEPTH). In feet.
STAIR_DROP_MIN = 1.0 / 12.0     # 1"
STAIR_MAX_DEPTH = 2.0           # 2 ft

# A beyond-the-walls region is only split off as its own deck/stair slab if it is at least
# this big (sqft).  Suppresses floor-edge noise — the interior slab poking a few inches past
# the wall envelope — while keeping genuine decks/stairs (the smallest real one seen is a
# ~10 sqft stair). Sub-threshold slivers stay part of the interior floor.
MIN_DECK_AREA = 5.0             # sqft

# Set FLOORS_DEFINER_FINISH=0 to skip Step 2 entirely (bare slabs, no coverings) —
# useful for inspecting the merged slab geometry on its own.
ADD_FINISHES = os.environ.get("FLOORS_DEFINER_FINISH", "1") != "0"

# Diagnostic: set FLOORS_DEFINER_DROP_INTERIOR_SLAB=1 to delete the merged interior
# floor slab entirely (leaving deck/roof/walls), to inspect the rest of the model.
DROP_INTERIOR_SLAB = os.environ.get("FLOORS_DEFINER_DROP_INTERIOR_SLAB", "0") == "1"


def _classify(name: str):
    """Return ``(prefix_word, matched_keyword_or_None)`` for a floor name."""
    low = name.lower()
    for keyword, prefix in EXTERIOR_PREFIXES:
        if keyword in low:
            return prefix, keyword
    return "Floor", None


def classify_floor(name: str) -> str:
    """Return the output prefix word for a floor name: Floor / Deck / Porch / Patio."""
    return _classify(name)[0]


def _interior_top_z(model):
    """World Z of the highest interior floor (`Floor:`-prefixed, classifies interior)."""
    tops = []
    for s in model.by_type("IfcSlab"):
        if (s.Name or "").startswith(PREFIX) and classify_floor(s.Name) == "Floor":
            faces = _extrusion_faces_world_z(s)
            if faces:
                tops.append(max(faces))
    return max(tops) if tops else None


def _is_stair(slab, interior_top):
    """
    True if an exterior slab is actually a stair: its top sits more than STAIR_DROP_MIN
    below the interior floor top, and its footprint is shallow (smaller plan dimension
    < STAIR_MAX_DEPTH).
    """
    if interior_top is None:
        return False
    faces = _extrusion_faces_world_z(slab)
    poly = _world_polygon(slab)
    if not faces or poly is None:
        return False
    drop = interior_top - max(faces)
    minx, miny, maxx, maxy = poly.bounds
    depth = min(maxx - minx, maxy - miny)
    return drop > STAIR_DROP_MIN and depth < STAIR_MAX_DEPTH


def _renamed(name, prefix_override=None) -> str:
    """
    Normalize a ``Floor:…`` name.  Strips the ``Floor:`` prefix and any space that
    follows it, then re-prefixes with the classified word (or `prefix_override`) + a
    single space, so the descriptor (which itself may contain colons / quotes / a
    trailing :id) is preserved.  Returns the name unchanged if it is missing or not
    ``Floor:``-prefixed.

    For exterior floors the matched keyword is now redundant with the new prefix, so
    it is removed from the descriptor along with any leading ``_``/space separator,
    e.g. ``Floor:Generic - 11" porch:547963`` → ``Porch: Generic - 11":547963``.
    `prefix_override` (e.g. "Stair") sets the prefix while still stripping the matched
    exterior keyword: ``Floor:Generic - 21"_DECK`` → ``Stair: Generic - 21"``.
    """
    if not name or not name.startswith(PREFIX):
        return name
    rest = name[len(PREFIX):].lstrip()          # drop "Floor:" + any existing space
    prefix, keyword = _classify(name)
    prefix = prefix_override or prefix
    if keyword:
        # Drop the redundant keyword (+ a leading "_" / space separator), case-insensitive.
        rest = re.sub(r"[\s_]*" + re.escape(keyword), "", rest, flags=re.IGNORECASE)
        rest = re.sub(r"\s{2,}", " ", rest).strip()
    return f"{prefix}: {rest}"


# ── Step 2: floor finishes (IfcCovering) ────────────────────────────────────────

FOOT_TO_M = 0.3048


def _body_solid(elem):
    """Return an element's extruded-area solid (unwrapping any boolean clip).

    Prefers the 'Body' representation, then falls back to any representation — so it also
    works for IfcSpace (whose swept solid may not be tagged 'Body')."""
    rep = elem.Representation
    if not rep:
        return None
    reps = sorted(rep.Representations,
                  key=lambda r: 0 if r.RepresentationIdentifier == "Body" else 1)
    for r in reps:
        for item in r.Items:
            while item and item.is_a("IfcBooleanResult"):   # covers IfcBooleanClippingResult
                item = item.FirstOperand
            if item and item.is_a("IfcExtrudedAreaSolid"):
                return item
    return None


def _clone_point(model, pt):
    return model.createIfcCartesianPoint(tuple(float(c) for c in pt.Coordinates))


def _clone_dir(model, d):
    return model.createIfcDirection(tuple(float(c) for c in d.DirectionRatios)) if d else None


def _make_covering_solid(model, slab, profile, thickness):
    """
    Build a thin IfcExtrudedAreaSolid from `profile` that sits flush on the slab's TOP
    face and grows upward (world +Z), expressed in the slab's solid-local frame.

    The slab solid has two end faces — at its Position origin and at
    origin + R_pos·(ext_local·Depth).  We transform both into world coordinates, pick
    the one with the higher world Z as the TOP face, place the profile there (reusing the
    slab solid's axes), and set the extrude direction to world-up mapped back into that
    frame.  Derived entirely from the slab's own placement, so it is correct regardless
    of the slab's (often Revit-flipped) extrusion axis.  `profile` must be expressed in
    the same solid-local 2D coordinates as the slab's own SweptArea.
    """
    solid = _body_solid(slab)
    if solid is None:
        return None

    op = ifc_placement.get_local_placement(slab.ObjectPlacement)   # object-local → world
    op_r = op[:3, :3]
    pos_m = ifc_placement.get_axis2placement(solid.Position)        # solid → object-local
    r_pos = pos_m[:3, :3]
    origin = pos_m[:3, 3]
    ext_local = np.array(solid.ExtrudedDirection.DirectionRatios, float)
    ext_local /= np.linalg.norm(ext_local)

    # The two extrusion end faces, in object-local then world coords.
    face0 = origin
    face1 = origin + r_pos @ (ext_local * float(solid.Depth))
    z0 = (op_r @ face0 + op[:3, 3])[2]
    z1 = (op_r @ face1 + op[:3, 3])[2]
    top_face = face0 if z0 >= z1 else face1

    # World +Z mapped into the solid-local frame → the covering's extrude direction.
    up_solid_local = r_pos.T @ (op_r.T @ np.array([0.0, 0.0, 1.0]))
    up_solid_local /= np.linalg.norm(up_solid_local)

    sp = solid.Position
    new_pos = model.createIfcAxis2Placement3D(
        model.createIfcCartesianPoint(tuple(float(x) for x in top_face)),
        _clone_dir(model, sp.Axis),
        _clone_dir(model, sp.RefDirection))
    ext_dir = model.createIfcDirection(tuple(float(x) for x in up_solid_local))
    return model.createIfcExtrudedAreaSolid(profile, new_pos, ext_dir, float(thickness))


# ── Footprint geometry (shapely) ─────────────────────────────────────────────────

def _xy_affine(slab):
    """2D affine (A, t) mapping slab solid-local 2D → world XY, plus its inverse."""
    solid = _body_solid(slab)
    M = ifc_placement.get_local_placement(slab.ObjectPlacement) \
        @ ifc_placement.get_axis2placement(solid.Position)
    A, t = M[:2, :2], M[:2, 3]
    return A, t, np.linalg.inv(A)


def _profile_rings_2d(profile):
    """(outer_pts, [inner_pts...]) of a profile in solid-local 2D, no closing dup."""
    def pts(curve):
        cs = [tuple(p.Coordinates[:2]) for p in curve.Points]
        if len(cs) > 1 and cs[0] == cs[-1]:
            cs = cs[:-1]
        return cs
    if profile.is_a("IfcRectangleProfileDef"):
        hx, hy = profile.XDim / 2.0, profile.YDim / 2.0
        pos = profile.Position
        loc = np.array(pos.Location.Coordinates if pos else (0.0, 0.0), float)
        # Apply the profile's 2D placement (location AND RefDirection rotation). Ignoring
        # RefDirection rotates the rectangle wrong (e.g. a wall's profile is rotated 90°).
        rd = (pos.RefDirection.DirectionRatios if (pos and pos.RefDirection) else (1.0, 0.0))
        ux = np.array(rd, float)
        ux /= (np.linalg.norm(ux) or 1.0)
        uy = np.array([-ux[1], ux[0]])
        return [tuple(loc + sx * hx * ux + sy * hy * uy)
                for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))], []
    if profile.is_a("IfcArbitraryProfileDefWithVoids"):
        return pts(profile.OuterCurve), [pts(c) for c in profile.InnerCurves]
    if profile.is_a("IfcArbitraryClosedProfileDef"):
        return pts(profile.OuterCurve), []
    raise ValueError(f"unsupported profile: {profile.is_a()}")


def _world_polygon(slab):
    """Shapely Polygon of the slab footprint in world XY, or None if it has no
    extruded-area body (e.g. a mesh/Brep wall)."""
    solid = _body_solid(slab)
    if solid is None:
        return None
    A, t, _ = _xy_affine(slab)
    outer, inners = _profile_rings_2d(solid.SweptArea)
    f = lambda p: tuple(A @ np.array(p, float) + t)
    return Polygon([f(p) for p in outer], [[f(p) for p in r] for r in inners])


def _as_polygons(geom):
    """Normalize a shapely result to a list of Polygons (drops slivers)."""
    if geom.is_empty:
        return []
    polys = list(geom.geoms) if isinstance(geom, MultiPolygon) else [geom]
    return [p for p in polys if p.area > 1e-9]


def _profile_from_polygon(model, slab, poly):
    """Build an IfcArbitrary(Closed/WithVoids)ProfileDef from a world-XY shapely Polygon,
    converting coordinates into `slab`'s solid-local 2D frame."""
    _, t, Ainv = _xy_affine(slab)
    to_local = lambda x, y: tuple(Ainv @ (np.array([x, y], float) - t))
    poly = orient(poly, 1.0)   # outer CCW, holes CW

    def polyline(coords):
        loc = [to_local(x, y) for x, y in coords]
        if loc[0] != loc[-1]:
            loc.append(loc[0])
        return model.createIfcPolyline(
            [model.createIfcCartesianPoint((float(u), float(v))) for u, v in loc])

    outer = polyline(poly.exterior.coords)
    if poly.interiors:
        inners = [polyline(r.coords) for r in poly.interiors]
        return model.createIfcArbitraryProfileDefWithVoids("AREA", None, outer, inners)
    return model.createIfcArbitraryClosedProfileDef("AREA", None, outer)


def _world_poly_profile(model, poly):
    """Profile for a NEW slab placed at the world origin (identity placement) — its 2D
    coords ARE world XY.  A clean axis-aligned box → parametric `IfcRectangleProfileDef`
    (most robust for viewers); otherwise an `IfcArbitraryClosedProfileDef`/`…WithVoids`."""
    poly = orient(poly, 1.0)
    minx, miny, maxx, maxy = poly.bounds
    if not poly.interiors and abs(poly.area - (maxx - minx) * (maxy - miny)) < 1e-6:
        return model.createIfcRectangleProfileDef(
            "AREA", None,
            model.createIfcAxis2Placement2D(
                model.createIfcCartesianPoint(((minx + maxx) / 2.0, (miny + maxy) / 2.0)),
                None),
            float(maxx - minx), float(maxy - miny))

    def ring(coords):
        c = [(float(x), float(y)) for x, y in coords]
        if c[0] != c[-1]:
            c.append(c[0])
        return model.createIfcPolyline([model.createIfcCartesianPoint(p) for p in c])

    outer = ring(poly.exterior.coords)
    if poly.interiors:
        return model.createIfcArbitraryProfileDefWithVoids(
            "AREA", None, outer, [ring(r.coords) for r in poly.interiors])
    return model.createIfcArbitraryClosedProfileDef("AREA", None, outer)


def _create_slab_from_poly(model, poly, z0, z1, name, owner, body_ctx, predefined="FLOOR"):
    """
    Create a brand-new clean IfcSlab from world-XY `poly`, from z0 to z1: a single upward
    `+Z` extrusion at an identity world placement (no Revit placement chain / boolean /
    flipped axis).  Returns the new IfcSlab.
    """
    pos = model.createIfcAxis2Placement3D(
        model.createIfcCartesianPoint((0.0, 0.0, float(z0))),
        model.createIfcDirection((0.0, 0.0, 1.0)),
        model.createIfcDirection((1.0, 0.0, 0.0)))
    solid = model.createIfcExtrudedAreaSolid(
        _world_poly_profile(model, poly), pos,
        model.createIfcDirection((0.0, 0.0, 1.0)), float(z1 - z0))
    rep = model.createIfcShapeRepresentation(body_ctx, "Body", "SweptSolid", [solid])
    placement = model.createIfcLocalPlacement(
        None, model.createIfcAxis2Placement3D(
            model.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None))
    return model.create_entity(
        "IfcSlab",
        GlobalId=ifcopenshell.guid.new(), OwnerHistory=owner, Name=name,
        ObjectPlacement=placement,
        Representation=model.createIfcProductDefinitionShape(None, None, [rep]),
        PredefinedType=predefined)


# ── Step 0: rebuild interior floor + split off deck / stair ─────────────────────

def _region_z(region, src):
    """(z_bottom, z_top) for an output region polygon, taken from the source slabs that
    cover it: z_top = top of the dominant (largest-overlap) slab; z_bottom = min bottom of
    overlapping slabs.  Preserves the true finished-floor level per region (no 'too low')."""
    over = [(p.intersection(region).area, z0, z1) for p, z0, z1 in src
            if p.intersects(region) and p.intersection(region).area > 1e-6]
    if not over:
        zs = [z for _, z0, z1 in src for z in (z0, z1)]
        return (min(zs), max(zs)) if zs else (0.0, 0.0)
    z_top = max(over, key=lambda o: o[0])[2]
    z_bottom = min(o[1] for o in over)
    return z_bottom, z_top


def merge_interior_floors(model):
    """
    Rebuild the interior floor and split it into up to three element kinds:
      - **main unit** (`Floor:`, → Hardwood): the area inside the unit-bounding (exterior +
        interior, NOT design) wall envelope.
      - **deck/exterior** (`Deck:`, → Concrete): area beyond the envelope (incl. the area
        under a *design* wall), wide enough not to be a stair.
      - **stair** (`Stair:`, → Concrete): a beyond-envelope piece that is narrow
        (`< STAIR_MAX_DEPTH`) AND steps down (top `> STAIR_DROP_MIN` below the main floor).

    Interior floor slabs are unioned (TRUE union, not bbox). Each output region becomes a
    fresh clean `+Z` slab at an identity placement (discarding Revit boolean/flipped-axis
    geometry); its z is taken **per region** from the source slab covering it (`_region_z`),
    so a layered buildup keeps its real finished-floor level. Space boundaries + storey
    containment are repointed. Runs when there are multiple interior slabs OR a beyond-
    envelope region; a single interior floor fully inside the walls is left untouched.

    Returns {'merged_slab', 'removed_slabs', 'removed_types'}.
    """
    empty = {"merged_slab": None, "removed_slabs": 0, "removed_types": 0}
    interior = [s for s in model.by_type("IfcSlab")
                if (s.Name or "").startswith(PREFIX) and classify_floor(s.Name) == "Floor"
                and _world_polygon(s) is not None]
    if not interior:
        return empty

    src = [(_world_polygon(s), *sorted(_extrusion_faces_world_z(s))) for s in interior]
    union = unary_union([p for p, _, _ in src])

    # Unit envelope from exterior + interior walls (design walls excluded → their area is
    # exterior). main = union ∩ envelope; beyond = union − envelope.
    try:
        import ifcopenshell.util.unit as ifc_unit
        ft = FOOT_TO_M / ifc_unit.calculate_unit_scale(model)
    except Exception:
        ft = 1.0

    rooms, wall_mask = _wall_rooms(model)
    env_parts = ([unary_union(rooms)] if rooms else []) + ([wall_mask] if wall_mask else [])
    envelope = unary_union(env_parts) if env_parts else None
    # Beyond the wall envelope: only regions ≥ MIN_DECK_AREA are genuine decks/stairs;
    # smaller pieces are floor-edge noise and stay part of the interior floor.
    beyond_all = _as_polygons(union.difference(envelope)) if envelope else []
    beyond_pieces = [p for p in beyond_all if p.area >= MIN_DECK_AREA * ft * ft]
    main_poly = union.difference(unary_union(beyond_pieces)) if beyond_pieces else union
    main_pieces = _as_polygons(main_poly)

    if len(interior) <= 1 and not beyond_pieces:
        return empty   # single interior floor, nothing genuine beyond the walls
    main_top = max((_region_z(p, src)[1] for p in main_pieces), default=max(
        (z1 for _, _, z1 in src), default=0.0))

    ref = max(interior, key=lambda s: _world_polygon(s).area)
    desc = ref.Name[len(PREFIX):].lstrip()
    body_ctx = next((c for c in model.by_type("IfcGeometricRepresentationContext")
                     if getattr(c, "ContextIdentifier", None) == "Body"), None)
    owner = (model.by_type("IfcOwnerHistory") or [None])[0]
    storey_rel = _slab_storey_rel(model, ref)

    def mk(piece, name):
        zb, zt = _region_z(piece, src)
        return _create_slab_from_poly(model, piece, zb, zt, name, owner, body_ctx)

    new_floor = [mk(p, ref.Name) for p in main_pieces]
    new_deck, new_stair = [], []
    for p in beyond_pieces:
        zb, zt = _region_z(p, src)
        minx, miny, maxx, maxy = p.bounds
        narrow = min(maxx - minx, maxy - miny) < STAIR_MAX_DEPTH * ft
        if narrow and (main_top - zt) > STAIR_DROP_MIN * ft:
            new_stair.append(mk(p, f"Stair: {desc}".rstrip()))
        else:
            new_deck.append(mk(p, f"Deck: {desc}".rstrip()))
    new_all = new_floor + new_deck + new_stair
    print(f"  main unit: {len(new_floor)} slab(s) {sum(p.area for p in main_pieces):.0f} sqft;"
          f" deck: {len(new_deck)}; stair: {len(new_stair)}")

    target = new_floor[0] if new_floor else (new_all[0] if new_all else None)
    repointed = 0
    for rb in model.by_type("IfcRelSpaceBoundary"):
        be = rb.RelatedBuildingElement
        if be is not None and any(be.id() == s.id() for s in interior):
            rb.RelatedBuildingElement = target
            repointed += 1
    if storey_rel is not None:
        storey_rel.RelatedElements = list(storey_rel.RelatedElements) + new_all

    for s in interior:
        ifcopenshell.api.run("root.remove_product", model, product=s)
    if repointed:
        print(f"    repointed {repointed} space boundary(ies) to new interior slab")

    used = {r.RelatingType.id() for r in model.by_type("IfcRelDefinesByType")
            if r.RelatingType and r.RelatingType.is_a("IfcSlabType")}
    removed_types = 0
    for st in list(model.by_type("IfcSlabType")):
        if st.id() not in used:
            ifcopenshell.api.run("root.remove_product", model, product=st)
            removed_types += 1

    removed_slabs = len(interior) - len(new_all)
    return {"merged_slab": target, "rebuilt": True,
            "removed_slabs": removed_slabs, "removed_types": removed_types}


def _extrusion_faces_world_z(elem):
    """World Z of the two extrusion end faces of an element's body solid, or None."""
    solid = _body_solid(elem)
    if solid is None or not elem.ObjectPlacement:
        return None
    op = ifc_placement.get_local_placement(elem.ObjectPlacement)
    pm = ifc_placement.get_axis2placement(solid.Position)
    r, o = pm[:3, :3], pm[:3, 3]
    ext = np.array(solid.ExtrudedDirection.DirectionRatios, float)
    ext /= np.linalg.norm(ext)
    f0, f1 = o, o + r @ (ext * float(solid.Depth))
    return ((op[:3, :3] @ f0 + op[:3, 3])[2], (op[:3, :3] @ f1 + op[:3, 3])[2])


def _slab_storey_rel(model, slab):
    """The IfcRelContainedInSpatialStructure that contains this slab, or None."""
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        if slab in rel.RelatedElements:
            return rel
    return None


def _already_finished(model, slab):
    """True if slab already has a FLOORING covering linked via IfcRelCoversBldgElements."""
    for rel in model.by_type("IfcRelCoversBldgElements"):
        if rel.RelatingBuildingElement == slab and any(
                c.is_a("IfcCovering") and c.PredefinedType == "FLOORING"
                for c in rel.RelatedCoverings):
            return True
    return False


def _is_toilet(e):
    """True if element looks like a toilet/WC by its Name or ObjectType."""
    s = ((e.Name or "") + " " + (getattr(e, "ObjectType", "") or "")).lower()
    return any(k in s for k in TOILET_KEYWORDS)


def _enclosing_space(model, elem):
    """The IfcSpace that contains `elem`: via spatial containment, else by world-XY
    point-in-footprint test against each space."""
    for r in model.by_type("IfcRelContainedInSpatialStructure"):
        if r.RelatingStructure and r.RelatingStructure.is_a("IfcSpace") \
                and elem in r.RelatedElements:
            return r.RelatingStructure
    if elem.ObjectPlacement:
        m = ifc_placement.get_local_placement(elem.ObjectPlacement)
        p = Point(m[0, 3], m[1, 3])
        for sp in model.by_type("IfcSpace"):
            poly = _world_polygon(sp)
            if poly is not None and poly.contains(p):
                return sp
    return None


def _safe_by_type(model, ifc_type):
    """model.by_type that returns [] for types absent from the file's schema
    (e.g. IfcSanitaryTerminal does not exist in IFC2X3)."""
    try:
        return model.by_type(ifc_type)
    except RuntimeError:
        return []


def _wall_class(wall):
    """Wall category from its Name: 'exterior' / 'interior' / 'design' / '' (the walls-
    cleanup stage renames walls 'Exterior wall: …' / 'Interior wall: …' / 'Design wall: …')."""
    low = (wall.Name or "").lower()
    for k in ("exterior", "interior", "design"):
        if k in low:
            return k
    return ""


def _all_walls(model):
    return list(model.by_type("IfcWall")) + list(model.by_type("IfcWallStandardCase"))


def _bounding_walls(model):
    """Walls that bound the *unit* — exterior + interior, excluding 'design' feature walls
    (a design wall sits on the exterior deck, so its area must NOT count as interior)."""
    return [w for w in _all_walls(model) if _wall_class(w) != "design"]


def _wall_centerlines(wall):
    """World-XY point lists of a wall's 'Axis' representation polylines."""
    out = []
    M = ifc_placement.get_local_placement(wall.ObjectPlacement)
    A, t = M[:2, :2], M[:2, 3]
    for r in (wall.Representation.Representations if wall.Representation else []):
        if r.RepresentationIdentifier == "Axis":
            for it in r.Items:
                if it.is_a("IfcPolyline"):
                    out.append([tuple(A @ np.array(p.Coordinates[:2], float) + t)
                                for p in it.Points])
    return out


def _wall_footprint_mask(model, walls=None):
    """Union of wall footprints (world XY), or None — used to trim coverings so no finish
    runs under a wall.  Defaults to the unit-bounding walls (excludes design walls)."""
    if walls is None:
        walls = _bounding_walls(model)
    foot = [fp for w in walls if (fp := _world_polygon(w)) is not None]
    return unary_union(foot) if foot else None


def _wall_rooms(model, walls=None):
    """
    Partition the plan into rooms from the WALL NETWORK (for bathrooms with no IfcSpace,
    and for the interior/deck split).  Wall axis centerlines are extended by
    WALL_ROOM_EXTEND (so near-meeting corners connect) and polygonized; centerlines run
    continuously through doorways, so rooms stay sealed.  Defaults to the unit-bounding
    walls (excludes design walls).  Returns (room_polygons, wall_footprint_mask).
    """
    if walls is None:
        walls = _bounding_walls(model)
    try:
        import ifcopenshell.util.unit as ifc_unit
        ext = WALL_ROOM_EXTEND * FOOT_TO_M / ifc_unit.calculate_unit_scale(model)
    except Exception:
        ext = WALL_ROOM_EXTEND

    def extend(p0, p1):
        v = np.array(p1, float) - np.array(p0, float)
        L = np.linalg.norm(v)
        if L < 1e-9:
            return p0, p1
        u = v / L
        return tuple(np.array(p0) - u * ext), tuple(np.array(p1) + u * ext)

    segs = []
    for w in walls:
        for cl in _wall_centerlines(w):
            for a, b in zip(cl[:-1], cl[1:]):
                segs.append(LineString(extend(a, b)))
    cells = list(polygonize(unary_union(segs))) if segs else []
    return cells, _wall_footprint_mask(model, walls)


def _wall_room_poly(cells, wall_mask, x, y):
    """The room cell containing (x, y), trimmed to the inner wall faces."""
    pt = Point(x, y)
    hit = [f for f in cells if f.contains(pt)]
    if not hit:
        return None
    room = min(hit, key=lambda f: f.area)        # innermost enclosing cell
    if wall_mask is not None:
        parts = _as_polygons(room.difference(wall_mask))
        if parts:
            room = max(parts, key=lambda p: p.area)
    return room


def detect_bathroom_polys(model):
    """
    Footprints (world-XY shapely polygons) of bathrooms — spaces / rooms containing a
    toilet fixture.  Preferred: the toilet's enclosing IfcSpace footprint.  Fallback (no
    space, e.g. IFC2X3 exports without rooms): the room derived from the wall network.
    """
    fixture_types = ("IfcSanitaryTerminal", "IfcFlowTerminal",
                     "IfcFurnishingElement", "IfcFurniture")
    toilets = [e for t in fixture_types for e in _safe_by_type(model, t) if _is_toilet(e)]
    if not toilets:
        return []

    seen, polys = set(), []
    cells = wall_mask = None        # computed lazily, only if a fallback is needed
    for wc in toilets:
        sp = _enclosing_space(model, wc)
        if sp is not None:
            if sp.id() in seen:
                continue
            seen.add(sp.id())
            poly = _world_polygon(sp)
        elif wc.ObjectPlacement:
            if cells is None:
                cells, wall_mask = _wall_rooms(model)
            m = ifc_placement.get_local_placement(wc.ObjectPlacement)
            poly = _wall_room_poly(cells, wall_mask, m[0, 3], m[1, 3])
        else:
            poly = None
        if poly is not None and poly.area > 1e-6:
            polys.append(poly)
    return polys


def _attached_deck_finish(model, stair):
    """(material, thickness_ft) of the nearest non-stair exterior deck/patio/porch slab —
    the surface the stair is attached to.  Falls back to the Deck finish."""
    sp = _world_polygon(stair)
    best, best_d = None, None
    for s in model.by_type("IfcSlab"):
        if (s.Name or "").startswith(("Deck:", "Porch:")) and _body_solid(s) is not None:
            d = _world_polygon(s).distance(sp)
            if best_d is None or d < best_d:
                best, best_d = s, d
    if best is None:
        return FINISH_BY_PREFIX["Deck"]
    return FINISH_BY_PREFIX.get(classify_floor(best.Name), FINISH_BY_PREFIX["Deck"])


def add_floor_finishes(model) -> int:
    """
    Create IfcCovering(FLOORING) finishes on top of the floor slabs.

    Per slab: exterior deck/porch/patio → Concrete; stair → its attached deck's finish.
    Interior `Floor:` slabs → Hardwood, but any **bathroom** region (a space containing a
    toilet) is laid as a Ceramic Tile covering and deducted from the hardwood, so the two
    tile the floor with no overlap.
    """
    bathroom_polys = detect_bathroom_polys(model)

    body_ctx = next((c for c in model.by_type("IfcGeometricRepresentationContext")
                     if getattr(c, "ContextIdentifier", None) == "Body"), None)
    owner = (model.by_type("IfcOwnerHistory") or [None])[0]

    # Config thickness is in feet; convert to the file's length unit.
    try:
        import ifcopenshell.util.unit as ifc_unit
        unit_scale = ifc_unit.calculate_unit_scale(model)        # file unit → metre
    except Exception:
        unit_scale = FOOT_TO_M
    ft_to_file = FOOT_TO_M / unit_scale

    mat_cache = {}

    def get_material(name):
        if name not in mat_cache:
            mat_cache[name] = model.create_entity("IfcMaterial", Name=name)
        return mat_cache[name]

    def wire(slab, solid, mat_name, label):
        rep = model.createIfcShapeRepresentation(body_ctx, "Body", "SweptSolid", [solid])
        cov = model.create_entity(
            "IfcCovering",
            GlobalId=ifcopenshell.guid.new(), OwnerHistory=owner,
            Name=f"{label} Finish: {mat_name}",
            ObjectPlacement=slab.ObjectPlacement,
            Representation=model.createIfcProductDefinitionShape(None, None, [rep]),
            PredefinedType="FLOORING")
        model.create_entity(
            "IfcRelAssociatesMaterial",
            GlobalId=ifcopenshell.guid.new(), OwnerHistory=owner,
            RelatedObjects=[cov], RelatingMaterial=get_material(mat_name))
        rel = _slab_storey_rel(model, slab)
        if rel is not None:
            rel.RelatedElements = list(rel.RelatedElements) + [cov]
        model.create_entity(
            "IfcRelCoversBldgElements",
            GlobalId=ifcopenshell.guid.new(), OwnerHistory=owner,
            RelatingBuildingElement=slab, RelatedCoverings=[cov])
        print(f"  [{label:8s}] {slab.Name}  +  {cov.Name}")
        return cov

    # Wall footprints — coverings are trimmed to the inner wall faces (no finish runs
    # under ANY wall, including design walls), so each covering's perimeter excludes the
    # walls. (Step 0's unit envelope, by contrast, excludes design walls.)
    wall_mask = _wall_footprint_mask(model, _all_walls(model))

    def minus_walls(poly):
        return poly.difference(wall_mask) if (poly is not None and wall_mask is not None) else poly

    created = 0

    def cover(slab, world_poly, thick_ft, mat_name, label):
        nonlocal created
        if world_poly is None or world_poly.is_empty:
            return
        for piece in _as_polygons(world_poly):
            prof = _profile_from_polygon(model, slab, piece)
            solid = _make_covering_solid(model, slab, prof, thick_ft * ft_to_file)
            if solid:
                wire(slab, solid, mat_name, label)
                created += 1

    tmat, tthick_ft = TILE_FINISH
    for slab in model.by_type("IfcSlab"):
        name = slab.Name or ""
        if not name.startswith(PREFIX_SET) or _already_finished(model, slab):
            continue

        if name.startswith("Stair:"):
            # Stairs inherit the finish of the deck/patio they are attached to.
            mat_name, thick_ft = _attached_deck_finish(model, slab)
            cover(slab, minus_walls(_world_polygon(slab)), thick_ft, mat_name, "Stair")
            continue

        if not name.startswith("Floor:"):     # exterior deck/porch/patio
            label = classify_floor(name)
            mat_name, thick_ft = FINISH_BY_PREFIX.get(label, DEFAULT_FINISH)
            cover(slab, minus_walls(_world_polygon(slab)), thick_ft, mat_name, label)
            continue

        # Interior floor: Hardwood = floor − bathrooms − walls; tile each bathroom (− walls).
        mat_name, thick_ft = FINISH_BY_PREFIX["Floor"]
        floor_fp = _world_polygon(slab)
        baths = [bp.intersection(floor_fp) for bp in bathroom_polys
                 if bp.intersection(floor_fp).area > 1e-6]
        bath_union = unary_union(baths) if baths else None

        hardwood = floor_fp.difference(bath_union) if bath_union else floor_fp
        cover(slab, minus_walls(hardwood), thick_ft, mat_name, "Floor")
        if bath_union:
            cover(slab, minus_walls(bath_union), tthick_ft, tmat, "Bath")

    print(f"\n  floor finishes created: {created}")
    return created


# ── Verification ────────────────────────────────────────────────────────────────

# Unrelated elements this stage must NEVER touch (count-stable before/after).
# NOTE: IfcCovering / IfcMaterial / IfcRelAssociatesMaterial are intentionally NOT here —
# Step 2 adds to those — they are checked explicitly in verify() instead.
PRESERVE_TYPES = [
    "IfcWall", "IfcWallStandardCase", "IfcDoor", "IfcWindow",
    "IfcSpace", "IfcRoof", "IfcBuildingStorey", "IfcBuilding",
    "IfcRelContainedInSpatialStructure",
    # Space boundaries are preserved even across a merge — Step 0 repoints a removed
    # slab's boundaries onto the new floor rather than dropping them.
    "IfcRelSpaceBoundary",
]

# Sub-entities OWNED by a floor slab: count-stable only when no slab is merged away.
# When Step 0 replaces the interior slab(s) it deletes their own material layer set /
# usage and any slab-cut openings (e.g. the bathroom void) along with them.
SLAB_COUPLED_TYPES = ["IfcMaterialLayerSet", "IfcMaterialLayerSetUsage", "IfcOpeningElement"]


def verify(src_path: str, out_path: str, merge_info=None, n_new_coverings=0) -> bool:
    """Validate the rename: counts stable, no bare 'Floor:' left, preserves intact."""
    merge_info = merge_info or {}
    removed_slabs = merge_info.get("removed_slabs", 0)
    removed_types = merge_info.get("removed_types", 0)
    rebuilt = merge_info.get("rebuilt", False)

    before = ifcopenshell.open(src_path)
    after  = ifcopenshell.open(out_path)

    print(f"\n{'-' * 70}\nVERIFICATION  ({Path(out_path).name})\n{'-' * 70}")
    ok = True

    for t, removed in (("IfcSlab", removed_slabs), ("IfcSlabType", removed_types)):
        nb, na = len(before.by_type(t)), len(after.by_type(t))
        good = na == nb - removed
        ok &= good
        delta = f" (merged away {removed})" if removed else ""
        print(f"  {t:24s} count {nb} → {na}{delta}  {'ok' if good else 'UNEXPECTED!'}")

    # No floor name may still start with the bare, space-less prefix "Floor:" (no
    # following space).  After the rename, interior floors read "Floor: ".
    bare = [e for e in after.by_type("IfcSlab") + after.by_type("IfcSlabType")
            if (e.Name or "").startswith(PREFIX) and not (e.Name or "").startswith(PREFIX + " ")]
    if bare:
        ok = False
        print(f"  bare 'Floor:' (no space) names remaining: {len(bare)}  FAIL")
        for e in bare:
            print(f"      {e.Name}")
    else:
        print("  bare 'Floor:' (no space) names remaining: 0  ok")

    # ── Step 2: floor finishes ─────────────────────────────────────────────────
    expected_new = n_new_coverings if ADD_FINISHES else 0
    nb_cov, na_cov = len(before.by_type("IfcCovering")), len(after.by_type("IfcCovering"))
    good = na_cov == nb_cov + expected_new
    ok &= good
    print(f"  IfcCovering count {nb_cov} → {na_cov} (expected +{expected_new})  "
          f"{'ok' if good else 'FAIL'}")

    floorings = [c for c in after.by_type("IfcCovering") if c.PredefinedType == "FLOORING"]
    if len(floorings) != expected_new:
        ok = False
        print(f"  FLOORING coverings: {len(floorings)} (expected {expected_new})  FAIL")
    covers = {r.RelatedCoverings[0].id(): r.RelatingBuildingElement
              for r in after.by_type("IfcRelCoversBldgElements") if r.RelatedCoverings}
    flush_ok = True
    for c in floorings:
        has_mat = any(c in r.RelatedObjects for r in after.by_type("IfcRelAssociatesMaterial"))
        has_link = c.id() in covers
        if not (has_mat and has_link):
            ok = False
            print(f"  FLOORING '{c.Name}': material={has_mat} link={has_link}  FAIL")
            continue
        # Analytic flush check: covering base plane coincides with slab top plane.
        cz, sz = _extrusion_faces_world_z(c), _extrusion_faces_world_z(covers[c.id()])
        if cz and sz and abs(min(cz) - max(sz)) > 1e-4:
            ok = flush_ok = False
            print(f"  FLOORING '{c.Name}': base z {min(cz):.5f} ≠ slab top {max(sz):.5f}  FAIL")
    if floorings and ok:
        print(f"  FLOORING coverings: {len(floorings)}, each with material + slab link + "
              f"flush on slab top  ok")

    # Unrelated elements must be count-stable.
    mismatch = [(t, len(before.by_type(t)), len(after.by_type(t)))
                for t in PRESERVE_TYPES
                if len(before.by_type(t)) != len(after.by_type(t))]
    if mismatch:
        ok = False
        print("  PRESERVE_TYPES count mismatches:")
        for t, nb, na in mismatch:
            print(f"      {t}: {nb} → {na}")
    else:
        print("  PRESERVE_TYPES counts: all unchanged  ok")

    # Slab-coupled sub-entities: strict only when Step 0 did NOT rebuild the floor.
    # (A rebuild may replace N interior slabs with N new ones — net slab count unchanged —
    # yet the old slabs' material usages / openings legitimately drop.)
    for t in SLAB_COUPLED_TYPES:
        nb, na = len(before.by_type(t)), len(after.by_type(t))
        if not rebuilt:
            if nb != na:
                ok = False
                print(f"  {t}: {nb} → {na}  FAIL (no merge expected)")
        elif nb != na:
            print(f"  {t}: {nb} → {na}  (expected — owned by a replaced slab)")

    print(f"{'-' * 70}\n  RESULT: {'PASS' if ok else 'FAIL'}\n")
    return ok


# ── Main pipeline ─────────────────────────────────────────────────────────────

def define_floors(src_path, out_path) -> None:
    src_path, out_path = str(src_path), str(out_path)
    print(f"\n{'=' * 70}\n{Path(src_path).name}  →  {Path(out_path).name}\n{'=' * 70}")

    # Work on a copy — the original is never modified.
    shutil.copy2(src_path, out_path)
    model = ifcopenshell.open(out_path)

    # Step 0 — merge interior floors into one; pull out tile areas (bathrooms).
    print("[Step 0] Merging interior floors / extracting tile areas")
    merge_info = merge_interior_floors(model)

    # Diagnostic: optionally drop the merged interior slab entirely.
    if DROP_INTERIOR_SLAB and merge_info.get("merged_slab") is not None:
        ms = merge_info["merged_slab"]
        print(f"\n[diagnostic] removing merged interior slab #{ms.id()} {ms.Name}")
        ifcopenshell.api.run("root.remove_product", model, product=ms)
        merge_info["removed_slabs"] = merge_info.get("removed_slabs", 0) + 1
        merge_info["merged_slab"] = None
        # clean its now-orphaned IfcSlabType
        used = {r.RelatingType.id() for r in model.by_type("IfcRelDefinesByType")
                if r.RelatingType and r.RelatingType.is_a("IfcSlabType")}
        for st in list(model.by_type("IfcSlabType")):
            if st.id() not in used:
                ifcopenshell.api.run("root.remove_product", model, product=st)
                merge_info["removed_types"] = merge_info.get("removed_types", 0) + 1

    elements = list(model.by_type("IfcSlab")) + list(model.by_type("IfcSlabType"))
    floors = [e for e in elements if (e.Name or "").startswith(PREFIX)]
    print(f"\n[Step 1] Renaming — floor-prefixed slabs/slabtypes: {len(floors)} "
          f"(of {len(elements)} IfcSlab/IfcSlabType)")

    # Geometry-based stair detection (per slab instance): an exterior slab whose top is
    # well below the interior floor and whose footprint is shallow is a stair, not a deck.
    # Precomputed BEFORE renaming, since renaming strips the keyword used to classify.
    interior_top = _interior_top_z(model)
    ext_slab_ids = {s.id() for s in model.by_type("IfcSlab")
                    if (s.Name or "").startswith(PREFIX)
                    and classify_floor(s.Name) in ("Deck", "Porch")}
    stair_ids = {sid for sid in ext_slab_ids if _is_stair(model.by_id(sid), interior_top)}
    # An IfcSlabType is a stair only when ALL its exterior slab instances are stairs.
    stair_type_ids = set()
    for rel in model.by_type("IfcRelDefinesByType"):
        st = rel.RelatingType
        if st and st.is_a("IfcSlabType"):
            ext = [o for o in rel.RelatedObjects
                   if o.is_a("IfcSlab") and o.id() in ext_slab_ids]
            if ext and all(o.id() in stair_ids for o in ext):
                stair_type_ids.add(st.id())

    def stair_prefix(e):
        ids = stair_ids if e.is_a("IfcSlab") else stair_type_ids
        return "Stair" if e.id() in ids else None

    tally = {}
    for e in floors:
        old = e.Name
        override = stair_prefix(e)
        new = _renamed(old, prefix_override=override)
        kind = override or classify_floor(old)
        tally[kind] = tally.get(kind, 0) + 1
        if new != old:
            e.Name = new
            print(f"  [{e.is_a():12s}] {old}  →  {new}")
        else:
            print(f"  [{e.is_a():12s}] {old}  (unchanged)")

    interior = tally.get("Floor", 0)
    exterior_tally = {k: v for k, v in tally.items() if k != "Floor"}
    print(f"\n  interior (Floor): {interior}   "
          f"exterior/stairs: {sum(exterior_tally.values())}  {exterior_tally}")

    # Step 2 — finish materials on top of each floor (after the rename).
    n_new = 0
    if ADD_FINISHES:
        print("\n[Step 2] Adding floor finishes (IfcCovering FLOORING)")
        n_new = add_floor_finishes(model)
    else:
        print("\n[Step 2] SKIPPED — FLOORS_DEFINER_FINISH=0 (bare slabs, no coverings)")

    model.write(out_path)
    print(f"\nWritten: {out_path}")

    verify(src_path, out_path, merge_info, n_new)


def _out_path_for(src_path) -> Path:
    """Insert '-F1' before the extension: 'NAME.ifc' → 'NAME-F1.ifc'."""
    p = Path(src_path)
    return p.with_name(f"{p.stem}-F1{p.suffix}")


def main():
    if len(sys.argv) >= 3:
        define_floors(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2:
        define_floors(sys.argv[1], _out_path_for(sys.argv[1]))
    else:
        ifc_dir = Path(__file__).parent / "IFCs"
        srcs = sorted(p for p in ifc_dir.glob("*.ifc")
                      if not p.stem.endswith("-F1"))
        if not srcs:
            print(f"No source .ifc files found in {ifc_dir}")
            sys.exit(1)
        for src in srcs:
            define_floors(src, _out_path_for(src))
    print("\nDone.")


if __name__ == "__main__":
    main()
