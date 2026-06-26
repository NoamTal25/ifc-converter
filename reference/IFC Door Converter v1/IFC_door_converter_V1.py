#!/usr/bin/env python3
"""
IFC_door_converter_V1.py — FormX IFC pipeline stage: DOORS only.

Takes a Revit-exported ADU IFC and rebuilds each ``IfcDoor``'s Body geometry into a clean,
parametric, *manipulatable* swept solid — a hollow lining frame + a solid leaf panel — so
downstream FormX tooling ("make it wider") has live dimensions to drive instead of a frozen
Brep. Everything that is NOT a door is left exactly as-is.

The original file is never modified. The converted result is written to the output folder
with the suffix "-D1" before the extension, e.g.
    INPUT_IFC_FILES_HERE/Sunflower_A.ifc  →  OUTPUT_IFC_FILES_HERE/Sunflower_A-D1.ifc

Sibling of the proven window converter (``IFC Window Converter/IFC_window_converter_V1.py``).
Doors fill an IfcOpeningElement voided into a wall exactly like windows, so the
measure→rebuild→preserve→style recipe transfers directly. The only structural differences:
  - the body is a frame + SOLID leaf (not a glazed pane);
  - a door's bottom meets the floor — but we measure the door's OWN local bbox and rebuild in
    place, so position is preserved with no sill assumption;
  - a door often carries a 2D ``FootPrint`` representation alongside ``Body``: we swap ONLY the
    Body and leave the FootPrint untouched ("change as little as possible").

Per the proven FormX recipe (Gal's tools + the window converter) this authors NO bespoke
property sets and NO operation-enum type apparatus — only clean geometry + canonical Name +
standard PredefinedType + intact relationships. The classifier (canonical Name + PredefinedType
+ a glazed-vs-opaque leaf flag) is grounded in IfcDoorTypeEnum / IfcDoorTypeOperationEnum but is
COSMETIC; OperationType authoring is deferred (see "IFC door converter algorithm.md" § Phase 2).

Usage:
    python3.11 IFC_door_converter_V1.py                 # batch: INPUT_IFC_FILES_HERE → OUTPUT_IFC_FILES_HERE
    python3.11 IFC_door_converter_V1.py <in.ifc>        # single file → OUTPUT_IFC_FILES_HERE/<in>-D1.ifc
    python3.11 IFC_door_converter_V1.py <in.ifc> <out.ifc>

Dependency: ifcopenshell (tested on 0.8.5).
"""
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import ifcopenshell
import ifcopenshell.geom as geom
import ifcopenshell.util.unit as ifc_unit
import ifcopenshell.util.element as ifc_element
import ifcopenshell.util.placement as ifc_placement

# ── self-contained: this converter depends ONLY on ifcopenshell (no project-internal imports).
# The door classification (Name + PredefinedType + glazed flag) is an inline keyword scan — it's
# cosmetic, so it never affects geometry.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent.parent      # repo root (this converter now lives under reference/)

# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════
SUFFIX = "-D1"                         # door stage suffix (free; composes with -W1/-L1/-F1/-WIN1)
INPUT_DIR = _ROOT / "INPUT_IFC_FILES_HERE"
OUTPUT_DIR = _ROOT / "OUTPUT_IFC_FILES_HERE"

# Frame proportions, expressed in METRES and converted to each file's units at runtime
# (real files may be feet / mm / m — never assume).
FRAME_THK_M = 0.05                     # lining face width + mullion width (50 mm)
LEAF_THK_M = 0.045                     # opaque leaf/panel thickness (45 mm)
GLAZE_THK_M = 0.020                    # glazed-pane thickness (20 mm)

MARK = "FormX-D1 parametric door"      # stamped on Description for idempotency
BBOX_TOL_M = 0.02                      # door world-bbox drift tolerance in verify (m)
FILL_MIN = 0.95                        # face-fill ratio below which a door is NOT rectangular
                                       # (arched/angled) → preserve original, don't flatten

_GEOM_LOCAL = geom.settings(); _GEOM_LOCAL.set("use-world-coords", False)
_GEOM_WORLD = geom.settings(); _GEOM_WORLD.set("use-world-coords", True)


# ══════════════════════════════════════════════════════════════════════════════
# Geometry helpers (author swept solids along an arbitrary local depth axis)
# ══════════════════════════════════════════════════════════════════════════════
def _pt(f, *c):  return f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(x) for x in c))
def _p2(f, *c):  return f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(x) for x in c))
def _dir(f, *c): return f.create_entity("IfcDirection", DirectionRatios=tuple(float(x) for x in c))


def _extrude_along(f, profile, center, depth_dir, ref_dir, depth):
    """Extrude `profile` by `depth` along world-local `depth_dir`, centred on `center`.

    The solid's placement maps profile-local +Z onto `depth_dir` and profile-local +X onto
    `ref_dir`, so the 2D profile lies in the face plane and the sweep runs through the wall.
    Location is pulled back half the depth so the solid straddles `center`.
    """
    loc = np.asarray(center, float) - np.asarray(depth_dir, float) * (depth / 2.0)
    pos = f.create_entity("IfcAxis2Placement3D", Location=_pt(f, *loc),
                          Axis=_dir(f, *depth_dir), RefDirection=_dir(f, *ref_dir))
    return f.create_entity("IfcExtrudedAreaSolid", SweptArea=profile, Position=pos,
                           ExtrudedDirection=_dir(f, 0, 0, 1), Depth=float(depth))


def _unit(i):
    e = [0.0, 0.0, 0.0]; e[i] = 1.0; return np.array(e)


