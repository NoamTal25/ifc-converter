#!/usr/bin/env python3
"""
IFC_door_converter_V2.py — FormX IFC pipeline stage: DOORS (golden-template-swap).

The go-forward method (CLAUDE.md §2a): each baked ``IfcDoor`` is **classified** into one of
FormX's 16 defined door types (PDF "IFC Standardizer: Template Gallery categorizing"), then rebuilt
as that type's **golden parametric template** — sized from the door's own measured extents,
coloured from the door's own harvested surface styles, and authored straight into the target file's
schema + units. Everything that is NOT a door is left exactly as-is.

Mirrors the proven window converter v2 module-for-module:
  * CLASSIFY (``classify_door.py``) → the FormX type drives the geometry recipe (panels /
    mullions / head-rail / barn-track / handle), the operation enum, and the panel properties.
  * Geometry comes from the SHARED recipe ``golden_door_geometry.py`` — the *same code* that authors
    the reviewable golden templates (``generate_goldens.py``) — so a converted door matches its
    golden, scaled. ``door_types.py`` is the single source of truth both consume.
  * The FormX param apparatus is authored at the OCCURRENCE level: Pset_DoorCommon
    (Overall/Rough W·H, Depth) + Pset FormX_Door_Window (HandFlipped/FacingFlipped) +
    IfcDoorLiningProperties + per-panel IfcDoorPanelProperties — via IfcRelDefinesByProperties.
    NEVER a second IfcDoorType (the doors are already Revit-typed; IfcRelDefinesByType is [0:1]).
    Schema differences (IFC2X3 IfcDoorStyle vs IFC4 IfcDoorType, style wrapping, semantics
    availability) live in ``schema_adapter.py``.

Preserved verbatim from the window converter: edit-a-copy, measure-the-element's-own-bbox, preserve
GlobalId/ObjectPlacement + the opening→fill→void→host chain + spatial containment, carry surface
styles forward, gate non-rectangular / unreadable doors, built-in verify(). The **Body**
representation is swapped in place (matched by ``.id()`` — §6 gotcha); **FootPrint is preserved**.

Door-specific deltas:
  * FOLDING-DEPTH CLAMP — bi-fold / folding-combo doors export partly folded, so their measured
    through-wall depth is the folded *projection*, not the leaf thickness (§6). Depth is clamped for
    folding types so we don't author a ~1.5-ft-thick door.
  * Verify/test bbox drift is measured on the FACE PLANE (two largest axes), since proud handles /
    the depth clamp deliberately change the through-wall envelope.

Output is written with the suffix "-D2" before the extension. Self-contained (ifcopenshell only).

Usage:
    python3.11 IFC_door_converter_V2.py                  # batch INPUT_IFC_FILES_HERE → OUTPUT…
    python3.11 IFC_door_converter_V2.py <in.ifc>         # → OUTPUT_IFC_FILES_HERE/<in>-D2.ifc
    python3.11 IFC_door_converter_V2.py <in.ifc> <out.ifc>
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
import golden_door_geometry as gg
import schema_adapter as sa
import door_types
from classify_door import classify

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════
SUFFIX = "-D2"
INPUT_DIR = _ROOT / "INPUT_IFC_FILES_HERE"
OUTPUT_DIR = _ROOT / "OUTPUT_IFC_FILES_HERE"

MARK = "FormX-D2 parametric door"   # stamped on Description for idempotency
BBOX_TOL_M = 0.02       # door world-bbox drift tolerance in verify (m), measured on the face plane
FILL_MIN = 0.95         # face-fill ratio below which a door is NOT rectangular → preserve
MAX_FOLD_DEPTH_M = 0.20 # folding doors export partly folded → clamp through-wall depth to this (m)

_GEOM_LOCAL = geom.settings(); _GEOM_LOCAL.set("use-world-coords", False)
_GEOM_WORLD = geom.settings(); _GEOM_WORLD.set("use-world-coords", True)


# ══════════════════════════════════════════════════════════════════════════════
# Geometry measurement (the element's OWN local frame; gates)
# ══════════════════════════════════════════════════════════════════════════════
def _local_verts(door, scale):
    """Door vertices in its OWN local frame, file units. geom returns metres → /scale."""
    sh = geom.create_shape(_GEOM_LOCAL, door)
    v = np.array(sh.geometry.verts, dtype=float).reshape(-1, 3) / scale
    if v.size == 0:
        raise ValueError("empty geometry")
    return v


def _axes_from_bbox(door, verts):
    """Return (center, ext, depth, width, height, dirs) for the local bbox.

    depth = thinnest axis (through-wall). Of the two face axes, the one whose world direction is
    most vertical (largest |world-Z|) is height; the other is width — so mullions / rails / the
    barn track orient correctly regardless of how the door is rotated."""
    mn, mx = verts.min(0), verts.max(0)
    ext = mx - mn
    center = (mn + mx) / 2.0
    depth = int(np.argmin(ext))
    face = [i for i in range(3) if i != depth]
    M = ifc_placement.get_local_placement(door.ObjectPlacement)
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


def _world_bbox(door):
    sh = geom.create_shape(_GEOM_WORLD, door)
    v = np.array(sh.geometry.verts, dtype=float).reshape(-1, 3)
    return v.min(0), v.max(0)


# ══════════════════════════════════════════════════════════════════════════════
# Surface styles (harvest the door's OWN styles; transparent = glass, opaque = everything else)
# ══════════════════════════════════════════════════════════════════════════════
def _max_transparency(styles):
    t = None
    for ss in sa.iter_surface_styles(styles):
        for r in (ss.Styles or []):
            if r.is_a("IfcSurfaceStyleRendering"):
                tr = r.Transparency if r.Transparency is not None else 0.0
                t = tr if t is None else max(t, tr)
    return t


def _harvest_styles(model, door):
    """Return (glass_styles, opaque_styles) reusing the door's OWN IfcSurfaceStyle entities,
    bucketed by transparency. Read BEFORE the Body is swapped. Either may be None.

    This is how a converted door 'takes on the values of the baked door': its real colours /
    transparency are carried straight onto the clean parametric parts."""
    rep = door.Representation
    if not rep or not rep.Representations:
        return None, None
    items = set()
    for sr in rep.Representations:
        for it in (sr.Items or []):
            if it.is_a("IfcMappedItem"):
                items.update(it.MappingSource.MappedRepresentation.Items or [])
            else:
                items.add(it)
    glass = opaque = None
    glass_t = 0.0
    for styled in model.by_type("IfcStyledItem"):
        if styled.Item not in items:
            continue
        t = _max_transparency(styled.Styles)
        if t is not None and t > 0:
            if t >= glass_t:
                glass_t, glass = t, styled.Styles
        elif opaque is None:
            opaque = styled.Styles
    return glass, opaque


def _apply_styles(model, items, glass, opaque):
    """Attach styles to the role-tagged (solid, role) items. A panel gets the harvested glass
    style when the door actually had glass; everything else (frame / mullion / rail / track /
    roller / handle) and opaque-leaf panels get the opaque style.

    If the door donated ONLY a transparent style (no opaque bucket), synthesize a default opaque
    style for the non-panel parts — otherwise an all-glass door family would paint its frame /
    mullion / handle see-through too."""
    if opaque is None:
        opaque = sa.build_default_styles(model)[1]      # default opaque 'Door' style
    if glass is None:
        glass = opaque
    # Glass-eligible roles (panel / glass / lite) take the harvested glass style when the door
    # actually had glass; every other role (frame / stile / rail / muntin / plank / hardware …) is
    # opaque. Role→bucket lives in golden_door_geometry (single source of truth).
    for solid, role in items:
        use_glass = (role in gg.GLASS_ROLES and glass is not opaque)
        model.create_entity("IfcStyledItem", Item=solid, Styles=(glass if use_glass else opaque))


# ══════════════════════════════════════════════════════════════════════════════
# Representation swap (Body only; preserve FootPrint) + old-rep cleanup
# ══════════════════════════════════════════════════════════════════════════════
def _body_shaperep(door):
    """The door's 'Body' IfcShapeRepresentation, or None."""
    rep = door.Representation
    if not rep:
        return None
    for r in (rep.Representations or []):
        if r.RepresentationIdentifier == "Body":
            return r
    return None


