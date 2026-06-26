#!/usr/bin/env python3
"""
IFC_walls_cleanup_V1.py — Gaudi IFC cleanup stage: WALLS ONLY.

Takes a Revit-exported IFC and cleans up the WALLS in place, leaving every other
element (doors, windows, slabs, roof, openings, spaces, materials, storeys, …)
exactly as-is.  This is one stage of the "Gaudi IFC" pipeline; later stages clean
other element types.

The original file is never modified.  The cleaned result is written next to it
with the suffix "-C1" before the extension, e.g.
    IFCs/SAN JUAN CYPRESS - AUG 2.ifc  →  IFCs/SAN JUAN CYPRESS - AUG 2-C1.ifc

The wall SHAPE is preserved exactly.  Walls keep their original body geometry — profile,
thickness, height and any IfcBooleanClippingResult (sloped / gable tops).  The only walls
whose geometry changes at all are the perimeter walls that get trimmed at a corner, and
even those only have their LENGTH shortened (the clipped top is kept).  Interior walls are
never touched.

Algorithm (see "IFC walls cleanup algorithm.md"):
  1. Detect the OUTER FACE of each wall (face pointing away from the building centroid).
  2. CLASSIFY each wall as Exterior / Interior / Design from its location and rename it
     e.g. "Exterior wall 529547".
  3. Verify every wall is a vertical EXTRUSION up the +Z axis (report only — walls are
     NOT reshaped, flattened or rectangularized).
  4. DIVIDE the walls so the plan view is clean: each exterior/design wall reaches one
     building-boundary corner and butts the perpendicular wall — no overlaps.  Interior
     walls keep their length and just butt cleanly (no overlap).  A trim only shortens a
     wall's length; its profile shape and clipped top are kept intact.

Usage:
    python3 IFC_walls_cleanup_V1.py [input.ifc] [output.ifc]

With no arguments it processes every .ifc under the ./IFCs subdirectory and writes a
"-C1" copy of each.
"""
import math
import re
import shutil
import sys
from pathlib import Path

import ifcopenshell


# ══════════════════════════════════════════════════════════════════════════════
# Wall geometry helpers
# ══════════════════════════════════════════════════════════════════════════════

def _upper_hull(points):
    """Upper edge of the convex hull of (u, z) points: the top boundary from the leftmost
    to the rightmost u.  Drops the leading/trailing vertical segments and collinear interior
    points, so a clean slope stays 2 points.  (Ported from the reference converter.)"""
    pts = sorted(set((round(u, 4), round(z, 4)) for u, z in points))
    if len(pts) < 2:
        return pts
    hull = []
    for p in pts:
        while len(hull) >= 2:
            (x1, y1), (x2, y2) = hull[-2], hull[-1]
            if (x2 - x1) * (p[1] - y1) - (y2 - y1) * (p[0] - x1) >= 0:
                hull.pop()
            else:
                break
        hull.append(p)
    while len(hull) >= 2 and abs(hull[0][0] - hull[1][0]) < 1e-6:
        hull.pop(0)
    while len(hull) >= 2 and abs(hull[-1][0] - hull[-2][0]) < 1e-6:
        hull.pop()
    return hull


def _z_at(profile, u):
    """Top height of a profile [(u, z), …] at coordinate u.  Interpolates within the
    captured range; beyond it, continues the end segment's slope (capped) so a small
    corner/snap extension keeps climbing instead of going flat.  (Ported from reference.)"""
    n = len(profile)
    MAX_SLOPE = 2.0
    if u < profile[0][0]:
        (u0, z0), (u1, z1) = profile[0], (profile[1] if n > 1 else profile[0])
        s = (z1 - z0) / (u1 - u0) if abs(u1 - u0) > 1e-9 else 0.0
        return z0 if abs(s) > MAX_SLOPE else z0 + s * (u - u0)
    if u > profile[-1][0]:
        (u0, z0), (u1, z1) = (profile[-2] if n > 1 else profile[-1]), profile[-1]
        s = (z1 - z0) / (u1 - u0) if abs(u1 - u0) > 1e-9 else 0.0
        return z1 if abs(s) > MAX_SLOPE else z1 + s * (u - u1)
    for k in range(n - 1):
        (u0, z0), (u1, z1) = profile[k], profile[k + 1]
        if u0 <= u <= u1:
            if abs(u1 - u0) < 1e-9:
                return max(z0, z1)
            return z0 + (z1 - z0) * (u - u0) / (u1 - u0)
    return profile[-1][1]


def _collect_points(entity, acc, seen):
    """Recursively gather every 3D IfcCartesianPoint reachable from `entity` (no model
    handle needed — entity_instance is iterable over its attribute values)."""
    if entity is None or id(entity) in seen:
        return
    seen.add(id(entity))
    if entity.is_a("IfcCartesianPoint"):
        if len(entity.Coordinates) == 3:
            acc.append(entity.Coordinates)
        return
    for attr in entity:
        if isinstance(attr, ifcopenshell.entity_instance):
            _collect_points(attr, acc, seen)
        elif isinstance(attr, (list, tuple)):
            for x in attr:
                if isinstance(x, ifcopenshell.entity_instance):
                    _collect_points(x, acc, seen)


_BREP_TYPES = ("IfcFacetedBrep", "IfcFaceBasedSurfaceModel", "IfcShellBasedSurfaceModel",
               "IfcManifoldSolidBrep")


def _brep_geometry(wall, axis_len):
    """Read a Brep (mesh) wall's geometry from its vertices (in the wall-local frame where
    local X = run, local Y = thickness).  Returns thickness, base_z, the top profile
    [(run_u, z_above_base)], a flat flag, height, and the Body rep/item to rebuild later."""
    body_rep = None
    brep_items = []
    for rep in (wall.Representation.Representations if wall.Representation else []):
        if rep.RepresentationIdentifier == "Body":
            for it in rep.Items:
                if it.is_a() in _BREP_TYPES:
                    body_rep = rep
                    brep_items.append(it)
    if not brep_items:
        return None
    # A wall can be split across SEVERAL Brep meshes (e.g. above/below an opening) — gather
    # vertices from ALL of them so the captured top spans the whole wall, not just one piece.
    pts = []
    seen = set()
    for it in brep_items:
        _collect_points(it, pts, seen)
    if not pts:
        return None
    body_item = brep_items[0]
    ys = [p[1] for p in pts]
    zs = [p[2] for p in pts]
    thickness = max(ys) - min(ys)
    base_z = min(zs)
    top = _upper_hull([(p[0], p[2] - base_z) for p in pts])
    # A 45° PLAN miter ends the mesh in a sharp tip at the base (z≈0) at the extreme run
    # positions; the upper hull then dips to 0 there, producing fake near-vertical rises.
    # Drop those steep leading/trailing segments so the captured top is the real roofline
    # (squaring removes the plan miter, so the squared wall should be full height to its end).
    MITER_SLOPE = 3.0
    while len(top) >= 3:
        (u0, z0), (u1, z1) = top[0], top[1]
        if abs(u1 - u0) > 1e-9 and z1 > z0 and (z1 - z0) / (u1 - u0) > MITER_SLOPE:
            top.pop(0)
        else:
            break
    while len(top) >= 3:
        (u0, z0), (u1, z1) = top[-2], top[-1]
        if abs(u1 - u0) > 1e-9 and z0 > z1 and (z0 - z1) / (u1 - u0) > MITER_SLOPE:
            top.pop()
        else:
            break
    if len(top) < 2:
        h = max(zs) - base_z
        top = [(0.0, h), (axis_len, h)]
    zmax = max(z for _, z in top)
    zmin = min(z for _, z in top)
    return {"thickness": thickness, "base_z": base_z, "top_profile": top,
            "flat": (zmax - zmin) <= 0.02, "height": zmax,
            "body_rep": body_rep, "body_item": body_item}


