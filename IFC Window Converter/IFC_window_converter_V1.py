#!/usr/bin/env python3
"""
IFC_window_converter_V1.py — FormX IFC pipeline stage: WINDOWS only.

Takes a Revit-exported ADU IFC and rebuilds each ``IfcWindow``'s geometry into a clean,
parametric, *manipulatable* swept solid — so downstream FormX tooling ("make it wider")
has a live frame+pane to drive instead of a frozen Brep. Everything that is NOT a window
is left exactly as-is.

The original file is never modified. The converted result is written to the output folder
with the suffix "-WIN1" before the extension, e.g.
    INPUT_IFC_FILES_HERE/HUDSON ADU.ifc  →  OUTPUT_IFC_FILES_HERE/HUDSON ADU-WIN1.ifc

Design follows the three working sibling tools in ``Gal_Similar_Project_Refrences/``
(walls cleanup / levels organizer / floors definer). Their proven recipe — confirmed by
what they actually author — is:

    clean regenerable geometry + canonical Name + standard PredefinedType
    + intact relationships, and nothing more.

So this stage authors NO bespoke property sets and NO operation-enum window-type apparatus
(that richer "golden spec" layer is deferred — see "IFC window converter algorithm.md" §
Phase 2). It only:
  - rebuilds each baked window body as a clean hollow-frame + centered glazed pane,
    sized and oriented from the window's OWN measured geometry (so it lands exactly in its
    opening), preserving GlobalId and ObjectPlacement;
  - sets a canonical Name and PredefinedType (WINDOW / SKYLIGHT);
  - leaves the opening / fill / void / containment relationships untouched.

Usage:
    python3 IFC_window_converter_V1.py                 # batch: INPUT_IFC_FILES_HERE → OUTPUT_IFC_FILES_HERE
    python3 IFC_window_converter_V1.py <in.ifc>        # single file → OUTPUT_IFC_FILES_HERE/<in>-WIN1.ifc
    python3 IFC_window_converter_V1.py <in.ifc> <out.ifc>

Dependency: ifcopenshell (tested on 0.8.5).
"""
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
# The style label + skylight detection are done inline (see _style_token) — they're cosmetic,
# so a lightweight keyword scan replaces the old classify.py / bakedness.py dependencies.
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════
SUFFIX = "-WIN1"                       # window stage suffix (W1 is taken by walls cleanup)
INPUT_DIR = _ROOT / "INPUT_IFC_FILES_HERE"
OUTPUT_DIR = _ROOT / "OUTPUT_IFC_FILES_HERE"

# Frame proportions, expressed in METRES and converted to each file's units at runtime
# (real files may be feet / mm / m — never assume).
FRAME_THK_M = 0.05                     # lining face width (50 mm)
GLAZE_THK_M = 0.020                    # glazing-unit thickness (20 mm)

MARK = "FormX-WIN1 parametric window"  # stamped on Description for idempotency
BBOX_TOL_M = 0.02                      # window world-bbox drift tolerance in verify (m)
FILL_MIN = 0.95                        # face-fill ratio below which a window is NOT rectangular
                                       # (trapezoid/triangle/arch) → preserve original, don't flatten

# Friendly operation word for the canonical Name, keyed off the style_code operation token.
_OP_WORD = {
    "FIXED": "Fixed", "CASEMENT": "Casement", "AWNING": "Awning", "HOPPER": "Hopper",
    "SINGLEHUNG": "Single-hung", "DOUBLEHUNG": "Double-hung", "HUNG": "Hung",
    "SLIDER": "Slider", "TILTTURN": "Tilt-turn", "PIVOTH": "Pivot", "PIVOTV": "Pivot",
    "SKYLIGHT": "Skylight",
}

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


