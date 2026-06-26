"""
golden_geometry.py — the SINGLE shared parametric window recipe.

Both ``generate_goldens.py`` (which writes the reviewable golden template IFCs) and the
converter (``IFC_window_converter_V2.py``, which rebuilds baked windows in-place) author
geometry through this one module. That guarantees a converted window is *provably identical*
to its golden template, only scaled to the measured instance dimensions.

Geometry entities (``IfcExtrudedAreaSolid`` / ``IfcRectangleProfileDef`` /
``IfcRectangleHollowProfileDef`` / placements / points / directions) are stable across
IFC2X3 / IFC4 / IFC4X3, so this module is schema-agnostic. The schema-*specific* parts
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
LINING_THK   =  60.0   # frame face width  (hollow-profile WallThickness)
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


def _hollow(f, xdim, ydim, wall):
    return f.create_entity("IfcRectangleHollowProfileDef", ProfileType="AREA",
                           Position=_ax2(f, 0, 0), XDim=float(xdim), YDim=float(ydim),
                           WallThickness=float(wall))


# ── the recipe ───────────────────────────────────────────────────────────────────
def build_window_items(f, width, height, depth, *, frame_thk, glaze_thk, bar_thk, split,
                        center=(0.0, 0.0, 0.0), depth_dir=(0.0, 0.0, 1.0),
                        width_dir=(1.0, 0.0, 0.0)):
    """Author one window's body as clean parametric swept solids.

    Returns a list of ``(solid, role)`` where role ∈ {"frame", "bar", "pane"}:
      - exactly one "frame" (hollow rectangular lining),
      - 0 or 1 "bar" (mullion if split='V', transom if split='H'),
      - 1 or 2 "pane" (centred glazing).

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

    items = []
    # Outer lining frame — the single hollow profile, full depth.
    frame = _extrude_along(f, _hollow(f, width, height, frame_thk),
                           center, depth_dir, width_dir, depth)
    items.append((frame, "frame"))

    iW = width - 2 * frame_thk     # inner glazed-opening width
    iH = height - 2 * frame_thk    # inner glazed-opening height

    if split == "V":
        # Vertical mullion spanning the full height (meets head + sill); left + right panes.
        items.append((_extrude_along(f, _rect(f, bar_thk, height),
                                     center, depth_dir, width_dir, depth), "bar"))
        pane_w = (iW - bar_thk) / 2.0
        off = bar_thk / 2.0 + pane_w / 2.0
        for cx in (-off, off):
            items.append((_extrude_along(f, _rect(f, pane_w, iH, cx=cx),
                                        center, depth_dir, width_dir, glaze_depth), "pane"))

    elif split == "H":
        # Horizontal transom spanning the full width (meets both jambs); top + bottom panes.
        items.append((_extrude_along(f, _rect(f, width, bar_thk),
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