def _get_wall_info(wall):
    """Extract the plan geometry (origin, run direction, length, thickness, endpoints).

    Handles both extruded-solid walls (incl. clipped) and Brep (mesh) walls; the latter
    have their length/thickness/top profile read from the mesh vertices."""
    if not wall.Representation or not wall.ObjectPlacement:
        return None

    a3d = wall.ObjectPlacement.RelativePlacement
    coords = list(a3d.Location.Coordinates)
    while len(coords) < 3:
        coords.append(0.0)
    origin = (coords[0], coords[1])

    ref = a3d.RefDirection
    x_dir = tuple(ref.DirectionRatios[:2]) if ref else (1.0, 0.0)

    axis_poly = axis_len = None
    for rep in wall.Representation.Representations:
        if rep.RepresentationIdentifier == "Axis":
            for item in rep.Items:
                if item.is_a("IfcPolyline"):
                    axis_poly = item
                    p0, p1 = item.Points[0].Coordinates, item.Points[-1].Coordinates
                    axis_len = math.hypot(p1[0] - p0[0], p1[1] - p0[1])
    if axis_len is None:
        return None

    # Unwrap any IfcBooleanClippingResult so we can READ the base extruded solid's
    # profile WITHOUT dropping the clip — the clip (e.g. a sloped roof-line top) is
    # left in place so the wall keeps its exact original shape.
    body_solid = profile = body_rep = None
    solids = []
    for rep in wall.Representation.Representations:
        if rep.RepresentationIdentifier == "Body":
            for item in rep.Items:
                solid = _unwrap_solid(item)
                if solid is not None:
                    body_solid = solid
                    profile = solid.SweptArea
                    body_rep = rep
                    solids.append(solid)

    info = {
        "wall": wall, "origin": origin, "x_dir": x_dir, "axis_len": axis_len,
        "ws": origin, "we": (origin[0] + axis_len * x_dir[0], origin[1] + axis_len * x_dir[1]),
        "axis_poly": axis_poly, "a3d_loc": a3d.Location, "body_rep": body_rep,
        # top-shape fields: extruded/clipped walls keep their body (top preserved via the
        # clip), so no captured profile is needed; Brep walls fill these in below.
        "body_kind": "solid", "top_profile": None, "flat": True, "base_z": coords[2],
    }

    # MULTI-SOLID body: a wall made of several extruded solids — typically the small jamb
    # pieces on either side of a large door/window opening (e.g. 14TH 628798, a 15-ft
    # sliding door with two jambs).  Reading just one piece and editing it would mangle the
    # wall, so flag it for a single-solid rebuild (the preserved opening re-cuts the hole).
    # Thickness = overall profile extent perpendicular to the run; height = max extrusion
    # depth; flat top (these are flat-topped walls).
    if len(solids) > 1:
        ys = []
        depths = []
        zs = []
        for s in solids:
            pr = s.SweptArea
            if pr.is_a("IfcArbitraryClosedProfileDef"):
                ys += [p.Coordinates[1] for p in pr.OuterCurve.Points]
            elif pr.is_a("IfcRectangleProfileDef"):
                cy = pr.Position.Location.Coordinates[1] if pr.Position and pr.Position.Location else 0.0
                ys += [cy - pr.YDim / 2, cy + pr.YDim / 2]
            depths.append(s.Depth or 0.0)
            sp = s.Position
            zs.append(sp.Location.Coordinates[2] if sp and sp.Location and len(sp.Location.Coordinates) > 2 else 0.0)
        h = max(depths) if depths else 9.0
        info.update({
            "thickness": (max(ys) - min(ys)) if ys else 0.5, "body_solid": None, "profile": None,
            "body_kind": "multisolid", "flat": True, "base_z": min(zs) if zs else coords[2],
            "brep_height": h, "top_profile": [(0.0, h), (axis_len, h)],
        })
        return info

    if body_solid is not None and profile is not None:
        pos = body_solid.Position
        axis = list(pos.Axis.DirectionRatios) if (pos and pos.Axis) else [0.0, 0.0, 1.0]
        if abs(axis[2]) < 0.5:
            # PROFILED wall (our rebuilt stepped/sloped top): an elevation polygon in the
            # (run, height) plane extruded along the THICKNESS, not the usual (run×thickness)
            # profile extruded +Z.  Thickness = extrusion depth; the profile points are
            # (run, height), so read the top edge from them rather than treating Y as
            # thickness (which would mis-read the height as the thickness).
            info["thickness"] = body_solid.Depth
            info["body_solid"] = body_solid
            info["profile"] = profile
            info["body_kind"] = "profiled"
            base_z = (pos.Location.Coordinates[2]
                      if pos and pos.Location and len(pos.Location.Coordinates) > 2 else 0.0)
            pts = ([p.Coordinates for p in profile.OuterCurve.Points]
                   if profile.is_a("IfcArbitraryClosedProfileDef") and profile.OuterCurve.is_a("IfcPolyline")
                   else [])
            if pts:
                zs = [p[1] for p in pts]
                info["top_profile"] = _upper_hull([(p[0], p[1]) for p in pts])
                info["flat"] = (max(zs) - min(zs)) <= 0.02
            info["base_z"] = base_z
            info["run_window"] = (0.0, axis_len)
            return info
    if profile is not None and profile.is_a("IfcRectangleProfileDef"):
        # The profile dimension matching the axis length is the LENGTH; the other is the
        # thickness.  Rotation-agnostic (some Revit exports swap XDim/YDim).
        info["thickness"] = profile.YDim if abs(profile.XDim - axis_len) <= abs(profile.YDim - axis_len) else profile.XDim
        info["body_solid"] = body_solid
        info["profile"] = profile
        return info
    if profile is not None and profile.is_a("IfcArbitraryClosedProfileDef"):
        ys = [pt.Coordinates[1] for pt in profile.OuterCurve.Points]
        info["thickness"] = max(ys) - min(ys)
        info["body_solid"] = body_solid
        info["profile"] = profile
        return info

    # No usable extruded solid — try a Brep (mesh) body.
    bg = _brep_geometry(wall, axis_len)
    if bg is None:
        return None
    info.update({
        "thickness": bg["thickness"], "body_solid": None, "profile": None,
        "body_kind": "brep", "body_rep": bg["body_rep"], "body_item": bg["body_item"],
        "top_profile": bg["top_profile"], "flat": bg["flat"], "base_z": bg["base_z"],
        "brep_height": bg["height"],
    })
    return info


def _footprint(info):
    """Axis-aligned bounding box (xmin, xmax, ymin, ymax) of the wall solid."""
    ws, we, t = info["ws"], info["we"], info["thickness"]
    xd = info["x_dir"]
    ny = (-xd[1], xd[0])
    corners = [
        (ws[0] + t / 2 * ny[0], ws[1] + t / 2 * ny[1]),
        (ws[0] - t / 2 * ny[0], ws[1] - t / 2 * ny[1]),
        (we[0] + t / 2 * ny[0], we[1] + t / 2 * ny[1]),
        (we[0] - t / 2 * ny[0], we[1] - t / 2 * ny[1]),
    ]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return min(xs), max(xs), min(ys), max(ys)


def _overlaps(fp_a, fp_b, tol=1e-3):
    ax0, ax1, ay0, ay1 = fp_a
    bx0, bx1, by0, by1 = fp_b
    return (ax1 > bx0 + tol and bx1 > ax0 + tol and
            ay1 > by0 + tol and by1 > ay0 + tol)


def _square_profile(model, info):
    """Replace a mitered / arbitrary base profile with a clean full-length rectangle
    (length × thickness), centred at (L/2, 0).  Removes 45° corner miters while keeping
    the body wrapper — including any IfcBooleanClippingResult (sloped top).  Updates
    ``info['profile']`` in place.  No-op (returns False) if already a clean rectangle."""
    prof = info["profile"]
    if prof.is_a("IfcRectangleProfileDef"):
        return False
    L, t = info["axis_len"], info["thickness"]
    center = model.createIfcCartesianPoint((L / 2.0, 0.0))
    placement = model.createIfcAxis2Placement2D(center, None)
    new_prof = model.createIfcRectangleProfileDef("AREA", None, placement, L, t)
    info["body_solid"].SweptArea = new_prof
    old_pos = getattr(prof, "Position", None)
    for ent in (prof, old_pos):
        if ent is not None:
            try:
                model.remove(ent)
            except Exception:
                pass
    info["profile"] = new_prof
    return True


def _build_profiled_solid(model, top_local, thickness, base_z):
    """Build an IfcExtrudedAreaSolid from a (run ℓ, height z) elevation polygon following
    `top_local` [(ell, z)] (ell in [0, L], z above base), extruded along the wall-local
    thickness so the body spans local Y in [-t/2, +t/2] and sits at `base_z`.  This keeps a
    stepped / sloped top while squaring the plan footprint to a clean rectangle."""
    L = max(ell for ell, _ in top_local)
    raw = [(0.0, 0.0), (L, 0.0)] + [(ell, z) for ell, z in reversed(top_local)]
    poly_pts = []
    for p in raw:
        if not poly_pts or abs(poly_pts[-1][0] - p[0]) > 1e-9 or abs(poly_pts[-1][1] - p[1]) > 1e-9:
            poly_pts.append(p)
    poly_pts.append((0.0, 0.0))
    poly = model.createIfcPolyline([model.createIfcCartesianPoint((p[0], p[1])) for p in poly_pts])
    profile = model.createIfcArbitraryClosedProfileDef("AREA", None, poly)
    # Position frame: X = run, Y = up (+Z world), Z = inward (−localY).  Origin at +t/2 so
    # extruding −localY by thickness centres the body on localY = 0.
    origin = model.createIfcCartesianPoint((0.0, thickness / 2.0, base_z))
    axis = model.createIfcDirection((0.0, -1.0, 0.0))
    refd = model.createIfcDirection((1.0, 0.0, 0.0))
    pl = model.createIfcAxis2Placement3D(origin, axis, refd)
    extdir = model.createIfcDirection((0.0, 0.0, 1.0))
    return model.createIfcExtrudedAreaSolid(profile, pl, extdir, thickness)