def _rect(f, xd, yd):
    return f.create_entity("IfcRectangleProfileDef", ProfileType="AREA",
                           Position=f.create_entity("IfcAxis2Placement2D", Location=_p2(f, 0, 0)),
                           XDim=float(xd), YDim=float(yd))


def _hollow(f, xd, yd, thk):
    return f.create_entity("IfcRectangleHollowProfileDef", ProfileType="AREA",
                           Position=f.create_entity("IfcAxis2Placement2D", Location=_p2(f, 0, 0)),
                           XDim=float(xd), YDim=float(yd), WallThickness=float(thk))


# ── modular part builders — each returns (solid, role) tuples; the recipe (plan) selects them ──
def _build_handles(f, center, mn, mx, dh, dv, dd, vert, horiz, depth, hardware, scale):
    """Canonical handle(s) per the recipe's `hardware` — `lever` (horizontal bar) or `flush_pull`
    (vertical bar), one per `side`, authored proud of BOTH leaf faces so it moves with the door
    and never stretches. Role `handle`. Reused on every door (no baked hardware preserved)."""
    kind = (hardware or {}).get("kind", "none")
    if kind == "none":
        return []
    hv = mn[vert] + 1.0 / scale                              # standard lever height up from the sill
    if hv > mx[vert] - 0.1 / scale:
        hv = (mn[vert] + mx[vert]) / 2.0
    ext_h = mx[horiz] - mn[horiz]
    inset = min(0.08 / scale, 0.2 * ext_h)
    if kind == "lever":
        gw, gh, pr = 0.12 / scale, 0.03 / scale, 0.04 / scale
    else:                                                    # flush_pull
        gw, gh, pr = 0.03 / scale, 0.20 / scale, 0.025 / scale
    mid = (mn[horiz] + mx[horiz]) / 2.0
    hx = {"left": mn[horiz] + inset, "right": mx[horiz] - inset,
          "center-left": mid - inset, "center-right": mid + inset}
    out = []
    for side in (hardware.get("sides") or []):
        anchor = np.asarray(center, float).copy()
        anchor = anchor + dv * (hv - center[vert]) + dh * (hx.get(side, mid) - center[horiz])
        for face in (1.0, -1.0):
            c = anchor + dd * face * (depth / 2.0 + pr / 2.0)
            out.append((_extrude_along(f, _rect(f, gw, gh), c, tuple(dd), tuple(dh), pr), "handle"))
    return out


def _door_depth(measured, plan, scale):
    """Through-wall depth. Folding/bi-fold leaves are often exported partly folded, so the
    thinnest measured axis is the folded *projection* (e.g. 1.55 ft) — clamp to a sane door
    thickness so we don't build a 1.5-ft-thick door."""
    if plan.get("folding"):
        return min(measured, 0.2 / scale)
    return measured


def _assemble(f, mn, mx, scale, depth_axis, vert, horiz, plan, glazed, layout=None):
    """Author the door body as **one frame layer** — outer hollow lining + panes + dividers — and
    the canonical handle. Two branches, but identical structure:
      • measured (glazed, `layout` present): border + pane widths + divider gaps come from the
        original's real glass extents (1-1 layout, honours uneven splits);
      • fallback (opaque / unmeasurable): even-tiled panes at the default border.
    No nested sub-frames → no lining+stile doubling. Each pane is bounded by lining+dividers, so it
    still reads as a framed leaf. Returns a flat list of (solid, role)."""
    mn = np.asarray(mn, float); mx = np.asarray(mx, float)
    ext = mx - mn; center = (mn + mx) / 2.0
    divide, span = (vert, horiz) if plan["arrangement"] == "stacked" else (horiz, vert)
    da, dd, dv, dh = _unit(divide), _unit(depth_axis), _unit(vert), _unit(horiz)
    ea, eb = float(ext[divide]), float(ext[span])
    depth = _door_depth(float(ext[depth_axis]), plan, scale)
    cap = 0.4 * min(ea, eb)
    pane_thk = min((GLAZE_THK_M if glazed else LEAF_THK_M) / scale, 0.9 * depth)

    # ── derive the divide-axis layout: pane widths + gaps (relative), and the frame border ──
    if layout and layout.get("intervals"):
        iv = layout["intervals"]                            # measured (lo, hi) per pane along divide
        widths = [hi - lo for lo, hi in iv]
        gaps = [iv[i + 1][0] - iv[i][1] for i in range(len(iv) - 1)]
        bd = (ea - (layout["pu_div"][1] - layout["pu_div"][0])) / 2.0      # side border (divide)
        bs = (eb - (layout["pu_span"][1] - layout["pu_span"][0])) / 2.0    # rail border (span)
        frame_thk = min(max(min(bd, bs), 0.0), cap) or min(FRAME_THK_M / scale, cap)
    else:                                                   # even tiling at the default border
        K = max(1, int(plan["panels"]))
        frame_thk = min(FRAME_THK_M / scale, cap)
        widths = [1.0] * K
        gaps = [1.0] * (K - 1)                              # divider = one "unit" wide, like a pane-gap

    parts = [(_extrude_along(f, _hollow(f, ea, eb, frame_thk), center, tuple(dd), tuple(da), depth), "frame")]

    # ── tile the inner opening along the divide axis by the (relative) widths + gaps ──
    inner_a = max(ea - 2 * frame_thk, ea * 0.5)
    inner_b = max(eb - 2 * frame_thk, eb * 0.5)
    # gaps default to a slim divider so panes dominate (fallback gap "1.0 unit" would be too wide)
    if not (layout and layout.get("intervals")):
        gaps = [frame_thk / max(inner_a, 1e-9) * sum(widths)] * len(gaps)   # divider ≈ frame_thk wide
    total = sum(widths) + sum(gaps)
    s = inner_a / total if total > 0 else 1.0
    off = -inner_a / 2.0
    for i, w in enumerate(widths):
        ws = w * s
        c = center + da * (off + ws / 2.0)
        parts.append((_extrude_along(f, _rect(f, ws, inner_b), c, tuple(dd), tuple(da), pane_thk), "pane"))
        off += ws
        if i < len(gaps):
            gs = gaps[i] * s
            if gs > 1e-9:
                cm = center + da * (off + gs / 2.0)
                parts.append((_extrude_along(f, _rect(f, gs, inner_b), cm, tuple(dd), tuple(da), depth),
                              "divider"))
            off += gs

    parts += _build_handles(f, center, mn, mx, dh, dv, dd, vert, horiz, depth, plan.get("hardware"), scale)
    return parts