def _build_window_solids(f, mn, mx, scale):
    """Clean hollow lining frame + centred glazed pane filling the local bbox [mn, mx]
    (file units). Returns [frame_solid, pane_solid]. Symmetric, so width-vs-height ambiguity
    is irrelevant: it occupies the same box the original did → world position is preserved.
    """
    mn = np.asarray(mn, float); mx = np.asarray(mx, float)
    ext = mx - mn
    center = (mn + mx) / 2.0
    d = int(np.argmin(ext))               # depth = thinnest axis (through-wall)
    u, v = [i for i in range(3) if i != d]
    eu, ev, depth = float(ext[u]), float(ext[v]), float(ext[d])

    frame_thk = min(FRAME_THK_M / scale, 0.4 * min(eu, ev))
    glaze_thk = min(GLAZE_THK_M / scale, 0.6 * depth)

    def unit(i):
        e = [0.0, 0.0, 0.0]; e[i] = 1.0; return tuple(e)
    du, dv, dd = unit(u), unit(v), unit(d)

    frame_prof = f.create_entity("IfcRectangleHollowProfileDef", ProfileType="AREA",
                                 Position=f.create_entity("IfcAxis2Placement2D", Location=_p2(f, 0, 0)),
                                 XDim=eu, YDim=ev, WallThickness=float(frame_thk))
    frame = _extrude_along(f, frame_prof, center, dd, du, depth)

    pane_prof = f.create_entity("IfcRectangleProfileDef", ProfileType="AREA",
                                Position=f.create_entity("IfcAxis2Placement2D", Location=_p2(f, 0, 0)),
                                XDim=max(eu - 2 * frame_thk, eu * 0.5),
                                YDim=max(ev - 2 * frame_thk, ev * 0.5))
    pane = _extrude_along(f, pane_prof, center, dd, du, glaze_thk)
    return frame, pane


# ══════════════════════════════════════════════════════════════════════════════
# Surface styles (glass / frame appearance) — carried forward so windows don't go gray
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


def _harvest_styles(model, win):
    """Return (glass_styles, frame_styles): the original window's `IfcStyledItem.Styles`
    values, bucketed by transparency (glass = most transparent, frame = opaque). Reuses the
    existing IfcSurfaceStyle entities verbatim — identical look, schema-correct. Either may
    be None. Read BEFORE the representation is swapped."""
    rep = win.Representation
    if not rep or not rep.Representations:
        return None, None
    items = set()
    for sr in rep.Representations:
        for it in (sr.Items or []):
            if it.is_a("IfcMappedItem"):
                items.update(it.MappingSource.MappedRepresentation.Items or [])
            else:
                items.add(it)
    glass = frame = None
    glass_t = 0.0
    for styled in model.by_type("IfcStyledItem"):
        if styled.Item not in items:
            continue
        t = _max_transparency(styled.Styles)
        if t is not None and t > 0:               # transparent → glass; keep the most transparent
            if t >= glass_t:
                glass_t, glass = t, styled.Styles
        elif frame is None:                       # opaque / unknown → frame
            frame = styled.Styles
    return glass, frame


def _default_styles(model):
    """Author a fallback (glass, frame) style pair for windows that carried none."""
    def surf(name, rgb, transp):
        col = model.create_entity("IfcColourRgb", Red=rgb[0], Green=rgb[1], Blue=rgb[2])
        rend = model.create_entity("IfcSurfaceStyleRendering", SurfaceColour=col,
                                   Transparency=float(transp), ReflectanceMethod="NOTDEFINED")
        ss = model.create_entity("IfcSurfaceStyle", Name=name, Side="BOTH", Styles=[rend])
        if model.schema == "IFC2X3":
            return [model.create_entity("IfcPresentationStyleAssignment", Styles=[ss])]
        return [ss]
    return surf("Glass", (0.78, 0.87, 0.93), 0.55), surf("Frame", (0.55, 0.55, 0.55), 0.0)


def _apply_styles(model, frame_solid, pane_solid, glass, frame):
    """Attach surface styles to the new frame + pane items (glass on the pane)."""
    if glass is None and frame is None:
        glass, frame = _default_styles(model)
    glass = glass or frame                        # never leave an item gray
    frame = frame or glass
    model.create_entity("IfcStyledItem", Item=frame_solid, Styles=frame)
    model.create_entity("IfcStyledItem", Item=pane_solid, Styles=glass)