def _rebuild_as_extrusion(model, info):
    """Replace a Brep (mesh) wall's body with a clean squared extrusion, preserving its
    top.  Flat tops become a plain rectangle extruded +Z (normal rectangle trim path);
    stepped / sloped tops become a profiled (run × height) extrusion that keeps the top
    (a profiled trim path).  Updates `info` to reflect the new body."""
    L, t, base_z = info["axis_len"], info["thickness"], info.get("base_z", 0.0)
    rep = info["body_rep"]
    if info.get("flat", True):
        center = model.createIfcCartesianPoint((L / 2.0, 0.0))
        pl2 = model.createIfcAxis2Placement2D(center, None)
        prof = model.createIfcRectangleProfileDef("AREA", None, pl2, L, t)
        org = model.createIfcCartesianPoint((0.0, 0.0, base_z))
        pl3 = model.createIfcAxis2Placement3D(org, None, None)
        height = info.get("brep_height") or 9.0
        solid = model.createIfcExtrudedAreaSolid(prof, pl3, model.createIfcDirection((0., 0., 1.)), height)
        rep.Items = (solid,)
        rep.RepresentationType = "SweptSolid"
        info.update({"body_solid": solid, "profile": prof, "body_kind": "solid"})
    else:
        # Sample the captured top over the FULL run [0, L] (including the 0 and L endpoints
        # via _z_at, which extrapolates the end slope).  Without this the polygon can start
        # at run > 0 — leaving a triangular notch at a wall end where the mesh's mitered tip
        # had no top vertex.
        top = info["top_profile"]
        knots = sorted({0.0, L} | {u for (u, _) in top if 0.0 < u < L})
        top_local = [(u, _z_at(top, u)) for u in knots]
        solid = _build_profiled_solid(model, top_local, t, base_z)
        rep.Items = (solid,)
        rep.RepresentationType = "SweptSolid"
        info.update({"body_solid": solid, "profile": solid.SweptArea,
                     "body_kind": "profiled", "run_window": (0.0, L)})
    return True


def _adjust_clip(model, clip_item, start_trim, old_len, new_len):
    """Keep a clipped wall's roof cut correct after the wall is repositioned.

    A clip (`IfcBooleanClippingResult` with an `IfcPolygonalBoundedHalfSpace`) represents the
    fixed WORLD roof.  When the wall is shifted/extended we must NOT let the clip ride along:
      • counter-translate the cutting PLANE by −start_trim along the run (wall-local X) so it
        stays at the same world height (otherwise a sloped top is lifted off the roof), and
      • on an extension, grow the bounded POLYGON to span the full new run length (otherwise the
        new sliver is uncut and pokes up at full height).
    """
    if clip_item is None or not clip_item.is_a("IfcBooleanClippingResult"):
        return
    # A wall may carry SEVERAL clips (a hip/gable end has multiple roof facets plus inert
    # base cuts), nested as BooleanClippingResult(BooleanClippingResult(base, hs1), hs2)…
    # We must counter-shift EVERY cutting plane — not just the outermost — or the inner roof
    # facets ride along with the moved origin and lift off the world roof.
    node = clip_item
    while node is not None and node.is_a("IfcBooleanClippingResult"):
        hs = node.SecondOperand
        node = node.FirstOperand
        if not (hs and hs.is_a("IfcHalfSpaceSolid")):   # covers both bounded & unbounded clips
            continue
        # Keep the cutting plane on the world roof: counter-translate by −start_trim along the
        # run (plane Location is in wall-local coords, X = run).  Horizontal base cuts are
        # unaffected (the shift is in-plane), so this is safe to apply to every clip.
        if abs(start_trim) > 1e-6:
            pl = hs.BaseSurface
            if pl and pl.is_a("IfcPlane") and pl.Position and pl.Position.Location:
                c = list(pl.Position.Location.Coordinates)
                while len(c) < 3:
                    c.append(0.0)
                c[0] -= start_trim
                # Assign a NEW point — this origin (0,0,0) is often SHARED (e.g. with the
                # building placement), so mutating it in place would move the whole model.
                pl.Position.Location = model.createIfcCartesianPoint(tuple(c))
        # An unbounded IfcHalfSpaceSolid cuts everywhere, so no sliver to fix.  A
        # PolygonalBoundedHalfSpace only cuts within its polygon, so grow it (along the run
        # only) to span the new length — keep its cross-run extent so hip facets aren't widened.
        if (hs.is_a("IfcPolygonalBoundedHalfSpace") and new_len > old_len + 1e-6
                and hs.PolygonalBoundary is not None):
            pb = hs.PolygonalBoundary
            hsx = (hs.Position.Location.Coordinates[0]
                   if hs.Position and hs.Position.Location else 0.0)
            xs = [p.Coordinates[0] for p in pb.Points]
            ys = [p.Coordinates[1] for p in pb.Points]
            ymin, ymax = min(ys), max(ys)
            # Extend only the end(s) that actually grew: keep the polygon's existing run-extent
            # where it already covers the (shifted) run, widening to reach [−hsx, new_len−hsx].
            x0 = min(min(xs), -hsx)
            x1 = max(max(xs), new_len - hsx)
            rect = [(x0, ymin), (x1, ymin), (x1, ymax), (x0, ymax), (x0, ymin)]
            pb.Points = [model.createIfcCartesianPoint(p) for p in rect]


def _anchor_profiled_tops(model, infos):
    """Make each PROFILED (sloped/stepped-top) wall's end-of-run top match the perpendicular
    neighbour's top at the corner, rather than blindly extrapolating its captured slope (which
    overshoots when the wall was extended to reach a corner).  This keeps the roof bound exact
    at corners.  Only walls rebuilt as profiled extrusions (e.g. FOREST's Brep walls) are
    affected; clipped walls already keep their world-fixed roof plane."""
    def run_axis(i):
        return 0 if abs(i["x_dir"][0]) >= abs(i["x_dir"][1]) else 1

    def neighbour_top(B, pt):
        """B's top z (above its base) at world point `pt`, projected onto B's run.  Only
        profiled neighbours are evaluated (the case that matters); others return None."""
        if B.get("body_kind") != "profiled" or not B.get("top_profile"):
            return None
        ell = (pt[0] - B["ws"][0]) * B["x_dir"][0] + (pt[1] - B["ws"][1]) * B["x_dir"][1]
        lo = B.get("run_window", (0.0, B["axis_len"]))[0]
        return _z_at(B["top_profile"], lo + ell) + B.get("base_z", 0.0)

    band = 0.6
    for A in [w for w in infos if w.get("body_kind") == "profiled" and w.get("top_profile")]:
        ra = run_axis(A); L = A["axis_len"]
        targets = {}
        for ename, pt in (("start", A["ws"]), ("far", A["we"])):
            best = None
            for B in infos:
                if B is A or run_axis(B) == ra:
                    continue
                fp = _footprint(B)
                if not (fp[0] - band <= pt[0] <= fp[1] + band and fp[2] - band <= pt[1] <= fp[3] + band):
                    continue
                tz = neighbour_top(B, pt)
                if tz is None:
                    continue
                d = min(abs(pt[0] - fp[0]), abs(pt[0] - fp[1]), abs(pt[1] - fp[2]), abs(pt[1] - fp[3]))
                if best is None or d < best[0]:
                    best = (d, tz)
            if best:
                targets[ename] = best[1] - A.get("base_z", 0.0)
        if not targets:
            continue
        top = A["top_profile"]; lo = A.get("run_window", (0.0, L))[0]
        knots = sorted({0.0, L} | {u - lo for (u, _) in top if lo < u < lo + L})
        cur = [(ell, _z_at(top, lo + ell)) for ell in knots]
        z0, z1 = _z_at(top, lo), _z_at(top, lo + L)
        d0 = targets.get("start", z0) - z0
        d1 = targets.get("far", z1) - z1
        if abs(d0) < 1e-4 and abs(d1) < 1e-4:
            continue
        corrected = [(ell, z + d0 + (d1 - d0) * (ell / L if L > 1e-9 else 0.0)) for ell, z in cur]
        solid = _build_profiled_solid(model, corrected, A["thickness"], A["base_z"])
        A["body_rep"].Items = (solid,)
        A["body_solid"] = solid
        A["profile"] = solid.SweptArea
        A["top_profile"] = corrected
        A["run_window"] = (0.0, L)
        print(f"    anchored top of {A['wall'].Name} (Δstart={d0:+.3f}, Δfar={d1:+.3f})")