# ══════════════════════════════════════════════════════════════════════════════
# Surface styles (leaf / frame appearance) — carried forward so doors don't go gray
# ══════════════════════════════════════════════════════════════════════════════
def _surface_styles(styles):
    """Yield IfcSurfaceStyle from an IfcStyledItem.Styles list, handling both the IFC2X3
    IfcPresentationStyleAssignment wrapper and the direct IFC4/4X3 select."""
    for s in styles or []:
        if s.is_a("IfcPresentationStyleAssignment"):
            yield from (sub for sub in (s.Styles or []) if sub.is_a("IfcSurfaceStyle"))
        elif s.is_a("IfcSurfaceStyle"):
            yield s


def _max_transparency(styles):
    """Max Transparency across an IfcStyledItem.Styles list; None if it has no rendering."""
    t = None
    for ss in _surface_styles(styles):
        for r in (ss.Styles or []):
            if r.is_a("IfcSurfaceStyleRendering"):
                tr = r.Transparency if r.Transparency is not None else 0.0
                t = tr if t is None else max(t, tr)
    return t


def _harvest_styles(model, body_sr):
    """Return (transparent_styles, opaque_styles): the original door BODY's
    `IfcStyledItem.Styles` values, bucketed by transparency (transparent = most see-through,
    opaque = the rest). Reuses the existing IfcSurfaceStyle entities verbatim — identical look,
    schema-correct. Either may be None. Read BEFORE the Body representation is swapped. Only the
    Body items are harvested (FootPrint curves carry no surface styles)."""
    if body_sr is None:
        return None, None
    items = set()
    for it in (body_sr.Items or []):
        if it.is_a("IfcMappedItem"):
            items.update(it.MappingSource.MappedRepresentation.Items or [])
        else:
            items.add(it)
    transparent = opaque = None
    trans_t = 0.0
    for styled in model.by_type("IfcStyledItem"):
        if styled.Item not in items:
            continue
        t = _max_transparency(styled.Styles)
        if t is not None and t > 0:                # see-through → glazed leaf candidate
            if t >= trans_t:
                trans_t, transparent = t, styled.Styles
        elif opaque is None:                       # opaque / unknown → frame / solid leaf
            opaque = styled.Styles
    return transparent, opaque


def _default_styles(model):
    """Author a fallback (glass, frame) style pair for doors that carried none."""
    def surf(name, rgb, transp):
        col = model.create_entity("IfcColourRgb", Red=rgb[0], Green=rgb[1], Blue=rgb[2])
        rend = model.create_entity("IfcSurfaceStyleRendering", SurfaceColour=col,
                                   Transparency=float(transp), ReflectanceMethod="NOTDEFINED")
        ss = model.create_entity("IfcSurfaceStyle", Name=name, Side="BOTH", Styles=[rend])
        if model.schema == "IFC2X3":
            return [model.create_entity("IfcPresentationStyleAssignment", Styles=[ss])]
        return [ss]
    return surf("Glass", (0.78, 0.87, 0.93), 0.55), surf("Door", (0.55, 0.43, 0.31), 0.0)


def _apply_styles(model, parts, transparent, opaque, glazed):
    """Attach surface styles to the assembled (solid, role) parts, role-keyed: `pane` → glazed-or-
    opaque per the flag; everything structural (`frame`/`divider`/`handle`) → opaque.
    Reuses harvested style entities verbatim; falls back to an authored glass+frame pair."""
    if transparent is None and opaque is None:
        transparent, opaque = _default_styles(model)
    frame_style = opaque or transparent
    pane_style = (transparent or opaque) if glazed else (opaque or transparent)
    for solid, role in parts:
        model.create_entity("IfcStyledItem", Item=solid,
                            Styles=pane_style if role == "pane" else frame_style)


# ══════════════════════════════════════════════════════════════════════════════
# Per-door helpers
# ══════════════════════════════════════════════════════════════════════════════
def _local_bbox(door, scale):
    """Axis-aligned bbox of the door in its OWN local frame, in file units.
    geom returns metres regardless of file units, so divide by the unit scale."""
    sh = geom.create_shape(_GEOM_LOCAL, door)
    v = np.array(sh.geometry.verts, dtype=float).reshape(-1, 3) / scale
    return v.min(0), v.max(0)