def _cleanup_old_shaperep(model, sr):
    """Remove an orphaned per-door Body shaperep + its per-door items. De-references any
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
            if len(model.get_inverse(it)) <= 1:      # per-door mapped item / solid
                model.remove(it)
        model.remove(sr)
    except Exception as e:
        print(f"     (old-rep cleanup skipped: {e!r})")


def _swap_body(model, door, new_items, ctx):
    """Replace only the Body shaperep with one holding `new_items`, preserving FootPrint &c.
    Matches the old Body by `.id()` (ifcopenshell returns fresh wrappers — `is` never matches)."""
    prod = door.Representation
    old_body = _body_shaperep(door)
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


def _ctx_for_body(door):
    body = _body_shaperep(door)
    if body is not None:
        return body.ContextOfItems
    rep = door.Representation
    return rep.Representations[0].ContextOfItems if rep and rep.Representations else None


# ══════════════════════════════════════════════════════════════════════════════
# Re-author one door
# ══════════════════════════════════════════════════════════════════════════════
def reauthor_door(model, door, scale, recipe, owner):
    """Rebuild door as its golden template, sized to its measured extents + coloured from its own
    styles. Returns the local ext."""
    verts = _local_verts(door, scale)
    center, ext, di, wi, hi, dirs = _axes_from_bbox(door, verts)
    depth_dir, width_dir, _ = dirs
    width, height, depth = float(ext[wi]), float(ext[hi]), float(ext[di])

    # capture the source occurrence operation BEFORE re-authoring — it carries real Revit
    # handedness we must preserve rather than overwrite with the class-canonical value.
    existing_op = getattr(door, "OperationType", None)

    # human-readable "which way does it open?" — derived from the SAME effective operation
    # set_door_semantics will keep (preserved Revit handedness, else class-canonical), so the
    # OpeningDirection label never contradicts the door's final OperationType.
    if existing_op not in (None, "NOTDEFINED"):
        eff_op, eff_user = existing_op, None
    else:
        eff_op, eff_user = recipe.get("operation"), recipe.get("user_op")
    opening_dir = _opening_direction(eff_op, eff_user, facing_flipped=False)

    # FOLDING-DEPTH CLAMP (§6): a bi-fold door's bbox depth is the folded projection, not the leaf.
    if recipe.get("folding"):
        depth = min(depth, MAX_FOLD_DEPTH_M / scale)

    glass, opaque = _harvest_styles(model, door)            # read BEFORE swapping the Body
    dims = gg.dims_in_units(scale)

    # Which world direction is "up" for the handle? The build's vertical axis is profile+Y =
    # depth_dir × width_dir (in the door's local frame); map it through the placement and read its
    # world-Z. Revit exports doors with the local height axis pointing up OR down, so without this
    # the handle would land near the top on the down-pointing ones (CLAUDE.md / handle-height fix).
    M = ifc_placement.get_local_placement(door.ObjectPlacement)
    profile_y = np.cross(np.array(depth_dir, float), np.array(width_dir, float))
    up_sign = 1.0 if float((M[:3, :3] @ profile_y)[2]) >= 0 else -1.0

    items = gg.build_door_items(
        model, width, height, depth, recipe=recipe["recipe"], dims=dims,
        center=tuple(center), depth_dir=depth_dir, width_dir=width_dir, up_sign=up_sign)

    _apply_styles(model, items, glass, opaque)
    _swap_body(model, door, items, _ctx_for_body(door))

    _author_formx_apparatus(model, door, recipe, owner, dims, width, height, depth, opening_dir)

    door.Name = recipe["name"]
    door.Description = MARK
    if hasattr(door, "OverallWidth"):
        door.OverallWidth = float(width)
    if hasattr(door, "OverallHeight"):
        door.OverallHeight = float(height)
    sa.set_door_semantics(door, "DOOR", recipe.get("operation"), recipe.get("user_op"),
                          existing_op=existing_op)
    return ext


def _opening_direction(operation, user_op, facing_flipped):
    """Human-readable "which way does it open?" — a hinge-side + swing-sense PAIR, derived from the
    door's effective IfcDoorTypeOperationEnum (cheapest reliable signal) plus FacingFlipped.

      * Swing doors → "<Hand> / <Sense>"  e.g. "Right / Outward", "Left / Inward".
      * Sliding/folding → "<Motion> <Hand>"  e.g. "Sliding Left", "Folding Right".
      * Revolving / rolling-up / userdefined combos / undefined → a safe descriptive token.

    Hand comes from the …_LEFT/…_RIGHT suffix (incl. OPPOSITE_* and SLIDING_/FOLDING_TO_* variants).
    Swing sense is a heuristic on FacingFlipped (False ⇒ Inward, the default Revit facing); refine
    when FacingFlipped is derived for real. Best-effort: always returns a non-empty label."""
    op = (operation or "").upper()
    hand = "Left" if "LEFT" in op else "Right" if "RIGHT" in op else None
    if "SLIDING" in op:
        return f"Sliding {hand}" if hand else "Sliding"
    if "FOLDING" in op:
        return f"Folding {hand}" if hand else "Folding"
    if "REVOLVING" in op:
        return "Revolving"
    if "ROLLINGUP" in op:
        return "Rolling Up"
    if op == "USERDEFINED":
        base = (user_op or "Combo").replace("_", " ").title()
        return f"{base} {hand}" if hand else base
    if op in ("", "NOTDEFINED"):
        return "Unspecified"
    swing = "Outward" if facing_flipped else "Inward"   # SINGLE_/DOUBLE_SWING_*, SWING_FIXED_*, OPPOSITE_*
    return f"{hand} / {swing}" if hand else f"Swing / {swing}"


def _author_formx_apparatus(model, door, recipe, owner, dims, width, height, depth, opening_dir):
    """Author the FormX param apparatus at the OCCURRENCE level (never a second IfcDoorType):
      * IfcDoorLiningProperties + IfcDoorPanelProperties (the parametric door detail), and
      * Pset_DoorCommon (Overall/Rough W·H, Depth, Reference, IsExternal) + Pset FormX_Door_Window
        (HandFlipped / FacingFlipped / OpeningDirection — the human-readable hinge+swing label),
    each linked via IfcRelDefinesByProperties (occurrence-level definitions are many-per-element,
    so no collision with the preserved type relationship).

    The lining/panel property entities are authored VALUE-LESS (no dimensional fields), matching the
    flush FormX-native reference: the FormX param contract rides on Pset_DoorCommon +
    IfcDoor.OverallWidth/Height, and Gaudi draws nothing from value-less props. (The pane↔frame
    "space" was the hollow-profile lining, since replaced by 4 solid bars — CLAUDE.md §6.)"""
    frame_thk = dims["frame_thk"]

    lining = sa.make_lining_props(model, owner)
    panel_props = sa.make_panel_props(model, owner, recipe["panel_props"])
    for pdef in ([lining] + panel_props):
        if pdef is not None:
            sa.relate_propertyset(model, owner, pdef, door)

    is_external = door_types.TYPES[recipe["formx_type"]]["is_external"]
    props = [
        sa.psv(model, "Reference",     recipe["formx_type"], "IfcIdentifier"),
        sa.psv(model, "IsExternal",    bool(is_external),    "IfcBoolean"),
        sa.psv(model, "OverallWidth",  width,                "IfcPositiveLengthMeasure"),
        sa.psv(model, "OverallHeight", height,               "IfcPositiveLengthMeasure"),
        sa.psv(model, "Depth",         depth,                "IfcPositiveLengthMeasure"),
        sa.psv(model, "RoughWidth",    width + 2 * frame_thk, "IfcPositiveLengthMeasure"),
        sa.psv(model, "RoughHeight",   height + frame_thk,    "IfcPositiveLengthMeasure"),
    ]
    sa.add_pset(model, owner, "Pset_DoorCommon", props, door)
    sa.add_pset(model, owner, "FormX_Door_Window", [
        sa.psv(model, "HandFlipped",      False,       "IfcBoolean"),
        sa.psv(model, "FacingFlipped",    False,       "IfcBoolean"),
        sa.psv(model, "OpeningDirection", opening_dir, "IfcLabel"),
    ], door)


def _already_converted(door):
    return (door.Description or "") == MARK


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

    doors = model.by_type("IfcDoor")
    print(f"[scan] {len(doors)} IfcDoor | unit scale {scale:.4f} m/unit | schema {model.schema}")
    stats = Counter()
    for d in doors:
        dt = ifc_element.get_type(d)
        if _already_converted(d):
            stats["already"] += 1
            continue
        recipe = classify(d, dt)
        nm = (d.Name or "?")
        if recipe.get("gate"):
            stats["gated"] += 1
            print(f"  [keep]    {nm[:46]:46}  GATE: {recipe['reason']}")
            continue
        try:
            verts = _local_verts(d, scale)
            fill = _face_fill_ratio(verts)
            if fill < FILL_MIN:
                stats["kept-nonrect"] += 1
                print(f"  [keep]    {nm[:46]:46}  non-rectangular (fill {fill:.2f}) → preserved")
                continue
            if _body_shaperep(d) is None:
                stats["kept-nobody"] += 1
                print(f"  [keep]    {nm[:46]:46}  no Body representation → preserved")
                continue
            reauthor_door(model, d, scale, recipe, owner)
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
# Built-in verification (reopen src + out; assert "only the doors changed")
# ══════════════════════════════════════════════════════════════════════════════
_PRESERVE_TYPES = [
    "IfcDoor", "IfcWindow", "IfcWall", "IfcWallStandardCase", "IfcSlab", "IfcRoof",
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

    print("  preservation (before → after):")
    for t in _PRESERVE_TYPES:
        nb, na = len(before.by_type(t)), len(after.by_type(t))
        if nb == 0 and na == 0:
            continue
        good = nb == na
        ok &= good
        print(f"    {t:34s} {nb} → {na}  [{'OK' if good else 'CHANGED!'}]")

    gb = {d.GlobalId for d in before.by_type("IfcDoor")}
    ga = {d.GlobalId for d in after.by_type("IfcDoor")}
    good = gb == ga; ok &= good
    print(f"  door GlobalIds preserved: {len(gb & ga)}/{len(gb)}  [{'OK' if good else 'CHANGED!'}]")

    eb = sum(len(before.by_type(t)) for t in ("IfcRelFillsElement", "IfcRelVoidsElement"))
    ea = sum(len(after.by_type(t)) for t in ("IfcRelFillsElement", "IfcRelVoidsElement"))
    good = eb == ea; ok &= good
    print(f"  fill/void relationship edges: {eb} → {ea}  [{'OK' if good else 'CHANGED!'}]")

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

    bpl = {d.GlobalId: ifc_placement.get_local_placement(d.ObjectPlacement)
           for d in before.by_type("IfcDoor") if d.ObjectPlacement}
    bad = 0
    for d in after.by_type("IfcDoor"):
        if (d.Description or "") != MARK:
            continue
        m0 = bpl.get(d.GlobalId)
        if m0 is None or d.ObjectPlacement is None or \
           not np.allclose(m0, ifc_placement.get_local_placement(d.ObjectPlacement), atol=1e-9):
            bad += 1
    ok &= bad == 0
    print(f"  rebuilt doors keep placement: {'OK' if bad == 0 else f'{bad} drifted!'}")

    # rebuilt doors occupy the same world box on the FACE PLANE (depth envelope may shift by the
    # folding clamp / proud handles; measure drift on the two largest-extent axes only)
    bbox_b = {}
    for d in before.by_type("IfcDoor"):
        try:
            bbox_b[d.GlobalId] = _world_bbox(d)
        except Exception:
            pass
    max_drift = 0.0; checked = 0
    for d in after.by_type("IfcDoor"):
        if (d.Description or "") != MARK:
            continue
        b = bbox_b.get(d.GlobalId)
        if b is None:
            continue
        try:
            mn_a, mx_a = _world_bbox(d)
        except Exception:
            continue
        size = (b[1] - b[0])
        face = np.argsort(size)[1:]          # two largest-extent world axes
        drift = float(max(np.abs(np.r_[(mn_a - b[0])[face], (mx_a - b[1])[face]])))
        max_drift = max(max_drift, drift); checked += 1
    good = max_drift <= BBOX_TOL_M; ok &= good
    print(f"  rebuilt door face-bbox drift (n={checked}): max {max_drift * 1000:.1f} mm  "
          f"[{'OK' if good else f'> {BBOX_TOL_M*1000:.0f} mm!'}]")

    styled_ids = {s.Item.id() for s in after.by_type("IfcStyledItem") if s.Item}
    unstyled = 0
    for d in after.by_type("IfcDoor"):
        if (d.Description or "") != MARK:
            continue
        body = _body_shaperep(d)
        items = list(body.Items or []) if body else []
        if items and not all(it.id() in styled_ids for it in items):
            unstyled += 1
    ok &= unstyled == 0
    print(f"  rebuilt doors missing styles: {unstyled}  "
          f"[{'OK — all styled' if unstyled == 0 else 'WOULD RENDER GRAY!'}]")

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