def _apply_trim(model, info, start_trim, end_trim):
    """Change a wall's run length, preserving its shape.  A POSITIVE trim shortens that end;
    a NEGATIVE trim EXTENDS it (used to push a perimeter wall out to its corner).

    The wall keeps its body wrapper — including any IfcBooleanClippingResult (sloped/gable
    top) — and only its base rectangle's run dimension changes.  A far-end change leaves the
    placement origin (and clip) untouched; a start-end change shifts the origin along the run
    and compensates the wall's openings so doors/windows stay located.
    """
    if abs(start_trim) < 1e-6 and abs(end_trim) < 1e-6:
        return
    old_len = info["axis_len"]
    new_len = old_len - start_trim - end_trim
    if new_len < 0.01:
        return

    wall = info["wall"]
    prof = info["profile"]
    xd = info["x_dir"]

    # 1. Shift placement origin by start_trim along the run direction.  This moves the
    #    whole wall frame, so anything placed RELATIVE to the wall (its openings, and the
    #    doors/windows that fill them) would ride along and end up mis-located.  Compensate
    #    by shifting each wall-relative opening back by the same amount in local run coords,
    #    so openings/doors stay at their original world position.
    if abs(start_trim) > 1e-6:
        old_c = list(info["a3d_loc"].Coordinates)
        while len(old_c) < 3:
            old_c.append(0.0)
        # Assign a NEW IfcCartesianPoint rather than mutating the existing one — Revit exports
        # frequently SHARE a point between a wall's placement and an ancestor (storey/building)
        # placement, so editing it in place would move the whole building.
        new_loc = model.createIfcCartesianPoint((
            old_c[0] + start_trim * xd[0],
            old_c[1] + start_trim * xd[1],
            old_c[2],
        ))
        wall.ObjectPlacement.RelativePlacement.Location = new_loc
        info["a3d_loc"] = new_loc
        wp = wall.ObjectPlacement
        for rel in getattr(wall, "HasOpenings", []):
            opl = rel.RelatedOpeningElement.ObjectPlacement
            if (opl and opl.PlacementRelTo and opl.PlacementRelTo.id() == wp.id()
                    and opl.RelativePlacement and opl.RelativePlacement.Location):
                oc = list(opl.RelativePlacement.Location.Coordinates)
                while len(oc) < 3:
                    oc.append(0.0)
                # Opening Location is in the wall's local frame (local X = run); the wall frame
                # moved +start_trim along local X, so subtract it to hold world position.  Use a
                # fresh point here too (opening points can also be shared).
                opl.RelativePlacement.Location = model.createIfcCartesianPoint(
                    (oc[0] - start_trim, oc[1], oc[2]))

    # 2. Update axis polyline end point
    if info["axis_poly"]:
        info["axis_poly"].Points[-1].Coordinates = (new_len, 0.0)

    # 3. Shorten the base footprint to new_len along the run.
    if info.get("body_kind") == "profiled":
        # Profiled (stepped/sloped top) wall: rebuild the elevation polygon over the new run
        # window, re-interpolating the top edge so the shortened wall keeps its slope/steps.
        lo, hi = info["run_window"]
        lo += start_trim
        hi -= end_trim
        top = info["top_profile"]
        knots = sorted({0.0, hi - lo} | {u - lo for (u, _) in top if lo < u < hi})
        top_local = [(ell, _z_at(top, lo + ell)) for ell in knots]
        new_solid = _build_profiled_solid(model, top_local, info["thickness"], info["base_z"])
        info["body_rep"].Items = (new_solid,)
        info["body_solid"] = new_solid
        info["profile"] = new_solid.SweptArea
        info["run_window"] = (lo, hi)
    else:
        # Solid wall: a mitered / arbitrary corner profile is squared first (only because
        # this wall is being divided — a clean butt joint requires it); non-divided walls
        # never reach here, so their original profiles are preserved.  The clip wrapper
        # (sloped top) is left untouched.
        if not prof.is_a("IfcRectangleProfileDef"):
            _square_profile(model, info)
            prof = info["profile"]
            print(f"    squared mitered end of {wall.Name} (divided wall)")
        # Reduce the rectangle's RUN dimension only (XDim or YDim — whichever matches the
        # wall length; the other is the thickness).  The profile centre sits at (L/2, 0), so
        # recentring to (new_len/2, 0) keeps the FIXED (origin) edge in place.
        if abs(prof.XDim - old_len) <= abs(prof.YDim - old_len):
            prof.XDim = new_len
        else:
            prof.YDim = new_len
        if prof.Position and prof.Position.Location:
            prof.Position.Location.Coordinates = (new_len / 2, 0.0)
        # Keep any clipped roof cut world-fixed and covering the new length.
        for item in (info["body_rep"].Items if info.get("body_rep") else []):
            if item.is_a("IfcBooleanClippingResult"):
                _adjust_clip(model, item, start_trim, old_len, new_len)

    # 4. Update the Length quantity
    for rel in wall.IsDefinedBy:
        if not rel.is_a("IfcRelDefinesByProperties"):
            continue
        pset = rel.RelatingPropertyDefinition
        if not pset.is_a("IfcElementQuantity"):
            continue
        for qty in pset.Quantities:
            if qty.is_a("IfcQuantityLength") and qty.Name == "Length":
                qty.LengthValue = new_len

    new_ws = (info["ws"][0] + start_trim * xd[0], info["ws"][1] + start_trim * xd[1])
    info["ws"] = new_ws
    info["we"] = (new_ws[0] + new_len * xd[0], new_ws[1] + new_len * xd[1])
    info["axis_len"] = new_len
    print(f"    trimmed {wall.Name}: {old_len:.4f} → {new_len:.4f} ft "
          f"(start={start_trim:.4f}, end={end_trim:.4f})")


def _xform_point(model, pt, T):
    """A fresh IfcCartesianPoint = T · pt (T is a 4x4 numpy transform). Never mutates `pt`."""
    import numpy as _np
    c = [float(x) for x in pt.Coordinates]
    while len(c) < 3:
        c.append(0.0)
    v = T @ _np.array([c[0], c[1], c[2], 1.0])
    return model.createIfcCartesianPoint([float(v[0]), float(v[1]), float(v[2])])


def _xform_dir(model, d, T):
    """A fresh IfcDirection = normalize(R(T) · d), or None if d is None. Rotation part only."""
    import numpy as _np
    if d is None:
        return None
    r = [float(x) for x in d.DirectionRatios]
    while len(r) < 3:
        r.append(0.0)
    v = T[:3, :3] @ _np.array(r)
    n = _np.linalg.norm(v)
    if n > 0:
        v = v / n
    return model.createIfcDirection([float(v[0]), float(v[1]), float(v[2])])


def _clone_clip(model, hs, T):
    """Deep-clone a half-space clip, re-anchored by the 4x4 transform T = inv(M_lower)·M_upper
    (maps the clip from the upper segment's local frame into the lower's while keeping it
    world-fixed — corrects any XY/Z/orientation difference left by per-storey division).
    Only fresh entities are created — shared points/planes are never mutated."""
    pl = hs.BaseSurface           # IfcPlane
    pp = pl.Position
    new_plane = model.createIfcPlane(model.createIfcAxis2Placement3D(
        _xform_point(model, pp.Location, T), _xform_dir(model, pp.Axis, T), _xform_dir(model, pp.RefDirection, T)))
    if hs.is_a("IfcPolygonalBoundedHalfSpace"):
        hp = hs.Position
        new_pos = model.createIfcAxis2Placement3D(
            _xform_point(model, hp.Location, T), _xform_dir(model, hp.Axis, T), _xform_dir(model, hp.RefDirection, T))
        # PolygonalBoundary points are 2D in the (now transformed) Position frame → keep as-is.
        new_poly = model.createIfcPolyline(
            [model.createIfcCartesianPoint([float(x) for x in p.Coordinates]) for p in hs.PolygonalBoundary.Points])
        return model.createIfcPolygonalBoundedHalfSpace(new_plane, hs.AgreementFlag, new_pos, new_poly)
    return model.createIfcHalfSpaceSolid(new_plane, hs.AgreementFlag)


def _detach_and_remove(model, ent):
    """Remove `ent` from every list-attribute that references it (dropping any relationship
    that becomes empty), then delete it — leaving no dangling references."""
    for inv in list(model.get_inverse(ent)):
        for idx in range(len(inv)):
            v = inv[idx]
            if isinstance(v, (list, tuple)) and ent in v:
                kept = [x for x in v if x != ent]
                if not kept and inv.is_a().startswith("IfcRel"):
                    try:
                        model.remove(inv)
                    except Exception:
                        pass
                    break
                inv[idx] = kept
    try:
        model.remove(ent)
    except Exception:
        pass