def _hull_area_2d(pts):
    """Convex-hull area of 2D points (Andrew's monotone chain). No scipy dependency."""
    pts = sorted(set(map(tuple, np.round(pts, 6).tolist())))
    if len(pts) < 3:
        return 0.0

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    area = 0.0
    for i in range(len(hull)):
        x1, y1 = hull[i]; x2, y2 = hull[(i + 1) % len(hull)]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def _face_fill_ratio(door):
    """How well the door's face silhouette fills its bounding rectangle. ~1.0 for a true
    rectangle; <0.95 ⇒ non-rectangular (arched/angled) → don't flatten it."""
    sh = geom.create_shape(_GEOM_LOCAL, door)
    v = np.array(sh.geometry.verts, dtype=float).reshape(-1, 3)
    ext = v.max(0) - v.min(0)
    d = int(np.argmin(ext)); u, w = [i for i in range(3) if i != d]
    bbox = ext[u] * ext[w]
    if bbox <= 0:
        return 1.0
    return _hull_area_2d(v[:, [u, w]]) / bbox


def _world_bbox(door):
    """Door bbox in world coords (metres) — for the verify() position check."""
    sh = geom.create_shape(_GEOM_WORLD, door)
    v = np.array(sh.geometry.verts, dtype=float).reshape(-1, 3)
    return v.min(0), v.max(0)


def _trailing_id(name):
    """Revit names end in ':<elementid>' — keep it as the stable handle, like Gal does."""
    if name and ":" in name:
        tail = name.rsplit(":", 1)[-1].strip()
        if tail:
            return tail
    return None


# ── measure the original's real frame border (so the rebuilt lining isn't skinnier) ──
def _ax3_matrix(p):
    """IfcAxis2Placement3D → 4×4."""
    loc = np.array(p.Location.Coordinates, float)
    z = np.array(p.Axis.DirectionRatios, float) if getattr(p, "Axis", None) else np.array([0, 0, 1.0])
    x = np.array(p.RefDirection.DirectionRatios, float) if getattr(p, "RefDirection", None) else np.array([1, 0, 0.0])
    z = z / np.linalg.norm(z); x = x - np.dot(x, z) * z; x = x / np.linalg.norm(x); y = np.cross(z, x)
    M = np.eye(4); M[:3, 0] = x; M[:3, 1] = y; M[:3, 2] = z; M[:3, 3] = loc
    return M


def _profile_pts(pr):
    """2D corner/vertex points of a swept profile, or None if its shape isn't simple."""
    if pr.is_a("IfcRectangleProfileDef"):       # (IfcRectangleHollowProfileDef is-a this, fine)
        x, y = pr.XDim / 2.0, pr.YDim / 2.0
        ox, oy = (pr.Position.Location.Coordinates if pr.Position else (0.0, 0.0))
        return [(ox + sx * x, oy + sy * y) for sx in (-1, 1) for sy in (-1, 1)]
    if pr.is_a("IfcArbitraryClosedProfileDef"):
        c = pr.OuterCurve
        if c.is_a("IfcPolyline"):
            return [tuple(p.Coordinates[:2]) for p in c.Points]
        if c.is_a("IfcIndexedPolyCurve") and c.Points.is_a("IfcCartesianPointList2D"):
            return [tuple(p[:2]) for p in c.Points.CoordList]
    if pr.is_a("IfcCircleProfileDef"):
        r = pr.Radius
        return [(-r, -r), (r, -r), (-r, r), (r, r)]
    return None


def _item_points(model, it):
    """Vertices of one representation item in the representation frame (FILE units), or None.
    Extruded solids → profile×placement×depth; Breps → all referenced 3D points (via traverse)."""
    pts = []
    if it.is_a("IfcExtrudedAreaSolid"):
        pp = _profile_pts(it.SweptArea)
        if pp is None:
            return None
        M = _ax3_matrix(it.Position); dep = it.Depth
        for (x, y) in pp:
            for t in (0.0, dep):
                pts.append((M @ np.array([x, y, t, 1.0]))[:3])
    else:
        for e in model.traverse(it):
            if e.is_a("IfcCartesianPoint") and len(e.Coordinates) == 3:
                pts.append(np.array(e.Coordinates, float))
            elif e.is_a("IfcCartesianPointList3D"):
                pts.extend(np.array(c, float) for c in e.CoordList)
    return np.array(pts) if pts else None


def _geom_items(body_sr):
    """The actual geometry items of a Body shaperep (dereferencing a mapped item to its source)."""
    out = []
    for it in (body_sr.Items or []):
        if it.is_a("IfcMappedItem"):
            out += list(it.MappingSource.MappedRepresentation.Items or [])
        else:
            out.append(it)
    return out


