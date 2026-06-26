"""
golden_geometry.py — the SINGLE shared parametric window recipe.

Both ``generate_goldens.py`` (which writes the reviewable golden template IFCs) and the
converter (``IFC_window_converter_V2.py``, which rebuilds baked windows in-place) author
geometry through this one module. That guarantees a converted window is *provably identical*
to its golden template, only scaled to the measured instance dimensions.

Geometry entities (``IfcExtrudedAreaSolid`` / ``IfcRectangleProfileDef`` / placements / points /
directions) are stable across IFC2X3 / IFC4 / IFC4X3, so this module is schema-agnostic. (The frame
is 4 solid ``IfcRectangleProfileDef`` bars, NOT an ``IfcRectangleHollowProfileDef`` — Gaudi
mis-renders the hollow profile; §6.) The schema-*specific* parts
(surface styles, window-type & panel property sets, Pset value types) live in
``schema_adapter.py``.

Coordinate convention (profile 2D plane): **X = width, Y = height**; the solid is extruded
along the *depth* axis (through-wall). The caller supplies ``depth_dir`` + ``width_dir`` so
the same recipe lands axis-aligned for a standalone golden, or along the measured through-wall
axis for an in-wall instance.
"""
import numpy as np

# ── canonical FormX frame proportions (millimetres) — used by the standalone goldens.
# The converter passes its own file-unit values (FRAME_THK_M / scale, …) instead.
LINING_DEPTH = 120.0   # frame depth into wall (extrusion depth)
LINING_THK   =  60.0   # frame face width  (width of each of the 4 lining bars)
GLAZE_THK    =  24.0   # glazing unit thickness
BAR_THK      =  60.0   # mullion / transom thickness


# ── low-level entity helpers ────────────────────────────────────────────────────
def _pt(f, *c):
    return f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(x) for x in c))

def _dir(f, *c):
    return f.create_entity("IfcDirection", DirectionRatios=tuple(float(x) for x in c))

def _ax2(f, cx=0.0, cy=0.0):
    return f.create_entity("IfcAxis2Placement2D",
                           Location=_pt(f, cx, cy), RefDirection=_dir(f, 1, 0))


def _extrude_along(f, profile, center, depth_dir, width_dir, depth):
    """Extrude ``profile`` by ``depth`` along ``depth_dir``, centred on ``center``.

    Maps profile-local +Z → ``depth_dir`` and profile-local +X → ``width_dir`` (so profile X
    is window width and profile Y is window height). The placement Location is pulled back half
    the depth so the solid straddles ``center`` in the through-wall direction; any in-plane
    offset of the part lives in the profile's own 2D Position (cx, cy).
    """
    loc = np.asarray(center, float) - np.asarray(depth_dir, float) * (depth / 2.0)
    pos = f.create_entity("IfcAxis2Placement3D", Location=_pt(f, *loc),
                          Axis=_dir(f, *depth_dir), RefDirection=_dir(f, *width_dir))
    return f.create_entity("IfcExtrudedAreaSolid", SweptArea=profile, Position=pos,
                           ExtrudedDirection=_dir(f, 0, 0, 1), Depth=float(depth))


def _rect(f, xdim, ydim, cx=0.0, cy=0.0):
    return f.create_entity("IfcRectangleProfileDef", ProfileType="AREA",
                           Position=_ax2(f, cx, cy), XDim=float(xdim), YDim=float(ydim))