def _merge_stacked_walls(model):
    """Combine vertically-stacked wall segments that share the same wall number into one
    full-height wall.  A split wall has a LOWER segment (full rectangle, floor→plate, carrying
    the openings) and an UPPER gable segment (plate→roof, clipped by the roof plane).  Only walls
    with the SAME trailing wall id and a coincident plan footprint are merged.  Returns the count
    merged."""
    import ifcopenshell.util.placement as _P
    from collections import defaultdict

    groups = defaultdict(list)
    for w in model.by_type("IfcWall"):
        eid = _eid_digits(w.Name)
        if eid:
            groups[eid].append(w)

    rel_of = {}
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        for e in rel.RelatedElements:
            rel_of[e.id()] = rel

    merged = 0
    for eid, ws in groups.items():
        if len(ws) != 2:
            continue                     # only clean two-segment splits
        a, b = ws
        ia, ib = _get_wall_info(a), _get_wall_info(b)
        if not ia or not ib:
            continue
        fa, fb = _footprint(ia), _footprint(ib)
        if any(abs(fa[k] - fb[k]) > 0.2 for k in range(4)):
            continue                     # plan footprints must coincide (same wall, stacked)
        za = _P.get_local_placement(a.ObjectPlacement)[2, 3]
        zb = _P.get_local_placement(b.ObjectPlacement)[2, 3]
        if abs(za - zb) < 0.1:
            continue                     # same level → not a vertical stack
        lower, upper = (a, b) if za < zb else (b, a)
        dz = abs(za - zb)

        lrep = next(r for r in lower.Representation.Representations if r.RepresentationIdentifier == "Body")
        urep = next(r for r in upper.Representation.Representations if r.RepresentationIdentifier == "Body")
        lbase, ubase = _unwrap_solid(lrep.Items[0]), _unwrap_solid(urep.Items[0])
        if lbase is None or ubase is None:
            continue

        # Extend the lower extrusion up to the roof peak, then graft the upper's clips,
        # re-anchored world-fixed (T = inv(M_lower)·M_upper) so the gable roof cut lands
        # at the exact same world position even if division shifted the two segments apart.
        import numpy as _np
        T = _np.linalg.inv(_P.get_local_placement(lower.ObjectPlacement)) @ \
            _P.get_local_placement(upper.ObjectPlacement)
        lbase.Depth = lbase.Depth + ubase.Depth
        body = lrep.Items[0]
        item = urep.Items[0]
        upper_clips = []
        while item.is_a("IfcBooleanClippingResult"):
            upper_clips.append(item.SecondOperand)
            item = item.FirstOperand
        for hs in upper_clips:
            body = model.createIfcBooleanClippingResult("DIFFERENCE", body, _clone_clip(model, hs, T))
        lrep.Items = (body,)

        # Transfer any openings hosted by the upper onto the lower (FOREST uppers have none).
        for rel in list(getattr(upper, "HasOpenings", [])):
            rel.RelatingBuildingElement = lower

        _detach_and_remove(model, upper)
        merged += 1
        print(f"    merged stacked segments of {lower.Name} (one full-height wall)")
    return merged


def _normalize_wall_classes(model):
    """Replace every deprecated IfcWallStandardCase with a plain IfcWall.  IfcWall and
    IfcWallStandardCase share identical attributes, so this is lossless: the new wall keeps the
    same GlobalId/Name/placement/representation, and all inverse references (containment,
    voids/openings, fills, material & property relationships) are redirected to it.  No-op when
    there are none.  Returns the count converted."""
    n = 0
    for old in list(model.by_type("IfcWallStandardCase")):
        attrs = old.get_info(recursive=False)
        attrs.pop("id", None)
        attrs.pop("type", None)
        new = model.create_entity("IfcWall", **attrs)
        for inv in model.get_inverse(old):
            for idx in range(len(inv)):
                v = inv[idx]
                if v == old:
                    inv[idx] = new
                elif isinstance(v, (list, tuple)) and old in v:
                    inv[idx] = [new if x == old else x for x in v]
        model.remove(old)
        n += 1
    return n


def _is_glass_wall(wall):
    """A glass/shower partition modelled in Revit as a thin wall (shower screens and
    'Generic Glass' panels).  Identified by the SHOWER/GLASS keyword in the Revit name."""
    up = (wall.Name or "").upper()
    return "SHOWER" in up or "GLASS" in up


def _convert_glass_walls(model, walls):
    """Re-type each glass/shower wall as an IfcFurniture glass element (IfcFurnishingElement on
    IFC2X3), so the cleaned model carries it as glazing/furniture rather than a structural wall.

    Lossless re-typing (same technique as `_normalize_wall_classes`): the new element keeps the
    wall's GlobalId, placement, body geometry and material, and every inverse reference
    (spatial containment, material & property relationships) is redirected to it.  Wall-only data
    is dropped: the `Axis` representation, the wall `PredefinedType` enum, and any openings.
    Returns a list of (element_id, GlobalId) for the converted walls."""
    target = "IfcFurniture" if model.schema != "IFC2X3" else "IfcFurnishingElement"
    converted = []
    for wall in list(walls):
        if not _is_glass_wall(wall):
            continue
        # Drop any hosted openings — a furniture element doesn't host doors/windows.
        for rel in list(getattr(wall, "HasOpenings", [])):
            op = rel.RelatedOpeningElement
            if op is not None:
                _detach_and_remove(model, op)   # also clears the now-empty IfcRelVoidsElement

        attrs = wall.get_info(recursive=False)
        attrs.pop("id", None)
        attrs.pop("type", None)
        attrs.pop("PredefinedType", None)        # IfcWallTypeEnum value is invalid for furniture
        eid = _eid_digits(wall.Name)
        attrs["Name"] = f"Shower glass: {eid}" if eid else "Shower glass"
        attrs["ObjectType"] = "Glass Panel"

        new = model.create_entity(target, **attrs)
        # Strip the wall-only Axis representation from the (now shared) shape; keep the Body.
        rep = new.Representation
        if rep is not None:
            kept = [r for r in rep.Representations if r.RepresentationIdentifier != "Axis"]
            if len(kept) != len(rep.Representations):
                rep.Representations = kept

        for inv in model.get_inverse(wall):
            for idx in range(len(inv)):
                v = inv[idx]
                if v == wall:
                    inv[idx] = new
                elif isinstance(v, (list, tuple)) and wall in v:
                    inv[idx] = [new if x == wall else x for x in v]
        model.remove(wall)
        converted.append((eid, new.GlobalId))
        print(f"    converted glass wall {eid} → {target}: {new.Name}")
    return converted


def _unwrap_solid(item):
    """Unwrap an IfcBooleanClippingResult down to its base IfcExtrudedAreaSolid."""
    if item is None:
        return None
    if item.is_a("IfcExtrudedAreaSolid"):
        return item
    if item.is_a("IfcBooleanClippingResult"):
        return _unwrap_solid(item.FirstOperand)
    return None


# ══════════════════════════════════════════════════════════════════════════════
# Wall naming / classification
# ══════════════════════════════════════════════════════════════════════════════

_TYPE_LABELS = {"exterior": "Exterior wall", "interior": "Interior wall", "design": "Design wall"}
_AXIS_ALIGN_COS = 0.966   # cos(15°): below this a wall is "skew" → name fallback


def _eid_digits(name):
    """Trailing Revit element id, e.g. 'Basic Wall:…:529547' → '529547'."""
    m = re.search(r"(\d+)\s*$", name or "")
    return m.group(1) if m else ""


def _name_keyword_category(name):
    """Fallback category from the Revit wall name keywords."""
    up = (name or "").upper()
    if "DESING" in up or "DESIGN" in up:
        return "design"
    if "EXT" in up:
        return "exterior"
    if "INT" in up:
        return "interior"
    return "exterior"


def _clean_name(category, original_name):
    """Clean display name, e.g. 'Exterior wall: 529547'."""
    eid = _eid_digits(original_name)
    label = _TYPE_LABELS.get(category, "Exterior wall")
    return f"{label}: {eid}" if eid else label


def _ray_hits_outward(M, normal, fps, self_idx, cover_tol=0.10):
    """Cast a ray from face-midpoint M along its outward normal; True if another wall
    footprint lies beyond it (so the building continues outward → M is not on the
    perimeter)."""
    ax = 0 if abs(normal[0]) >= abs(normal[1]) else 1
    perp = 1 - ax
    sgn = 1 if normal[ax] >= 0 else -1
    for k, fp in enumerate(fps):
        if k == self_idx or fp is None:
            continue
        lo = (fp[0], fp[2]); hi = (fp[1], fp[3])
        if not (lo[perp] - cover_tol <= M[perp] <= hi[perp] + cover_tol):
            continue
        if sgn > 0 and hi[ax] > M[ax] + 1e-6:
            return True
        if sgn < 0 and lo[ax] < M[ax] - 1e-6:
            return True
    return False


def _count_exposed_faces(axis_start, x_dir, y_dir, t, length, fps, self_idx,
                         samples=(0.25, 0.5, 0.75)):
    """How many of a wall's two long faces are open to the exterior (0, 1 or 2)."""
    exposed = 0
    for fsign in (+1, -1):
        normal = (fsign * y_dir[0], fsign * y_dir[1])
        face_open = True
        for frac in samples:
            p = (axis_start[0] + frac * length * x_dir[0] + fsign * (t / 2) * y_dir[0],
                 axis_start[1] + frac * length * x_dir[1] + fsign * (t / 2) * y_dir[1])
            if _ray_hits_outward(p, normal, fps, self_idx):
                face_open = False
                break
        if face_open:
            exposed += 1
    return exposed


def _category_from_exposed(n):
    """0 exposed long faces → interior, 1 → exterior (envelope), 2 → design."""
    return {0: "interior", 1: "exterior", 2: "design"}[n]