def _measure_layout(model, body_sr, divide, span):
    """Measure the original's real member layout from its **transparent (glass) sub-solids** — the
    1-1 source for `_assemble`'s measured branch. Returns, in the door's local frame (FILE units):
      { 'intervals': [(lo,hi), …]  — each glazed pane's extent along the divide axis, ordered;
        'pu_div':    (lo,hi)       — union of panes along the divide axis (→ side border);
        'pu_span':   (lo,hi) }     — union of panes along the span axis  (→ rail border).
    Returns None if there are no transparent panes (opaque door) or shapes are unreadable → the
    caller falls back to even-tiling. Uses the same transparency signal as the style harvest, so
    it's extent-measurement, not semantic mesh inference."""
    try:
        items = _geom_items(body_sr)
        if not items:
            return None
        ids = {it.id() for it in items}
        trans = {}
        for st in model.by_type("IfcStyledItem"):
            if st.Item and st.Item.id() in ids:
                t = _max_transparency(st.Styles)
                if t:
                    trans[st.Item.id()] = max(trans.get(st.Item.id(), 0.0), t)
        boxes = []
        for it in items:
            if trans.get(it.id(), 0.0) > 0:
                p = _item_points(model, it)
                if p is not None:
                    boxes.append((p.min(0), p.max(0)))
        if not boxes:
            return None
        # cluster glass sub-solids into panes: ordered by divide-centre, merge ones that overlap
        # along the divide axis (same leaf); a mullion gap separates leaves into distinct panes.
        boxes.sort(key=lambda b: (b[0][divide] + b[1][divide]) / 2.0)
        panes = [[boxes[0][0].copy(), boxes[0][1].copy()]]
        for lo, hi in boxes[1:]:
            if lo[divide] <= panes[-1][1][divide] + 1e-9:
                panes[-1][0] = np.minimum(panes[-1][0], lo)
                panes[-1][1] = np.maximum(panes[-1][1], hi)
            else:
                panes.append([lo.copy(), hi.copy()])
        intervals = [(float(p[0][divide]), float(p[1][divide])) for p in panes]
        allmn = np.min([p[0] for p in panes], axis=0)
        allmx = np.max([p[1] for p in panes], axis=0)
        return {"intervals": intervals,
                "pu_div": (float(allmn[divide]), float(allmx[divide])),
                "pu_span": (float(allmn[span]), float(allmx[span]))}
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# Classification — comprehensive taxonomy grounded in IfcDoorTypeEnum /
# IfcDoorTypeOperationEnum. COSMETIC: drives the canonical Name, the PredefinedType, and a
# glazed-vs-opaque leaf flag only — never geometry. OperationType authoring is deferred (v2).
# ══════════════════════════════════════════════════════════════════════════════
# Layer A — macro category → PredefinedType (all 5 IfcDoorTypeEnum values). Order matters:
# BOOM_BARRIER/TURNSTILE/TRAPDOOR before GATE (so "boom gate" is a barrier, not a gate).
_PREDEF_KEYWORDS = [
    ("turnstile", "TURNSTILE"),
    ("boom", "BOOM_BARRIER"), ("barrier", "BOOM_BARRIER"),
    ("trapdoor", "TRAPDOOR"), ("trap door", "TRAPDOOR"), ("hatch", "TRAPDOOR"),
    ("attic access", "TRAPDOOR"), ("floor access", "TRAPDOOR"), ("ceiling access", "TRAPDOOR"),
    ("gate", "GATE"),
]

# Layer B — operation family → (canonical Name label, representative IfcDoorTypeOperationEnum).
# Ordered specific→general; first keyword match wins (so "double sliding" beats "sliding").
_OP_FAMILIES = [
    (("revolving",),                                            "Revolving door",          "REVOLVING"),
    (("rolling", "roll up", "rollup", "overhead", "garage",
      "sectional", "coiling", "shutter"),                       "Rolling / overhead door", "ROLLINGUP"),
    (("lifting", "up and over", "tilt"),                        "Lifting door",            "LIFTING_VERTICAL_LEFT"),
    (("double sliding",),                                       "Double sliding door",     "DOUBLE_DOOR_SLIDING"),
    (("double folding", "double fold"),                         "Double folding door",     "DOUBLE_DOOR_FOLDING"),
    (("double acting", "double swing"),                         "Double-acting door",      "DOUBLE_SWING_RIGHT"),
    (("pocket",),                                               "Pocket door",             "SLIDING_TO_RIGHT"),
    (("sliding", "slider", "barn", "patio", "bypass"),          "Sliding door",            "SLIDING_TO_RIGHT"),
    (("bifold", "bi fold", "folding", "fold", "four fold",
      "accordion", "multifold"),                                "Folding / bi-fold door",  "FOLDING_TO_RIGHT"),
    (("fixed panel", "sidelite", "sidelight", "swing fixed"),   "Swing + fixed-panel door","SWING_FIXED_RIGHT"),
    (("double", "french", "pair"),                              "Double / French door",    "DOUBLE_DOOR_SINGLE_SWING"),
    # default catch-all (single/flush/hinged/swing/panel or unknown)
    ((),                                                        "Single door",             "SINGLE_SWING_RIGHT"),
]

# Layer C — glazing modifier (orthogonal to operation): a glazed leaf wears the see-through style.
_GLAZE_KEYWORDS = ("glass", "glazed", "storefront", "vision lite", "vision")

# Friendly base label for the special macro categories (when not a plain DOOR).
_PREDEF_LABEL = {"GATE": "Gate", "TRAPDOOR": "Trapdoor",
                 "BOOM_BARRIER": "Boom barrier", "TURNSTILE": "Turnstile"}


def _norm(name):
    """Lowercase + replace Revit separators (-, _, /) with spaces and collapse runs, so keyword
    matching is robust to 'Door-Double-Sliding' vs 'four_fold' vs 'Double Full Glass'."""
    return re.sub(r"\s+", " ", re.sub(r"[-_/]", " ", (name or "").lower())).strip()


# Families whose parametric rebuild divides up the HEIGHT into stacked sections (horizontal
# rails) instead of side-by-side leaves (vertical mullions) — e.g. overhead / sectional garage.
_STACKED_FAMILIES = {"Rolling / overhead door", "Lifting door"}
# Hardware by family: a recessed bar pull for sliding-type leaves; nothing for overhead/revolving.
_FLUSH_FAMILIES = {"Sliding door", "Double sliding door", "Pocket door",
                   "Folding / bi-fold door", "Double folding door"}
_NO_HANDLE_FAMILIES = {"Rolling / overhead door", "Lifting door", "Revolving door"}
_FOLDING_FAMILIES = {"Folding / bi-fold door", "Double folding door"}