# ══════════════════════════════════════════════════════════════════════════════
# Per-window helpers
# ══════════════════════════════════════════════════════════════════════════════
def _local_bbox(win, scale):
    """Axis-aligned bbox of the window in its OWN local frame, in file units.
    geom returns metres regardless of file units, so divide by the unit scale."""
    sh = geom.create_shape(_GEOM_LOCAL, win)
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


def _face_fill_ratio(win):
    """How well the window's face silhouette fills its bounding rectangle. ~1.0 for a true
    rectangle; ~0.5 for a triangle/trapezoid; <0.95 ⇒ non-rectangular (don't flatten it)."""
    sh = geom.create_shape(_GEOM_LOCAL, win)
    v = np.array(sh.geometry.verts, dtype=float).reshape(-1, 3)
    ext = v.max(0) - v.min(0)
    d = int(np.argmin(ext)); u, w = [i for i in range(3) if i != d]
    bbox = ext[u] * ext[w]
    if bbox <= 0:
        return 1.0
    return _hull_area_2d(v[:, [u, w]]) / bbox


def _world_bbox(win):
    """Window bbox in world coords (metres) — for the verify() position check."""
    sh = geom.create_shape(_GEOM_WORLD, win)
    v = np.array(sh.geometry.verts, dtype=float).reshape(-1, 3)
    return v.min(0), v.max(0)


def _trailing_id(name):
    """Revit names end in ':<elementid>' — keep it as the stable handle, like Gal does."""
    if name and ":" in name:
        tail = name.rsplit(":", 1)[-1].strip()
        if tail:
            return tail
    return None


# Coarse window-style tokens, matched against the Revit family/type name. First match wins.
# This is COSMETIC — it only flavors the canonical Name and flags skylights; it never affects
# geometry — so a keyword scan is enough (replaces the old classify.py).
_STYLE_KEYWORDS = [
    ("skylight", "SKYLIGHT"), ("casement", "CASEMENT"), ("awning", "AWNING"),
    ("hopper", "HOPPER"), ("double-hung", "DOUBLEHUNG"), ("single-hung", "SINGLEHUNG"),
    ("hung", "HUNG"), ("slider", "SLIDER"), ("sliding", "SLIDER"),
    ("tilt", "TILTTURN"), ("pivot", "PIVOTH"), ("fixed", "FIXED"),
]


def _style_token(win, wt):
    """Best-effort style token from the window/type family name (cosmetic; '' = unknown)."""
    nm = " ".join(s for s in (win.Name, getattr(wt, "Name", None)) if s).lower()
    for kw, token in _STYLE_KEYWORDS:
        if kw in nm:
            return token
    return ""


def _orig_geom_kind(win):
    """A short label for the window's ORIGINAL representation, for the log (replaces the PIS)."""
    rep = win.Representation
    reps = rep.Representations if rep else None
    if not reps or not reps[0].Items:
        return "no-geom"
    it = reps[0].Items[0]
    if it.is_a("IfcMappedItem"):
        inner = it.MappingSource.MappedRepresentation
        kind = inner.Items[0].is_a() if inner.Items else "?"
        return f"mapped/{inner.RepresentationType}({kind})"
    return reps[0].RepresentationType or "?"


def _canonical_name(token, win):
    word = _OP_WORD.get(token, "Window")
    eid = _trailing_id(win.Name)
    if token == "SKYLIGHT":
        base = "Skylight"
    elif word == "Window":            # unknown style — avoid the doubled "Window window"
        base = "Window"
    else:
        base = f"{word} window"
    return f"{base}: {eid}" if eid else base