def _refine_projecting_designs(infos, fps, cats):
    """Re-classify a short stub wall as `design` when it projects beyond the building envelope.

    A tiny projecting fin (length ≈ thickness) that sticks out past the perimeter is a
    free-standing/design wall, but the exposed-face test can mislabel it `exterior`: if two such
    fins sit on the same line, each one's inward ray-cast hits the *other* across the open gap and
    reads that face as 'enclosed' (1 exposed face → exterior).  Here we catch them geometrically:
    a stub still classed `exterior` whose footprint protrudes past the envelope of the *long*
    perimeter walls is a projecting wall → `design`.  Only stubs are considered and only
    exterior→design is ever changed, so real (long) perimeter walls are never affected."""
    ext_long = [(i, fp) for i, fp in zip(infos, fps)
                if cats[i["wall"].id()] == "exterior" and i["axis_len"] > 2.0 * i["thickness"]]
    if len(ext_long) < 2:
        return
    xs0 = min(fp[0] for _, fp in ext_long); xs1 = max(fp[1] for _, fp in ext_long)
    ys0 = min(fp[2] for _, fp in ext_long); ys1 = max(fp[3] for _, fp in ext_long)
    tol = 0.25
    for info, fp in zip(infos, fps):
        wid = info["wall"].id()
        if cats[wid] != "exterior" or info["axis_len"] > 1.5 * info["thickness"]:
            continue                     # only short stub walls are candidates
        if fp[0] < xs0 - tol or fp[1] > xs1 + tol or fp[2] < ys0 - tol or fp[3] > ys1 + tol:
            cats[wid] = "design"         # protrudes past the perimeter envelope → projecting wall


def classify_walls(walls):
    """Location-based classification: returns {wall_id: 'exterior'|'interior'|'design'}.
    Falls back to the Revit name keywords for skew (non-axis-aligned) walls."""
    infos = [i for i in (_get_wall_info(w) for w in walls) if i]
    cats = {}
    if not infos:
        return cats
    fps = [_footprint(i) for i in infos]
    for idx, info in enumerate(infos):
        wid = info["wall"].id()
        name = info["wall"].Name or ""
        xd = info["x_dir"]
        yd = (-xd[1], xd[0])
        if max(abs(yd[0]), abs(yd[1])) < _AXIS_ALIGN_COS:
            cats[wid] = _name_keyword_category(name)
        else:
            n = _count_exposed_faces(info["ws"], xd, yd, info["thickness"],
                                     info["axis_len"], fps, idx)
            cats[wid] = _category_from_exposed(n)
    _refine_projecting_designs(infos, fps, cats)
    return cats


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — verify every wall is a vertical (+Z) extrusion
# ══════════════════════════════════════════════════════════════════════════════

def verify_z_extrusion(walls):
    """Check (without modifying anything) that each wall's base solid is extruded
    straight up the +Z axis.  Walls keep their EXACT original body — including any
    IfcBooleanClippingResult (sloped/gable tops) — so this step only reports; it never
    promotes, flattens or rectangularizes a wall.  Returns (ok_count, warn_count)."""
    ok = warn = 0
    for w in walls:
        if not w.Representation:
            continue
        base = None
        for rep in w.Representation.Representations:
            if rep.RepresentationIdentifier == "Body":
                for item in rep.Items:
                    s = _unwrap_solid(item)
                    if s is not None:
                        base = s
        if base is None or not base.is_a("IfcExtrudedAreaSolid"):
            continue
        d = base.ExtrudedDirection.DirectionRatios if base.ExtrudedDirection else (0., 0., 1.)
        if abs(d[0]) < 1e-6 and abs(d[1]) < 1e-6 and d[2] > 0:
            ok += 1
        else:
            warn += 1
            print(f"    NOTE {w.Name}: extruded along {tuple(round(x, 3) for x in d)} "
                  f"(not straight +Z) — left unchanged")
    return ok, warn


# ══════════════════════════════════════════════════════════════════════════════
# Steps 1 & 4 — outer-face detection + wall division (diagonal corner joints)
# ══════════════════════════════════════════════════════════════════════════════