def _rebuild_plan(label, real_op, nm):
    """Classification → **rebuild recipe** the assembler composes: which modules run and how.
    Returns {panels, arrangement, framed, folding, hardware}. Panel count comes from the
    authoritative `OperationType` enum when present, else the family Name; `arrangement` picks
    which way leaves divide (width vs height); `framed` whether each leaf gets a sub-frame;
    `hardware` which canonical handle + on which sides."""
    op = real_op or ""
    if op.startswith("DOUBLE_DOOR") or op in ("SWING_FIXED_LEFT", "SWING_FIXED_RIGHT"):
        panels = 2
    elif op and op not in ("NOTDEFINED", "USERDEFINED"):
        panels = 1                                   # single swing / sliding / folding leaf
    elif "four fold" in nm or "4 panel" in nm or "4 fold" in nm:
        panels = 4
    elif "3 panel" in nm or "3 fold" in nm or "tri fold" in nm:
        panels = 3
    elif any(k in nm for k in ("2 panel", "double", "french", "bi parting")):
        panels = 2
    elif any(k in nm for k in ("bifold", "bi fold", "folding", "fold")):
        panels = 2
    else:
        panels = 1
    stacked = label in _STACKED_FAMILIES
    if stacked and panels == 1:
        panels = 4                                   # sectional/overhead doors default to 4 panels

    # framed: a plain flush single is a bare slab; everything else gets per-leaf stile/rail frames.
    framed = not (panels == 1 and label == "Single door" and "flush" in nm and "glass" not in nm)

    # hardware: kind by family, side(s) by panel count + handedness from the operation enum.
    if label in _NO_HANDLE_FAMILIES:
        kind = "none"
    elif label in _FLUSH_FAMILIES:
        kind = "flush_pull"
    else:
        kind = "lever"
    handed = "left" if op.endswith("LEFT") else "right"
    if kind == "none":
        sides = []
    elif panels >= 2:
        sides = ["center-left", "center-right"]      # one per leaf, meeting at the mullion
    else:
        sides = [handed]
    return {"panels": panels, "arrangement": "stacked" if stacked else "side-by-side",
            "framed": framed, "folding": label in _FOLDING_FAMILIES,
            "hardware": {"kind": kind, "sides": sides}}


def _classify(door, dt):
    """Map the door/type family Name into the taxonomy + pick the rebuild methodology. Returns
    (predef, name_label, op_enum, glazed, plan). The label/predef/glazed are cosmetic; `plan` is
    the structural part — it drives which parametric geometry gets built."""
    nm = " ".join(_norm(s) for s in (door.Name, getattr(dt, "Name", None)) if s)
    predef = "DOOR"
    for kw, pt in _PREDEF_KEYWORDS:
        if kw in nm:
            predef = pt
            break
    label, op_enum = "Single door", "SINGLE_SWING_RIGHT"
    for kws, lbl, op in _OP_FAMILIES:
        if not kws or any(kw in nm for kw in kws):
            label, op_enum = lbl, op
            break
    if predef in _PREDEF_LABEL:          # gate/trapdoor/etc. name themselves, not by swing family
        label = _PREDEF_LABEL[predef]
    glazed = any(kw in nm for kw in _GLAZE_KEYWORDS)
    real_op = (getattr(door, "OperationType", None)
               or (getattr(dt, "OperationType", None) if dt else None) or "")
    plan = _rebuild_plan(label, real_op, nm)
    return predef, label, op_enum, glazed, plan


def _canonical_name(label, door):
    eid = _trailing_id(door.Name)
    return f"{label}: {eid}" if eid else label


def _body_rep(door):
    """The door's 'Body' IfcShapeRepresentation (or None) plus a usable context.
    The FootPrint and any other representations are left for the caller to preserve."""
    rep = door.Representation
    reps = (rep.Representations if rep else None) or []
    body = next((r for r in reps if r.RepresentationIdentifier == "Body"), None)
    ctx = body.ContextOfItems if body else (reps[0].ContextOfItems if reps else None)
    return body, ctx


def _orig_geom_kind(body_sr):
    """A short label for the door's ORIGINAL Body representation, for the log."""
    if not body_sr or not body_sr.Items:
        return "no-geom"
    it = body_sr.Items[0]
    if it.is_a("IfcMappedItem"):
        inner = it.MappingSource.MappedRepresentation
        kind = inner.Items[0].is_a() if inner.Items else "?"
        return f"mapped/{inner.RepresentationType}({kind})"
    return body_sr.RepresentationType or "?"


def _already_converted(door):
    return (door.Description or "") == MARK


def _cleanup_old_body(model, body_sr):
    """Remove the door's old per-instance Body shaperep once it's been replaced. De-references
    it from any IfcPresentationLayerAssignment first (so no layer is left pointing at an empty
    rep), removes its now-orphaned items, and NEVER touches a shared IfcRepresentationMap (other
    doors may still map it) or the FootPrint. The enclosing IfcProductDefinitionShape is kept —
    it still holds the FootPrint + the new Body.
    """
    if body_sr is None:
        return
    try:
        for inv in list(model.get_inverse(body_sr)):
            if inv.is_a("IfcPresentationLayerAssignment"):
                kept = [x for x in inv.AssignedItems if x.id() != body_sr.id()]
                if kept:
                    inv.AssignedItems = kept
                else:
                    model.remove(inv)
        for it in list(body_sr.Items or []):           # mapped item is per-door; map is shared
            if len(model.get_inverse(it)) <= 1:
                model.remove(it)
        model.remove(body_sr)
    except Exception as e:                              # cleanup must never break a conversion
        print(f"     (old-body cleanup skipped: {e!r})")