def _body_context(win):
    """Reuse the window's existing 'Body' representation context so we don't add a new one."""
    rep = win.Representation
    if rep:
        reps = rep.Representations or []
        for r in reps:
            if r.RepresentationIdentifier == "Body":
                return r.ContextOfItems
        if reps:
            return reps[0].ContextOfItems
    return None


def _already_converted(win):
    return (win.Description or "") == MARK


def _cleanup_old_rep(model, prod):
    """Remove the window's old per-instance representation entities once they're orphaned.
    De-references them from any IfcPresentationLayerAssignment first (so no shaperep is left
    empty), and NEVER touches a shared IfcRepresentationMap (other windows may still map it).
    """
    if prod is None:
        return
    try:
        for sr in list(prod.Representations or []):
            # Detach this per-window shaperep from any presentation layer that lists it,
            # else removing its items would leave the layer pointing at an empty rep.
            for inv in list(model.get_inverse(sr)):
                if inv.is_a("IfcPresentationLayerAssignment"):
                    kept = [x for x in inv.AssignedItems if x.id() != sr.id()]
                    if kept:
                        inv.AssignedItems = kept
                    else:
                        model.remove(inv)
            for it in list(sr.Items or []):             # mapped item is per-window; map is shared
                if len(model.get_inverse(it)) <= 1:
                    model.remove(it)
            model.remove(sr)                            # per-window, now unreferenced
        model.remove(prod)
    except Exception as e:                              # cleanup must never break a conversion
        print(f"     (old-rep cleanup skipped: {e!r})")


def reauthor_window(model, win, scale, style_code, predef):
    """Replace win's Representation with a clean parametric frame+pane sized from its own
    geometry. Preserves GlobalId + ObjectPlacement. Returns (mn, mx) local bbox or raises."""
    ctx = _body_context(win)
    mn, mx = _local_bbox(win, scale)                   # raises if geometry can't be read
    glass, frame_style = _harvest_styles(model, win)   # read BEFORE swapping the representation
    frame, pane = _build_window_solids(model, mn, mx, scale)
    _apply_styles(model, frame, pane, glass, frame_style)
    shape = model.create_entity("IfcShapeRepresentation", ContextOfItems=ctx,
                                RepresentationIdentifier="Body", RepresentationType="SweptSolid",
                                Items=[frame, pane])
    new_prod = model.create_entity("IfcProductDefinitionShape", Representations=[shape])
    old = win.Representation
    win.Representation = new_prod
    _cleanup_old_rep(model, old)

    win.Name = _canonical_name(style_code, win)
    win.Description = MARK
    if hasattr(win, "PredefinedType"):
        try:
            win.PredefinedType = predef
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

    wins = model.by_type("IfcWindow")
    print(f"[scan] {len(wins)} IfcWindow | unit scale {scale:.4f} m/unit | schema {model.schema}")
    stats = Counter()
    for w in wins:
        wt = ifc_element.get_type(w)
        if _already_converted(w):
            stats["already"] += 1
            continue
        token = _style_token(w, wt)                        # cosmetic style label ('' = unknown)
        # PredefinedType: SKYLIGHT vs WINDOW is the one semantic distinction (a roof window),
        # so the "skylight" keyword wins regardless of any other word in the name.
        predef = "SKYLIGHT" if token == "SKYLIGHT" else "WINDOW"
        try:
            # Non-rectangular windows (trapezoid / triangle / arch) would be flattened to a
            # rectangle by the neutral template — so preserve their original geometry instead.
            fill = _face_fill_ratio(w)
            if fill < FILL_MIN:
                stats["kept-nonrect"] += 1
                print(f"  [keep]    {w.Name!r:42}  non-rectangular (fill {fill:.2f}) → "
                      f"original geometry preserved (rebuild would flatten it)")
                continue
            kind = _orig_geom_kind(w)
            reauthor_window(model, w, scale, token, predef)
            stats["rebuilt"] += 1
            if token == "":
                stats["fallback-named"] += 1
            print(f"  [rebuilt] {w.Name!r:42}  (was {kind}, style={token or 'unknown'})")
        except Exception as e:
            stats["skipped"] += 1
            print(f"  [SKIP]    {w.Name!r:42}  geometry unreadable → left untouched: {e!r}")

    model.write(out_path)
    print(f"[write] {out_path}")
    print(f"[stats] rebuilt={stats['rebuilt']} kept-nonrect={stats['kept-nonrect']} "
          f"skipped={stats['skipped']} already={stats['already']} "
          f"generic-named={stats['fallback-named']}")
    verify(src_path, out_path)