def divide_walls(model, walls, cat_map):
    """
    Clean perimeter corners with no overlaps — orientation-agnostic alternating pinwheel.

    For every overlapping pair of PERPENDICULAR perimeter (exterior/design) walls the two
    bodies overlap in a one-thickness "corner square".  One wall OWNS the corner (its body
    fills the square and reaches the outer corner); the other BUTTS it (its corner end is
    trimmed back to the owner's near face).

    Ownership follows an alternating PINWHEEL: each wall is oriented along a consistent
    counter-clockwise loop around the building, owns the corner at its loop-backward end and
    butts the corner at its loop-forward end.  Going around the perimeter, ownership
    alternates and every wall is trimmed exactly once (a 9×13 box → 8.5 / 12.5 / 8.5 / 12.5).
    Because the loop direction is derived from each wall's position (not its Revit export
    orientation), it works even when walls run "the wrong way" — which previously left a wall
    un-trimmed with its original 45° miter.  Corners with no clean loop fall back to
    "longer wall owns".

    The butt wall is trimmed at WHICHEVER physical end meets the corner — start or far.
    Corner walls with mitered / arbitrary profiles are squared to clean rectangles (keeping
    any clipped top).  Interior walls and standalone walls are never touched.

    Outer faces (algorithm step 1) are reported here from the building centroid.
    """
    infos = [i for i in (_get_wall_info(w) for w in walls) if i]
    if not infos:
        return 0

    # ── Step 1: building centroid (outer face = the long face farther from it) ───
    cx = cy = 0.0
    for info in infos:
        cx += info["ws"][0] + (info["axis_len"] / 2) * info["x_dir"][0]
        cy += info["ws"][1] + (info["axis_len"] / 2) * info["x_dir"][1]
    centroid = (cx / len(infos), cy / len(infos))
    print(f"    building centroid: ({centroid[0]:.3f}, {centroid[1]:.3f})")

    def is_perimeter(info):
        cat = cat_map.get(info["wall"].id()) or _name_keyword_category(info["wall"].Name)
        return cat in ("exterior", "design")

    def run_axis(info):
        xd = info["x_dir"]
        return 0 if abs(xd[0]) >= abs(xd[1]) else 1

    # Storey of each wall — corners are resolved PER STOREY so walls stacked on different
    # floors (which share a plan footprint) are never treated as meeting each other.
    storey_of = {}
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        for e in rel.RelatedElements:
            storey_of[e.id()] = rel.RelatingStructure.id()

    def same_storey(ia, ib):
        return storey_of.get(ia["wall"].id()) == storey_of.get(ib["wall"].id())

    tol = 0.05   # ~0.6": absorbs Revit coordinate imprecision

    # ── Pre-pass: extend every perimeter wall to its full outer-corner span ──────
    #    Walls modelled as clean butt joints only TOUCH at corners, so the overlap-based
    #    division below would otherwise leave them as Revit arranged them (a wall could own
    #    both corners while its neighbour owns none).  Pushing each perimeter wall's ends out
    #    to its outer corners makes every corner overlap by one thickness; the pinwheel then
    #    trims each wall back to owning exactly one corner — so already-butting buildings get
    #    the same treatment as overlapping ones.
    perim = [i for i in infos if is_perimeter(i)]
    _BAND = 0.6

    def outer_corner_for(W, pt):
        """Outer-corner coordinate along W's run for W's end at `pt`: the outer face (the one
        farther from the centroid) of the nearest perpendicular perimeter neighbour met
        there.  None if no neighbour is found near that end."""
        rW = run_axis(W); pW = 1 - rW
        best = None
        for N in perim:
            if N is W or run_axis(N) == rW or not same_storey(W, N):
                continue
            fpN = _footprint(N)
            if not (fpN[2 * pW] - _BAND <= pt[pW] <= fpN[2 * pW + 1] + _BAND):
                continue
            d = min(abs(pt[rW] - fpN[2 * rW]), abs(pt[rW] - fpN[2 * rW + 1]))
            if d > _BAND:
                continue
            lo, hi = fpN[2 * rW], fpN[2 * rW + 1]
            outer = hi if abs(hi - centroid[rW]) > abs(lo - centroid[rW]) else lo
            if best is None or d < best[0]:
                best = (d, outer)
        return best[1] if best else None

    ext_targets = {}   # wall id → (start_trim, end_trim), both ≤ 0 (extension only)
    for W in perim:
        rW = run_axis(W); xd = W["x_dir"]
        s_outer = outer_corner_for(W, W["ws"])
        f_outer = outer_corner_for(W, W["we"])
        st = (s_outer - W["ws"][rW]) / xd[rW] if s_outer is not None else 0.0
        et = (W["we"][rW] - f_outer) / xd[rW] if f_outer is not None else 0.0
        st, et = min(0.0, st), min(0.0, et)        # only extend; never shorten in this pass
        if st < -1e-4 or et < -1e-4:
            ext_targets[W["wall"].id()] = (st, et)

    for W in perim:
        if W["wall"].id() not in ext_targets:
            continue
        if W.get("body_kind") in ("brep", "multisolid"):
            _rebuild_as_extrusion(model, W)   # make it a single trimmable solid (top kept)
        elif W.get("body_kind") == "solid" and W["profile"] is not None \
                and not W["profile"].is_a("IfcRectangleProfileDef"):
            _square_profile(model, W)
        st, et = ext_targets[W["wall"].id()]
        _apply_trim(model, W, st, et)
        print(f"    extended {W['wall'].Name} to outer corners "
              f"(start +{-st:.3f}, far +{-et:.3f})")

    # ── Find perpendicular, overlapping pairs (the corners / T-junctions) ────────
    #    Includes interior–interior pairs so interior walls also butt cleanly (perimeter
    #    pairs drive the pinwheel; any pair involving an interior wall uses "longer owns").
    pairs = []
    for a in range(len(infos)):
        for b in range(a + 1, len(infos)):
            ia, ib = infos[a], infos[b]
            if not same_storey(ia, ib):
                continue                       # walls on different floors never meet
            if not _overlaps(_footprint(ia), _footprint(ib)):
                continue
            if run_axis(ia) == run_axis(ib):
                continue                       # parallel / collinear: not a corner
            pairs.append((ia, ib))

    # ── Square the corner-participating walls (remove 45° miters) ────────────────
    #    Done before trimming so BOTH the owner and the butt wall lose their miters; the
    #    clipped top is kept (squaring is a no-op for clean rectangles).  Walls not in any
    #    corner (standalone) are left untouched, so their original shape is preserved.
    corner_ids = set()
    for ia, ib in pairs:
        corner_ids.add(ia["wall"].id())
        corner_ids.add(ib["wall"].id())
    squared = 0
    for info in infos:
        if info["wall"].id() not in corner_ids:
            continue
        if info.get("body_kind") in ("brep", "multisolid"):
            # Brep (mesh) or multi-solid (jambs-around-an-opening) corner wall → rebuild as a
            # single clean squared extrusion, preserving its top.  This removes the plan
            # miter, merges the pieces into one solid, and makes it trimmable; the wall's
            # preserved IfcOpeningElements re-cut any door/window hole.
            kind = info.get("body_kind")
            _rebuild_as_extrusion(model, info)
            squared += 1
            print(f"    rebuilt {kind} wall {info['wall'].Name} as single extrusion (top preserved)")
        elif info.get("body_kind") == "solid" and info["profile"] is not None \
                and not info["profile"].is_a("IfcRectangleProfileDef"):
            if _square_profile(model, info):
                squared += 1
                print(f"    squared mitered profile of {info['wall'].Name}")

    def corner_end(victim, priority):
        """Which end (start/far) of `victim` sits inside `priority`'s footprint."""
        fp = _footprint(priority)
        def inside(p):
            return fp[0] - tol <= p[0] <= fp[1] + tol and fp[2] - tol <= p[1] <= fp[3] + tol
        in_s, in_f = inside(victim["ws"]), inside(victim["we"])
        if in_s and not in_f:
            return "start"
        if in_f and not in_s:
            return "far"
        return None   # neither, or both (a T-junction-style pass-through) → leave

    def trim_to_butt(victim, priority, end):
        """Amount to pull `victim`'s `end` back to `priority`'s near face (the priority
        face on the victim's body side), so the victim butts the owner with no overlap."""
        run = run_axis(victim)
        sgn = 1.0 if victim["x_dir"][run] >= 0 else -1.0
        fp = _footprint(priority)
        p_lo, p_hi = fp[2 * run], fp[2 * run + 1]   # priority extent along victim's run
        if end == "far":
            far_c = victim["we"][run]
            target = p_lo if sgn > 0 else p_hi      # near face = first priority face met
            return (far_c - target) * sgn
        else:
            start_c = victim["ws"][run]
            target = p_hi if sgn > 0 else p_lo
            return (target - start_c) * sgn

    def loop_forward_end(info):
        """Which physical end (start/far) is the wall's COUNTER-CLOCKWISE-forward end,
        i.e. the end that leads into the next corner going CCW around the building.  The
        CCW tangent at the wall's midpoint is (centroid→point) rotated +90°; the physical
        run agrees with it (→ far is forward) or opposes it (→ start is forward).  This
        gives a consistent loop orientation regardless of how Revit exported each wall."""
        mx = info["ws"][0] + (info["axis_len"] / 2) * info["x_dir"][0]
        my = info["ws"][1] + (info["axis_len"] / 2) * info["x_dir"][1]
        tx, ty = -(my - centroid[1]), (mx - centroid[0])    # CCW tangent
        xd = info["x_dir"]
        return "far" if (xd[0] * tx + xd[1] * ty) >= 0 else "start"

    def longer_owns(ia, ib):
        """Fallback owner/butt when the loop roles are ambiguous (non-loop layouts)."""
        if abs(ia["axis_len"] - ib["axis_len"]) > 1e-6:
            return (ia, ib) if ia["axis_len"] > ib["axis_len"] else (ib, ia)
        return (ia, ib) if ia["wall"].id() < ib["wall"].id() else (ib, ia)

    def is_continued(info, end):
        """True if `info`'s `end` is continued by another perimeter wall on the same line —
        i.e. another perimeter wall's end meets this end (same run axis, coincident along the
        run, perpendicular offset within ~half a thickness).  Such an end is a "through" end:
        the wall line continues there, so the wall must reach the junction (it can't be the
        butt that gets trimmed back).  e.g. 14TH 628797's far end continues into 692873."""
        if end is None:
            return False
        run = run_axis(info)
        prp = 1 - run
        pt = info["ws"] if end == "start" else info["we"]
        for other in infos:
            if other is info or not is_perimeter(other) or run_axis(other) != run \
                    or not same_storey(info, other):
                continue
            ptol = 0.5 * max(info["thickness"], other["thickness"]) + tol
            for oe in (other["ws"], other["we"]):
                if abs(oe[run] - pt[run]) < tol and abs(oe[prp] - pt[prp]) <= ptol:
                    return True
        return False

    # ── Resolve each corner: alternating pinwheel ────────────────────────────────
    #    Around a consistent CCW loop, each wall butts the corner at one loop-end and owns
    #    the other — so ownership alternates and every wall is trimmed exactly once (the
    #    classic pinwheel, e.g. a 9×13 box → 8.5 / 12.5 / 8.5 / 12.5).  There are two mirror
    #    pinwheels (butt at the loop-forward end, or at the loop-backward end); both are
    #    valid, so we pick the one that trims more FAR ends — a far-end trim leaves the
    #    placement origin (and thus the clipped top) untouched, whereas a start-end trim
    #    nudges it.  Ambiguous corners (no clean loop) fall back to "longer wall owns".
    def other_end(e):
        return "far" if e == "start" else "start"

    def resolve(butt_at_forward):
        out = []   # (butt_info, owner_info, physical_end)
        for ia, ib in pairs:
            ea, eb = corner_end(ia, ib), corner_end(ib, ia)
            # The CCW pinwheel only makes sense for perimeter-loop corners.  Any pair
            # involving an interior wall (a T-junction or interior corner) uses "longer
            # owns" so the interior/shorter wall butts the wall it meets.
            if is_perimeter(ia) and is_perimeter(ib) and ea is not None and eb is not None:
                role_a = loop_forward_end(ia) if butt_at_forward else other_end(loop_forward_end(ia))
                role_b = loop_forward_end(ib) if butt_at_forward else other_end(loop_forward_end(ib))
                a_butt, b_butt = (ea == role_a), (eb == role_b)
                if a_butt and not b_butt:
                    out.append((ia, ib, ea)); continue
                if b_butt and not a_butt:
                    out.append((ib, ia, eb)); continue
            owner, butt = longer_owns(ia, ib)      # interior / ambiguous / no clean loop
            end = corner_end(butt, owner)
            if end is not None:
                out.append((butt, owner, end))
        return out

    # Pick the mirror pinwheel that (1) never trims a CONTINUED end — a wall whose end runs
    # into a collinear neighbour (e.g. 628797 → 692873) must reach that junction, or the
    # perpendicular wall's edge protrudes past it — then (2) trims fewer START ends (a
    # start-trim shifts the origin / nudges the clipped top).  Both mirrors are valid
    # pinwheels, so this only chooses which end of each wall is trimmed.
    def score(res):
        continued = sum(1 for butt, _, end in res if is_continued(butt, end))
        starts = sum(1 for _, _, end in res if end == "start")
        return (continued, starts)
    res_fwd, res_bwd = resolve(True), resolve(False)
    chosen = min((res_fwd, res_bwd), key=score)

    trims = {i["wall"].id(): [0.0, 0.0] for i in infos}
    for butt, owner, end in chosen:
        amt = trim_to_butt(butt, owner, end)
        if 1e-4 < amt < butt["axis_len"]:
            trims[butt["wall"].id()][0 if end == "start" else 1] += amt

    changed = 0
    for info in infos:
        st, et = trims[info["wall"].id()]
        if st > 1e-6 or et > 1e-6:
            _apply_trim(model, info, st, et)
            changed += 1

    # Anchor profiled (sloped-top) wall ends to the roof height of the wall they meet at each
    # corner, so an extended profiled wall's top doesn't overshoot the roof.
    _anchor_profiled_tops(model, infos)
    return changed


# ══════════════════════════════════════════════════════════════════════════════
# Verification
# ══════════════════════════════════════════════════════════════════════════════

PRESERVE_TYPES = [
    "IfcDoor", "IfcWindow", "IfcSlab", "IfcRoof", "IfcOpeningElement", "IfcSpace",
    "IfcRelVoidsElement", "IfcRelFillsElement", "IfcMaterialLayerSetUsage",
    "IfcBuildingStorey", "IfcFurnishingElement", "IfcBuildingElementProxy", "IfcCovering",
]