def reauthor_door(model, door, scale, predef, label, glazed, plan):
    """Replace the door's BODY representation with a clean parametric body built per `plan`
    (panel count + arrangement), sized from its own geometry — preserving GlobalId,
    ObjectPlacement, and every non-Body representation (FootPrint). Returns (mn, mx) or raises."""
    prod = door.Representation
    body_sr, ctx = _body_rep(door)
    if body_sr is None:
        raise ValueError("no Body representation to rebuild")
    mn, mx = _local_bbox(door, scale)                  # raises if geometry can't be read
    # Axis roles: depth = thinnest; of the two face axes, the one whose WORLD direction is most
    # vertical (largest |Z|) is the height axis. Side-by-side leaves divide the width; stacked
    # sections divide the height. (Empirical, orientation-agnostic — no exporter axis assumption.)
    ext = np.asarray(mx, float) - np.asarray(mn, float)
    d = int(np.argmin(ext))
    u, v = [i for i in range(3) if i != d]
    M = ifc_placement.get_local_placement(door.ObjectPlacement) if door.ObjectPlacement else np.eye(4)
    vert, horiz = (u, v) if abs(M[2, u]) >= abs(M[2, v]) else (v, u)
    divide, span = (vert, horiz) if plan["arrangement"] == "stacked" else (horiz, vert)
    transparent, opaque = _harvest_styles(model, body_sr)   # read BEFORE swapping the Body
    layout = _measure_layout(model, body_sr, divide, span) if glazed else None   # 1-1 members
    parts = _assemble(model, mn, mx, scale, d, vert, horiz, plan, glazed, layout)
    _apply_styles(model, parts, transparent, opaque, glazed)
    new_body = model.create_entity("IfcShapeRepresentation", ContextOfItems=ctx,
                                   RepresentationIdentifier="Body", RepresentationType="SweptSolid",
                                   Items=[s for s, _ in parts])
    # Swap ONLY the Body shaperep inside the existing product-definition-shape; keep FootPrint &co.
    # Compare by .id() — ifcopenshell hands back fresh wrappers per access, so `is` would miss.
    bid = body_sr.id()
    prod.Representations = [new_body if r.id() == bid else r for r in (prod.Representations or [])]
    _cleanup_old_body(model, body_sr)

    door.Name = _canonical_name(label, door)
    door.Description = MARK
    if hasattr(door, "PredefinedType"):
        try:
            door.PredefinedType = predef                # IFC2X3 IfcDoor has no PredefinedType
        except Exception:
            pass
    return mn, mx


# ══════════════════════════════════════════════════════════════════════════════
# Main pipeline
# ══════════════════════════════════════════════════════════════════════════════
def convert(src_path, out_path):
    src_path, out_path = str(src_path), str(out_path)
    print(f"\n{'=' * 74}\n{Path(src_path).name}  →  {Path(out_path).name}\n{'=' * 74}")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, out_path)
    model = ifcopenshell.open(out_path)
    scale = ifc_unit.calculate_unit_scale(model)

    doors = model.by_type("IfcDoor")                   # includes IfcDoorStandardCase
    print(f"[scan] {len(doors)} IfcDoor | unit scale {scale:.4f} m/unit | schema {model.schema}")
    stats = Counter()
    for d in doors:
        dt = ifc_element.get_type(d)
        if _already_converted(d):
            stats["already"] += 1
            continue
        predef, label, op_enum, glazed, plan = _classify(d, dt)
        try:
            # Non-rectangular doors (arched/angled) would be flattened to a rectangle by the
            # neutral template — so preserve their original geometry instead.
            fill = _face_fill_ratio(d)
            if fill < FILL_MIN:
                stats["kept-nonrect"] += 1
                print(f"  [keep]    {d.Name!r:46}  non-rectangular (fill {fill:.2f}) → "
                      f"original geometry preserved (rebuild would flatten it)")
                continue
            body_sr, _ = _body_rep(d)
            kind = _orig_geom_kind(body_sr)
            reauthor_door(model, d, scale, predef, label, glazed, plan)
            stats["rebuilt"] += 1
            print(f"  [rebuilt] {d.Name!r:46}  (was {kind}, {label}, "
                  f"{plan['panels']}-panel/{plan['arrangement']}"
                  f"{', glazed' if glazed else ''}, PT={predef})")
        except Exception as e:
            stats["skipped"] += 1
            print(f"  [SKIP]    {d.Name!r:46}  geometry unreadable → left untouched: {e!r}")

    model.write(out_path)
    print(f"[write] {out_path}")
    print(f"[stats] rebuilt={stats['rebuilt']} kept-nonrect={stats['kept-nonrect']} "
          f"skipped={stats['skipped']} already={stats['already']}")
    verify(src_path, out_path)


# ══════════════════════════════════════════════════════════════════════════════
# Built-in verification (reopen src + out; assert "only the doors changed")
# ══════════════════════════════════════════════════════════════════════════════
# Element/relationship types that must be IDENTICAL in count before vs after.
_PRESERVE_TYPES = [
    "IfcDoor", "IfcWindow", "IfcWall", "IfcWallStandardCase", "IfcSlab", "IfcRoof",
    "IfcOpeningElement", "IfcSpace", "IfcBuildingStorey", "IfcCovering",
    "IfcFurnishingElement", "IfcBuildingElementProxy",
    "IfcRelFillsElement", "IfcRelVoidsElement", "IfcRelContainedInSpatialStructure",
    "IfcRelDefinesByType", "IfcRelAggregates",
]


def _has_footprint(door):
    rep = door.Representation
    return bool(rep and any((r.RepresentationIdentifier or "") == "FootPrint"
                            for r in (rep.Representations or [])))