# ══════════════════════════════════════════════════════════════════════════════
# Built-in verification (reopen src + out; assert "only the windows changed")
# ══════════════════════════════════════════════════════════════════════════════
# Element/relationship types that must be IDENTICAL in count before vs after.
_PRESERVE_TYPES = [
    "IfcWindow", "IfcWall", "IfcWallStandardCase", "IfcSlab", "IfcRoof", "IfcDoor",
    "IfcOpeningElement", "IfcSpace", "IfcBuildingStorey", "IfcCovering",
    "IfcFurnishingElement", "IfcBuildingElementProxy",
    "IfcRelFillsElement", "IfcRelVoidsElement", "IfcRelContainedInSpatialStructure",
    "IfcRelDefinesByType", "IfcRelAggregates",
]


def verify(src_path, out_path):
    print("[verify]")
    before = ifcopenshell.open(src_path)
    after = ifcopenshell.open(out_path)
    ok = True

    # 1. Non-window element/relationship preservation (counts unchanged).
    print("  preservation (before → after):")
    for t in _PRESERVE_TYPES:
        nb, na = len(before.by_type(t)), len(after.by_type(t))
        if nb == 0 and na == 0:
            continue
        good = nb == na
        ok &= good
        flag = "OK" if good else "CHANGED!"
        print(f"    {t:34s} {nb} → {na}  [{flag}]")

    # 2. Window GlobalIds preserved (none added/dropped/re-minted).
    gb = {w.GlobalId for w in before.by_type("IfcWindow")}
    ga = {w.GlobalId for w in after.by_type("IfcWindow")}
    good = gb == ga
    ok &= good
    print(f"  window GlobalIds preserved: {len(gb & ga)}/{len(gb)}  [{'OK' if good else 'CHANGED!'}]")

    # 3. Openings did not move (world placement identical) — windows stay located.
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

    # 4. Each window occupies the same world box as before (stays in its opening).
    bbox_b = {}
    for w in before.by_type("IfcWindow"):
        try:
            bbox_b[w.GlobalId] = _world_bbox(w)
        except Exception:
            pass
    max_drift = 0.0; checked = 0
    for w in after.by_type("IfcWindow"):
        b = bbox_b.get(w.GlobalId)
        if b is None:
            continue
        try:
            mn_a, mx_a = _world_bbox(w)
        except Exception:
            continue
        drift = float(max(np.abs(np.r_[mn_a - b[0], mx_a - b[1]])))
        max_drift = max(max_drift, drift); checked += 1
    good = max_drift <= BBOX_TOL_M
    ok &= good
    print(f"  window world-bbox drift (n={checked}): max {max_drift * 1000:.1f} mm  "
          f"[{'OK' if good else f'> {BBOX_TOL_M*1000:.0f} mm!'}]")

    # 4b. Every rebuilt window's new items carry surface styles (else they render gray).
    styled_ids = {s.Item.id() for s in after.by_type("IfcStyledItem") if s.Item}
    unstyled = 0
    for w in after.by_type("IfcWindow"):
        if (w.Description or "") != MARK or not w.Representation:
            continue
        items = [it for r in w.Representation.Representations for it in (r.Items or [])]
        if items and not all(it.id() in styled_ids for it in items):
            unstyled += 1
    ok &= unstyled == 0
    print(f"  rebuilt windows missing surface styles: {unstyled}  "
          f"[{'OK — all styled' if unstyled == 0 else 'WOULD RENDER GRAY!'}]")

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