def verify(src_path, out_path, converted=()):
    before = ifcopenshell.open(src_path)
    after = ifcopenshell.open(out_path)
    converted = list(converted)
    n_conv = len(converted)
    walls = list(after.by_type("IfcWall")) + list(after.by_type("IfcWallStandardCase"))
    seen = set(); walls = [w for w in walls if not (w.id() in seen or seen.add(w.id()))]

    print(f"\n{'─' * 70}\nVERIFICATION  ({len(walls)} walls in {Path(out_path).name})\n{'─' * 70}")

    # Overlap check (penetration > ¼" is a real overlap).  The footprint test is 2D, so
    # walls stacked on different storeys would show as overlapping even though they don't
    # in 3D — only flag pairs that share a storey AND a Z range.
    storey_of = {}
    for rel in after.by_type("IfcRelContainedInSpatialStructure"):
        for e in rel.RelatedElements:
            storey_of[e.id()] = rel.RelatingStructure.id()

    def zrange(w):
        base = None
        for rep in (w.Representation.Representations if w.Representation else []):
            if rep.RepresentationIdentifier == "Body":
                for it in rep.Items:
                    s = _unwrap_solid(it)
                    if s is not None and s.is_a("IfcExtrudedAreaSolid"):
                        base = s
        z0 = w.ObjectPlacement.RelativePlacement.Location.Coordinates[2] if w.ObjectPlacement else 0.0
        h = base.Depth if base else 0.0
        return (z0, z0 + h)

    items = [(w, _footprint(i)) for w, i in
             ((w, _get_wall_info(w)) for w in walls) if i]
    found = False
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (wa, a), (wb, b) = items[i], items[j]
            if not (a[1] > b[0] + 1e-3 and b[1] > a[0] + 1e-3 and
                    a[3] > b[2] + 1e-3 and b[3] > a[2] + 1e-3):
                continue
            depth = min(min(a[1], b[1]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[2], b[2]))
            if depth <= 0.02:
                continue
            # Same storey?  And do their Z ranges actually overlap?
            if storey_of.get(wa.id()) != storey_of.get(wb.id()):
                continue
            za, zb = zrange(wa), zrange(wb)
            if not (za[1] > zb[0] + 1e-3 and zb[1] > za[0] + 1e-3):
                continue
            print(f"  ✗ OVERLAP ({depth * 12:.2f}\")  {wa.Name}  ×  {wb.Name}")
            found = True
    print("  overlaps: none ✓" if not found else "  overlaps: SEE ABOVE")

    # Non-wall elements preserved.  Glass walls re-typed to IfcFurniture (Step 0) legitimately
    # raise the IfcFurnishingElement count by the number converted — that delta is expected.
    print("  preservation (must be unchanged):")
    all_ok = True
    for t in PRESERVE_TYPES:
        nb, na = len(before.by_type(t)), len(after.by_type(t))
        expected = nb + n_conv if t == "IfcFurnishingElement" else nb
        if nb == 0 and na == 0 and expected == 0:
            continue
        ok = na == expected
        all_ok &= ok
        note = f"  (+{n_conv} from glass→furniture)" if t == "IfcFurnishingElement" and n_conv else ""
        print(f"    {t:28s} {nb} → {na}  [{'OK' if ok else 'CHANGED!'}]{note}")
    print("  preservation: all non-wall elements intact ✓" if all_ok
          else "  preservation: SOMETHING CHANGED — see above")

    # Glass-wall → furniture conversion: each converted GlobalId must now be a furniture element
    # (not a wall) keeping a Body representation + placement, and the IfcWall total must drop by
    # the number converted.
    if converted:
        after_walls = len(after.by_type("IfcWall")) + len(after.by_type("IfcWallStandardCase"))
        conv_ok = True
        for eid, gid in converted:
            try:
                e = after.by_guid(gid)
            except Exception:
                e = None
            is_furn = e is not None and e.is_a("IfcFurnishingElement")
            has_body = bool(e and e.Representation and any(
                r.RepresentationIdentifier == "Body" for r in e.Representation.Representations))
            has_plc = bool(e and e.ObjectPlacement)
            # Each converted GlobalId must now be a furniture element (never a wall) with its
            # geometry & placement intact.  (Global IfcWall count isn't asserted here — the merge
            # step also changes it — but the GUID must no longer resolve to a wall.)
            row_ok = is_furn and has_body and has_plc and (e is not None and not e.is_a("IfcWall"))
            conv_ok &= row_ok
            print(f"    glass→furniture {eid}: {e.is_a() if e else 'MISSING'} "
                  f"name='{e.Name if e else ''}' body={has_body} placed={has_plc} "
                  f"[{'OK' if row_ok else 'FAIL'}]")
        print(f"  conversion: {n_conv} glass wall(s) → furniture (IfcWall now {after_walls})  "
              f"[{'OK' if conv_ok else 'FAIL'}]")

    # Wall names
    print("  walls:")
    for w in walls:
        print(f"    {w.Name}")


# ══════════════════════════════════════════════════════════════════════════════
# Pipeline
# ══════════════════════════════════════════════════════════════════════════════

def clean(src_path, out_path):
    src_path, out_path = str(src_path), str(out_path)
    print(f"\n{'=' * 70}\n{Path(src_path).name}  →  {Path(out_path).name}\n{'=' * 70}")

    # Work on a copy — the original is never modified.
    shutil.copy2(src_path, out_path)
    model = ifcopenshell.open(out_path)

    walls = list(model.by_type("IfcWall")) + list(model.by_type("IfcWallStandardCase"))
    seen = set(); walls = [w for w in walls if not (w.id() in seen or seen.add(w.id()))]
    print(f"Walls found: {len(walls)}")

    # Step 0 — convert glass/shower partition walls into IfcFurniture glass elements.  Done
    # first so they are excluded from all wall processing (classification, division, merge).
    # Split the list BEFORE converting — converted walls are removed from the model and must
    # not be touched again (accessing a deleted entity crashes ifcopenshell).
    print("\n[Step 0] Converting glass/shower walls → IfcFurniture")
    glass_walls = [w for w in walls if _is_glass_wall(w)]
    walls = [w for w in walls if not _is_glass_wall(w)]
    converted = _convert_glass_walls(model, glass_walls)
    if converted:
        print(f"  converted {len(converted)} glass wall(s) → furniture; {len(walls)} walls remain")
    else:
        print("  no glass walls")

    # Step 3 — verify vertical (+Z) extrusion.  Walls are NOT reshaped: their original
    # body (including any clipped/sloped top) is preserved exactly.
    print("\n[Step 3] Verifying vertical (+Z) extrusions (no reshaping)")
    ok, warn = verify_z_extrusion(walls)
    print(f"  vertical +Z: {ok}   non-vertical (left as-is): {warn}")

    # Step 2 — outer-face-aware classification + rename.
    print("\n[Step 2] Classifying walls (exterior / interior / design) and renaming")
    cat_map = classify_walls(walls)
    for w in walls:
        cat = cat_map.get(w.id(), "exterior")
        old = w.Name
        w.Name = _clean_name(cat, old)
        kw = _name_keyword_category(old)
        flag = "" if kw == cat else f"   (name keyword said: {kw})"
        print(f"    {old[-34:]:34s} → {w.Name}{flag}")

    # Steps 1 & 4 — divide walls into clean butting corner joints (no overlaps).
    print("\n[Steps 1 & 4] Detecting outer faces and dividing walls (corner joints)")
    changed = divide_walls(model, walls, cat_map)
    print(f"  walls trimmed: {changed}")

    # Merge vertically-stacked segments of the same wall (floor→plate + plate→roof gable) into
    # one full-height wall.  No-op for single-storey files.
    mg = _merge_stacked_walls(model)
    if mg:
        print(f"  merged {mg} stacked wall pair(s) into full-height walls")

    # Normalize deprecated IfcWallStandardCase → IfcWall (done last so it can't dangle the
    # wall references used above).
    retyped = _normalize_wall_classes(model)
    if retyped:
        print(f"  normalized {retyped} IfcWallStandardCase → IfcWall")

    model.write(out_path)
    print(f"\nWritten: {out_path}")

    verify(src_path, out_path, converted)


def _out_path_for(src_path):
    """Insert '-W1' before the extension: 'NAME.ifc' → 'NAME-W1.ifc'."""
    p = Path(src_path)
    return p.with_name(f"{p.stem}-W1{p.suffix}")


def main():
    if len(sys.argv) >= 3:
        clean(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2:
        clean(sys.argv[1], _out_path_for(sys.argv[1]))
    else:
        ifc_dir = Path(__file__).parent / "IFCs"
        srcs = sorted(p for p in ifc_dir.glob("*.ifc")
                      if not (p.stem.endswith("-W1") or p.stem.endswith("-C1")))
        if not srcs:
            print(f"No source .ifc files found in {ifc_dir}")
            sys.exit(1)
        for src in srcs:
            clean(src, _out_path_for(src))
    print("\nDone.")


if __name__ == "__main__":
    main()