def verify(src_path, out_path):
    print("[verify]")
    before = ifcopenshell.open(src_path)
    after = ifcopenshell.open(out_path)
    ok = True

    # 1. Non-door element/relationship preservation (counts unchanged).
    print("  preservation (before → after):")
    for t in _PRESERVE_TYPES:
        nb, na = len(before.by_type(t)), len(after.by_type(t))
        if nb == 0 and na == 0:
            continue
        good = nb == na
        ok &= good
        flag = "OK" if good else "CHANGED!"
        print(f"    {t:34s} {nb} → {na}  [{flag}]")

    # 2. Door GlobalIds preserved (none added/dropped/re-minted).
    gb = {d.GlobalId for d in before.by_type("IfcDoor")}
    ga = {d.GlobalId for d in after.by_type("IfcDoor")}
    good = gb == ga
    ok &= good
    print(f"  door GlobalIds preserved: {len(gb & ga)}/{len(gb)}  [{'OK' if good else 'CHANGED!'}]")

    # 3. Openings did not move (world placement identical) — doors stay located.
    bplace = {o.GlobalId: ifc_placement.get_local_placement(o.ObjectPlacement)
              for o in before.by_type("IfcOpeningElement") if o.ObjectPlacement}
    moved = 0
    for o in after.by_type("IfcOpeningElement"):
        m0 = bplace.get(o.GlobalId)
        if m0 is not None and o.ObjectPlacement is not None:
            if not np.allclose(m0, ifc_placement.get_local_placement(o.ObjectPlacement), atol=1e-6):
                moved += 1
    ok &= moved == 0
    print(f"  openings moved: {moved}  [{'OK — none moved' if moved == 0 else 'CHANGED!'}]")

    # 4. Each door occupies the same FACE footprint as before (stays in its opening / on the
    #    floor). Drift is measured on the two largest (face = width×height) world axes only — the
    #    thin through-wall axis is allowed to differ, since a proud handle and the folding-depth
    #    clamp legitimately change the depth envelope without moving the door's face.
    bbox_b = {}
    for d in before.by_type("IfcDoor"):
        try:
            bbox_b[d.GlobalId] = _world_bbox(d)
        except Exception:
            pass
    max_drift = 0.0; checked = 0
    for d in after.by_type("IfcDoor"):
        b = bbox_b.get(d.GlobalId)
        if b is None:
            continue
        try:
            mn_a, mx_a = _world_bbox(d)
        except Exception:
            continue
        face = np.argsort(b[1] - b[0])[1:]          # the 2 largest-extent axes = the door face
        drift = float(max(np.abs(np.r_[(mn_a - b[0])[face], (mx_a - b[1])[face]])))
        max_drift = max(max_drift, drift); checked += 1
    good = max_drift <= BBOX_TOL_M
    ok &= good
    print(f"  door face-bbox drift (n={checked}): max {max_drift * 1000:.1f} mm  "
          f"[{'OK' if good else f'> {BBOX_TOL_M*1000:.0f} mm!'}]")

    # 4b. Every rebuilt door's new Body items carry surface styles (else they render gray).
    styled_ids = {s.Item.id() for s in after.by_type("IfcStyledItem") if s.Item}
    unstyled = 0
    for d in after.by_type("IfcDoor"):
        if (d.Description or "") != MARK:
            continue
        body_sr, _ = _body_rep(d)
        items = (body_sr.Items if body_sr else None) or []
        if items and not all(it.id() in styled_ids for it in items):
            unstyled += 1
    ok &= unstyled == 0
    print(f"  rebuilt doors missing surface styles: {unstyled}  "
          f"[{'OK — all styled' if unstyled == 0 else 'WOULD RENDER GRAY!'}]")

    # 4c. FootPrint (2D) representation preserved — every door that had one still has one.
    fp_before = {d.GlobalId for d in before.by_type("IfcDoor") if _has_footprint(d)}
    fp_after = {d.GlobalId for d in after.by_type("IfcDoor") if _has_footprint(d)}
    lost = fp_before - fp_after
    ok &= not lost
    print(f"  FootPrint reps preserved: {len(fp_before & fp_after)}/{len(fp_before)}  "
          f"[{'OK' if not lost else f'{len(lost)} LOST!'}]")

    # 5. Schema validity gate (manual FormX/viewer test is ground truth).
    try:
        import ifcopenshell.validate as V
        lb = V.json_logger(); V.validate(before, lb)
        la = V.json_logger(); V.validate(after, la)
        nb, na = len(lb.statements), len(la.statements)
        good = na <= nb                                # we must not INTRODUCE schema errors
        ok &= good
        note = "OK" if good else "NEW ERRORS INTRODUCED!"
        pre = f" ({nb} pre-existing in source)" if nb else ""
        print(f"  ifcopenshell.validate: source {nb} → output {na}{pre}  [{note}]")
    except Exception as e:
        print(f"  ifcopenshell.validate: skipped ({e!r})")

    print(f"\n  RESULT: {'ALL CHECKS PASSED ✓' if ok else 'SEE WARNINGS ABOVE ✗'}")
    return ok


def _out_path_for(src_path):
    p = Path(src_path)
    return OUTPUT_DIR / f"{p.stem}{SUFFIX}{p.suffix}"


def main():
    if len(sys.argv) >= 3:
        convert(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2:
        convert(sys.argv[1], _out_path_for(sys.argv[1]))
    else:
        srcs = sorted(p for p in INPUT_DIR.glob("*.ifc") if not p.stem.endswith(SUFFIX))
        if not srcs:
            print(f"No source .ifc files found in {INPUT_DIR}")
            sys.exit(1)
        for src in srcs:
            convert(src, _out_path_for(src))
    print("\nDone.")


if __name__ == "__main__":
    main()
