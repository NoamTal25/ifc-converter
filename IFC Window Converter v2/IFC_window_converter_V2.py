#!/usr/bin/env python3
"""
IFC_window_converter_V2.py — FormX IFC pipeline stage: WINDOWS (golden-template-swap).

The go-forward method (CLAUDE.md §2a): each baked ``IfcWindow`` is **classified** into one of
FormX's defined window types (PDF "IFC Standardizer: Template Gallery categorizing"), then
rebuilt as that type's **golden parametric template** — sized from the window's own measured
extents and authored straight into the target file's schema + units. Everything that is NOT a
window is left exactly as-is.

How this differs from v1 (which authored one neutral hollow-frame+pane for every window):
  * CLASSIFY → the type drives the geometry. SINGLE_PANEL (fixed/casement/awning/slider) → one
    pane; DOUBLE_HORIZONTAL → vertical mullion + 2 panes; DOUBLE_VERTICAL / double-hung →
    horizontal transom + 2 panes. (``classify_window.py``)
  * Geometry comes from the SHARED recipe ``golden_geometry.py`` — the *same code* that authors
    the reviewable golden templates (``generate_goldens.py``), so a converted window matches its
    golden, scaled.
  * The FormX param apparatus is authored back (the promoted "golden spec"): IfcWindowType /
    IfcWindowStyle + lining + per-panel properties + Pset_WindowCommon (Overall/Rough W·H, Depth,
    PanelType, HandFlipped/FacingFlipped, Split). Schema differences live in ``schema_adapter.py``.

Preserved verbatim from v1: edit-a-copy, measure-the-element's-own-bbox, preserve
GlobalId/ObjectPlacement + the opening→fill→void→host chain + spatial containment, carry surface
styles forward, gate non-rectangular / unreadable windows, built-in verify(). The **Body**
representation is swapped in place (matched by ``.id()`` — §6 gotcha); **FootPrint is preserved**.

Output is written with the suffix "-WIN2" before the extension. Self-contained (ifcopenshell only).

Usage:
    python3.11 IFC_window_converter_V2.py                  # batch INPUT_IFC_FILES_HERE → OUTPUT…
    python3.11 IFC_window_converter_V2.py <in.ifc>         # → OUTPUT_IFC_FILES_HERE/<in>-WIN2.ifc
    python3.11 IFC_window_converter_V2.py <in.ifc> <out.ifc>
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
import golden_geometry as gg
import schema_adapter as sa
from classify_window import classify

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════
SUFFIX = "-WIN2"
INPUT_DIR = _ROOT / "INPUT_IFC_FILES_HERE"
OUTPUT_DIR = _ROOT / "OUTPUT_IFC_FILES_HERE"

# Frame proportions in METRES, converted to each file's units at runtime (files may be ft/mm/m).
FRAME_THK_M = 0.05      # lining face width (50 mm)
GLAZE_THK_M = 0.020     # glazing-unit thickness (20 mm)
BAR_THK_M   = 0.05      # mullion / transom thickness (50 mm)

MARK = "FormX-WIN2 parametric window"   # stamped on Description for idempotency
BBOX_TOL_M = 0.02       # window world-bbox drift tolerance in verify (m)
FILL_MIN = 0.95         # face-fill ratio below which a window is NOT rectangular → preserve

_GEOM_LOCAL = geom.settings(); _GEOM_LOCAL.set("use-world-coords", False)
_GEOM_WORLD = geom.settings(); _GEOM_WORLD.set("use-world-coords", True)


# ══════════════════════════════════════════════════════════════════════════════
# Geometry measurement (the element's OWN local frame; gates)
# ══════════════════════════════════════════════════════════════════════════════
def _local_verts(win, scale):
    """Window vertices in its OWN local frame, file units. geom returns metres → /scale."""
    sh = geom.create_shape(_GEOM_LOCAL, win)
    v = np.array(sh.geometry.verts, dtype=float).reshape(-1, 3) / scale
    if v.size == 0:
        raise ValueError("empty geometry")
    return v


def _axes_from_bbox(verts, win):
    """Return (center, ext, depth_axis, width_axis, height_axis, dirs) for the local bbox.

    depth = thinnest axis (through-wall). Of the two face axes, the one whose world direction is
    most vertical (largest |world-Z|) is height; the other is width — so a mullion/transom is
    oriented correctly regardless of how the element is rotated."""
    mn, mx = verts.min(0), verts.max(0)
    ext = mx - mn
    center = (mn + mx) / 2.0
    depth = int(np.argmin(ext))
    face = [i for i in range(3) if i != depth]
    # world direction of each local axis = columns of the placement rotation
    M = ifc_placement.get_local_placement(win.ObjectPlacement)
    world_z = [abs(float(M[2, i])) for i in range(3)]
    height = max(face, key=lambda i: world_z[i])
    width = face[0] if face[1] == height else face[1]

    def unit(i):
        e = [0.0, 0.0, 0.0]; e[i] = 1.0; return tuple(e)
    return center, ext, depth, width, height, (unit(depth), unit(width), unit(height))


def _hull_area_2d(pts):
    """Convex-hull area of 2D points (Andrew's monotone chain; no scipy)."""
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


def _face_fill_ratio(verts):
    """How well the face silhouette fills its bounding rect (~1.0 rectangle; <0.95 ⇒ not)."""
    ext = verts.max(0) - verts.min(0)
    d = int(np.argmin(ext)); u, w = [i for i in range(3) if i != d]
    bbox = ext[u] * ext[w]
    if bbox <= 0:
        return 1.0
    return _hull_area_2d(verts[:, [u, w]]) / bbox


def _world_bbox(win):
    sh = geom.create_shape(_GEOM_WORLD, win)
    v = np.array(sh.geometry.verts, dtype=float).reshape(-1, 3)
    return v.min(0), v.max(0)


# ══════════════════════════════════════════════════════════════════════════════
# Surface styles (harvest the originals; glass = transparent, frame = opaque)
# ══════════════════════════════════════════════════════════════════════════════
def _max_transparency(styles):
    t = None
    for ss in sa.iter_surface_styles(styles):
        for r in (ss.Styles or []):
            if r.is_a("IfcSurfaceStyleRendering"):
                tr = r.Transparency if r.Transparency is not None else 0.0
                t = tr if t is None else max(t, tr)
    return t


def _harvest_styles(model, win):
    """Return (glass_styles, frame_styles) reusing the window's own IfcSurfaceStyle entities,
    bucketed by transparency. Read BEFORE the Body is swapped. Either may be None."""
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
        if t is not None and t > 0:
            if t >= glass_t:
                glass_t, glass = t, styled.Styles
        elif frame is None:
            frame = styled.Styles
    return glass, frame


def _apply_styles(model, items, glass, frame):
    """Attach styles to the role-tagged (solid, role) items; glass on panes, frame on lining+bars."""
    if glass is None and frame is None:
        glass, frame = sa.build_default_styles(model)
    glass = glass or frame
    frame = frame or glass
    for solid, role in items:
        model.create_entity("IfcStyledItem", Item=solid,
                            Styles=(glass if role == "pane" else frame))


# ══════════════════════════════════════════════════════════════════════════════
# Representation swap (Body only; preserve FootPrint) + old-rep cleanup
# ══════════════════════════════════════════════════════════════════════════════
def _body_shaperep(win):
    """The window's 'Body' IfcShapeRepresentation, or None."""
    rep = win.Representation
    if not rep:
        return None
    for r in (rep.Representations or []):
        if r.RepresentationIdentifier == "Body":
            return r
    return None


def _cleanup_old_shaperep(model, sr):
    """Remove an orphaned per-window Body shaperep + its per-window items. De-references any
    IfcPresentationLayerAssignment first; NEVER removes a shared IfcRepresentationMap."""
    try:
        for inv in list(model.get_inverse(sr)):
            if inv.is_a("IfcPresentationLayerAssignment"):
                kept = [x for x in inv.AssignedItems if x.id() != sr.id()]
                if kept:
                    inv.AssignedItems = kept
                else:
                    model.remove(inv)
        for it in list(sr.Items or []):
            if len(model.get_inverse(it)) <= 1:      # per-window mapped item / solid
                model.remove(it)
        model.remove(sr)
    except Exception as e:
        print(f"     (old-rep cleanup skipped: {e!r})")


def _swap_body(model, win, new_items, ctx):
    """Replace only the Body shaperep with one holding `new_items`, preserving FootPrint &c.
    Matches the old Body by `.id()` (ifcopenshell returns fresh wrappers — `is` never matches)."""
    prod = win.Representation
    old_body = _body_shaperep(win)
    new_body = model.create_entity(
        "IfcShapeRepresentation", ContextOfItems=ctx,
        RepresentationIdentifier="Body", RepresentationType="SweptSolid",
        Items=[s for s, _ in new_items])
    if old_body is not None:
        prod.Representations = tuple(new_body if r.id() == old_body.id() else r
                                     for r in prod.Representations)
        _cleanup_old_shaperep(model, old_body)
    else:
        prod.Representations = tuple(prod.Representations) + (new_body,)


# ══════════════════════════════════════════════════════════════════════════════
# Re-author one window
# ══════════════════════════════════════════════════════════════════════════════
def _ctx_for_body(win):
    body = _body_shaperep(win)
    if body is not None:
        return body.ContextOfItems
    rep = win.Representation
    return rep.Representations[0].ContextOfItems if rep and rep.Representations else None


def reauthor_window(model, win, scale, recipe, owner):
    """Rebuild win as its golden template, sized to its measured extents. Returns the local ext."""
    verts = _local_verts(win, scale)
    center, ext, di, wi, hi, dirs = _axes_from_bbox(verts, win)
    depth_dir, width_dir, _ = dirs
    width, height, depth = float(ext[wi]), float(ext[hi]), float(ext[di])

    glass, frame_style = _harvest_styles(model, win)        # read BEFORE swapping the Body

    items = gg.build_window_items(
        model, width, height, depth,
        frame_thk=FRAME_THK_M / scale, glaze_thk=GLAZE_THK_M / scale,
        bar_thk=BAR_THK_M / scale, split=recipe["split"],
        center=tuple(center), depth_dir=depth_dir, width_dir=width_dir)

    _apply_styles(model, items, glass, frame_style)
    _swap_body(model, win, items, _ctx_for_body(win))

    _author_formx_apparatus(model, win, recipe, owner, scale, width, height, depth)

    win.Name = recipe["name"]
    win.Description = MARK
    sa.set_window_semantics(win, recipe.get("predef"), recipe.get("part_type"))
    return ext


def _author_formx_apparatus(model, win, recipe, owner, scale, width, height, depth):
    """Author the FormX param apparatus at the OCCURRENCE level (never a second IfcWindowType —
    these windows are already typed by Revit, and IfcRelDefinesByType is [0:1]):
      * IfcWindowLiningProperties + IfcWindowPanelProperties (the parametric window detail), and
      * Pset_WindowCommon (the PDF param contract: Overall/Rough W·H, Depth, PanelType, Split,
        HandFlipped/FacingFlipped),
    each linked via IfcRelDefinesByProperties (occurrence-level property definitions are
    many-per-element, so no collision with the preserved type relationship)."""
    frame_thk = FRAME_THK_M / scale
    bar_thk = BAR_THK_M / scale
    lining = sa.make_lining_props(
        model, owner, lining_depth=depth, lining_thk=frame_thk,
        split=recipe["split"], bar_thk=bar_thk, width=width, height=height)
    panels = sa.make_panel_props(model, owner, recipe["panels"])
    for pdef in [lining] + panels:
        if pdef is not None:
            sa.relate_propertyset(model, owner, pdef, win)

    rough = 2 * frame_thk                    # simple rough-opening margin (+1 lining each side)
    props = [
        sa.psv(model, "Reference",     recipe["formx_type"], "IfcIdentifier"),
        sa.psv(model, "OverallWidth",  width,                "IfcPositiveLengthMeasure"),
        sa.psv(model, "OverallHeight", height,               "IfcPositiveLengthMeasure"),
        sa.psv(model, "Depth",         depth,                "IfcPositiveLengthMeasure"),
        sa.psv(model, "RoughWidth",    width + rough,        "IfcPositiveLengthMeasure"),
        sa.psv(model, "RoughHeight",   height + rough,       "IfcPositiveLengthMeasure"),
        sa.psv(model, "HandFlipped",   False,                "IfcBoolean"),
        sa.psv(model, "FacingFlipped", False,                "IfcBoolean"),
    ]
    if recipe["split"] == "V":
        props.append(sa.psv(model, "SplitWidth",
                            (width - 2 * frame_thk - bar_thk) / 2.0, "IfcPositiveLengthMeasure"))
    elif recipe["split"] == "H":
        props.append(sa.psv(model, "SplitHeight",
                            (height - 2 * frame_thk - bar_thk) / 2.0, "IfcPositiveLengthMeasure"))
    for k, v in recipe.get("pset_panel", {}).items():
        props.append(sa.psv(model, k, v, "IfcLabel"))
    sa.add_pset(model, owner, "Pset_WindowCommon", props, win)


def _already_converted(win):
    return (win.Description or "") == MARK


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
    owner = next(iter(model.by_type("IfcOwnerHistory")), None)

    wins = model.by_type("IfcWindow")
    print(f"[scan] {len(wins)} IfcWindow | unit scale {scale:.4f} m/unit | schema {model.schema}")
    stats = Counter()
    for w in wins:
        wt = ifc_element.get_type(w)
        if _already_converted(w):
            stats["already"] += 1
            continue
        recipe = classify(w, wt)
        nm = (w.Name or "?")
        if recipe.get("gate"):
            stats["gated"] += 1
            print(f"  [keep]    {nm[:46]:46}  GATE: {recipe['reason']}")
            continue
        try:
            verts = _local_verts(w, scale)
            fill = _face_fill_ratio(verts)
            if fill < FILL_MIN:
                stats["kept-nonrect"] += 1
                print(f"  [keep]    {nm[:46]:46}  non-rectangular (fill {fill:.2f}) → preserved")
                continue
            if _body_shaperep(w) is None:
                stats["kept-nobody"] += 1
                print(f"  [keep]    {nm[:46]:46}  no Body representation → preserved")
                continue
            reauthor_window(model, w, scale, recipe, owner)
            stats["rebuilt"] += 1
            print(f"  [rebuilt] {nm[:46]:46}  → {recipe['formx_type']} ({recipe['reason']})")
        except Exception as e:
            stats["skipped"] += 1
            print(f"  [SKIP]    {nm[:46]:46}  geometry unreadable → left untouched: {e!r}")

    model.write(out_path)
    print(f"[write] {out_path}")
    print(f"[stats] rebuilt={stats['rebuilt']} gated={stats['gated']} "
          f"kept-nonrect={stats['kept-nonrect']} kept-nobody={stats['kept-nobody']} "
          f"skipped={stats['skipped']} already={stats['already']}")
    verify(src_path, out_path)


# ══════════════════════════════════════════════════════════════════════════════
# Built-in verification (reopen src + out; assert "only the windows changed")
# ══════════════════════════════════════════════════════════════════════════════
_PRESERVE_TYPES = [
    "IfcWindow", "IfcWall", "IfcWallStandardCase", "IfcSlab", "IfcRoof", "IfcDoor",
    "IfcOpeningElement", "IfcSpace", "IfcBuildingStorey", "IfcCovering",
    "IfcFurnishingElement", "IfcBuildingElementProxy",
    "IfcRelFillsElement", "IfcRelVoidsElement", "IfcRelContainedInSpatialStructure",
    "IfcRelAggregates",
]


def verify(src_path, out_path):
    print("[verify]")
    before = ifcopenshell.open(src_path)
    after = ifcopenshell.open(out_path)
    ok = True

    print("  preservation (before → after):")
    for t in _PRESERVE_TYPES:
        nb, na = len(before.by_type(t)), len(after.by_type(t))
        if nb == 0 and na == 0:
            continue
        good = nb == na
        ok &= good
        print(f"    {t:34s} {nb} → {na}  [{'OK' if good else 'CHANGED!'}]")

    gb = {w.GlobalId for w in before.by_type("IfcWindow")}
    ga = {w.GlobalId for w in after.by_type("IfcWindow")}
    good = gb == ga; ok &= good
    print(f"  window GlobalIds preserved: {len(gb & ga)}/{len(gb)}  [{'OK' if good else 'CHANGED!'}]")

    # fill/void edges identical (windows stay in their openings)
    eb = sum(len(before.by_type(t)) for t in ("IfcRelFillsElement", "IfcRelVoidsElement"))
    ea = sum(len(after.by_type(t)) for t in ("IfcRelFillsElement", "IfcRelVoidsElement"))
    good = eb == ea; ok &= good
    print(f"  fill/void relationship edges: {eb} → {ea}  [{'OK' if good else 'CHANGED!'}]")

    # openings unmoved
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

    # each rebuilt window keeps its placement exactly (kernel-free; stays in its opening)
    bpl = {w.GlobalId: ifc_placement.get_local_placement(w.ObjectPlacement)
           for w in before.by_type("IfcWindow") if w.ObjectPlacement}
    bad = 0
    for w in after.by_type("IfcWindow"):
        if (w.Description or "") != MARK:
            continue
        m0 = bpl.get(w.GlobalId)
        if m0 is None or w.ObjectPlacement is None or \
           not np.allclose(m0, ifc_placement.get_local_placement(w.ObjectPlacement), atol=1e-9):
            bad += 1
    ok &= bad == 0
    print(f"  rebuilt windows keep placement: {'OK' if bad == 0 else f'{bad} drifted!'}")

    # rebuilt windows occupy the same world box on the FACE PLANE (depth envelope may shift
    # by the glaze inset; measure drift on the two largest-extent axes only)
    bbox_b = {}
    for w in before.by_type("IfcWindow"):
        try:
            bbox_b[w.GlobalId] = _world_bbox(w)
        except Exception:
            pass
    max_drift = 0.0; checked = 0
    for w in after.by_type("IfcWindow"):
        if (w.Description or "") != MARK:
            continue
        b = bbox_b.get(w.GlobalId)
        if b is None:
            continue
        try:
            mn_a, mx_a = _world_bbox(w)
        except Exception:
            continue
        size = (b[1] - b[0])
        face = np.argsort(size)[1:]          # two largest-extent world axes
        drift = float(max(np.abs(np.r_[(mn_a - b[0])[face], (mx_a - b[1])[face]])))
        max_drift = max(max_drift, drift); checked += 1
    good = max_drift <= BBOX_TOL_M; ok &= good
    print(f"  rebuilt window face-bbox drift (n={checked}): max {max_drift * 1000:.1f} mm  "
          f"[{'OK' if good else f'> {BBOX_TOL_M*1000:.0f} mm!'}]")

    # every rebuilt window's new items carry surface styles
    styled_ids = {s.Item.id() for s in after.by_type("IfcStyledItem") if s.Item}
    unstyled = 0
    for w in after.by_type("IfcWindow"):
        if (w.Description or "") != MARK:
            continue
        body = _body_shaperep(w)
        items = list(body.Items or []) if body else []
        if items and not all(it.id() in styled_ids for it in items):
            unstyled += 1
    ok &= unstyled == 0
    print(f"  rebuilt windows missing styles: {unstyled}  "
          f"[{'OK — all styled' if unstyled == 0 else 'WOULD RENDER GRAY!'}]")

    # schema-validity gate — must not INTRODUCE errors
    try:
        import ifcopenshell.validate as V
        lb = V.json_logger(); V.validate(before, lb)
        la = V.json_logger(); V.validate(after, la)
        nb, na = len(lb.statements), len(la.statements)
        good = na <= nb; ok &= good
        pre = f" ({nb} pre-existing in source)" if nb else ""
        print(f"  ifcopenshell.validate: source {nb} → output {na}{pre}  "
              f"[{'OK' if good else 'NEW ERRORS INTRODUCED!'}]")
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