# ── the recipe ───────────────────────────────────────────────────────────────────
def build_window_items(f, width, height, depth, *, frame_thk, glaze_thk, bar_thk, split,
                        center=(0.0, 0.0, 0.0), depth_dir=(0.0, 0.0, 1.0),
                        width_dir=(1.0, 0.0, 0.0)):
    """Author one window's body as clean parametric swept solids.

    Returns a list of ``(solid, role)`` where role ∈ {"frame", "bar", "pane"}:
      - exactly FOUR "frame" solids (the lining border, as 4 solid bars — see note below),
      - 0 or 1 "bar" (mullion if split='V', transom if split='H'),
      - 1 or 2 "pane" (centred glazing).

    FRAME = 4 SOLID BARS, NOT a hollow profile (CLAUDE.md §6). FormX's viewer Gaudi mis-renders
    ``IfcRectangleHollowProfileDef`` — it draws the ring's inner opening larger than authored,
    leaving a uniform "space" band between the lining and the pane (Blender/openIFC render the same
    mesh flush; the repo README also notes openIFC *skips* hollow-profile frames). Building the
    border from four plain ``IfcRectangleProfileDef`` bars renders flush in every viewer, including
    Gaudi (confirmed by side-by-side test), at the cost of 4 solids instead of 1.

    ``split``: None → single pane · 'V' → vertical mullion, left/right panes
    (DOUBLE_HORIZONTAL) · 'H' → horizontal transom, top/bottom panes (DOUBLE_VERTICAL /
    double-hung sashes).

    All lengths are in the caller's file units. ``frame_thk`` / ``glaze_thk`` are clamped so a
    small instance still yields a valid (non-degenerate) frame + inset pane.
    """
    # Clamp so a tiny instance can't produce a frame thicker than the window or a glaze unit
    # deeper than the wall (identical clamps to window converter v1 — goldens never trip them).
    frame_thk = float(min(frame_thk, 0.4 * min(width, height)))
    glaze_thk = float(min(glaze_thk, 0.6 * depth))
    bar_thk   = float(min(bar_thk, 0.4 * min(width, height)))

    # Glazing fills the FULL lining depth and exactly fills the opening, flush with the lining
    # faces — so there is no recessed "well" (the Gaudi gap) and the glass front tiles cleanly
    # against the lining front (adjacent areas, non-overlapping → no z-fighting). The
    # mullion/transom run the FULL window extent so they meet the head/sill/jambs instead of
    # stopping at the inner opening (they used to end at iH/iW, leaving the gap at the frame top).
    glaze_depth = depth

    iW = width - 2 * frame_thk     # inner glazed-opening width
    iH = height - 2 * frame_thk    # inner glazed-opening height

    items = []
    # Outer lining frame — FOUR solid bars (top/bottom span full width; left/right span the inner
    # height between them), forming a border of thickness `frame_thk`. Replaces the single
    # IfcRectangleHollowProfileDef, which Gaudi mis-renders (the pane↔frame "space" — §6).
    items += [
        (_extrude_along(f, _rect(f, width, frame_thk, cy=(height - frame_thk) / 2.0),
                        center, depth_dir, width_dir, depth), "frame"),   # head
        (_extrude_along(f, _rect(f, width, frame_thk, cy=-(height - frame_thk) / 2.0),
                        center, depth_dir, width_dir, depth), "frame"),   # sill
        (_extrude_along(f, _rect(f, frame_thk, iH, cx=-(width - frame_thk) / 2.0),
                        center, depth_dir, width_dir, depth), "frame"),   # left jamb
        (_extrude_along(f, _rect(f, frame_thk, iH, cx=(width - frame_thk) / 2.0),
                        center, depth_dir, width_dir, depth), "frame"),   # right jamb
    ]

    if split == "V":
        # Vertical mullion between the jambs (meets head + sill); left + right panes.
        items.append((_extrude_along(f, _rect(f, bar_thk, iH),
                                     center, depth_dir, width_dir, depth), "bar"))
        pane_w = (iW - bar_thk) / 2.0
        off = bar_thk / 2.0 + pane_w / 2.0
        for cx in (-off, off):
            items.append((_extrude_along(f, _rect(f, pane_w, iH, cx=cx),
                                        center, depth_dir, width_dir, glaze_depth), "pane"))

    elif split == "H":
        # Horizontal transom between the jambs (meets head + sill); top + bottom panes.
        items.append((_extrude_along(f, _rect(f, iW, bar_thk),
                                     center, depth_dir, width_dir, depth), "bar"))
        pane_h = (iH - bar_thk) / 2.0
        off = bar_thk / 2.0 + pane_h / 2.0
        for cy in (off, -off):
            items.append((_extrude_along(f, _rect(f, iW, pane_h, cy=cy),
                                        center, depth_dir, width_dir, glaze_depth), "pane"))

    else:
        # Single pane filling the opening.
        items.append((_extrude_along(f, _rect(f, iW, iH),
                                     center, depth_dir, width_dir, glaze_depth), "pane"))

    return items
