"""
golden_door_geometry.py — the SINGLE shared parametric door recipe.

Door v2 analog of the window converter's ``golden_geometry.py``. Both
``generate_goldens.py`` (which writes the reviewable golden template IFCs) and the converter
(``IFC_door_converter_V2.py`` — which rebuilds baked doors in-place) author geometry through this
one module. That guarantees a converted door is *provably identical* to its golden template, only
scaled to the measured instance dimensions.

Geometry entities (``IfcExtrudedAreaSolid`` / ``IfcRectangleProfileDef`` / placements / points /
directions) are stable across IFC2X3 / IFC4 / IFC4X3, so this module is schema-agnostic. (The lining
is 4 solid ``IfcRectangleProfileDef`` bars, NOT an ``IfcRectangleHollowProfileDef`` — Gaudi
mis-renders the hollow profile; §6.) The schema-*specific* parts
(surface styles, door-type & panel property sets, Pset value types) live in ``schema_adapter.py``.

Coordinate convention (profile 2D plane): **X = width, Y = height**; the solid is extruded
along the *depth* axis (through-wall). The caller supplies ``depth_dir`` + ``width_dir`` so the
same recipe lands axis-aligned for a standalone golden, or along the measured through-wall axis
for an in-wall instance. The door body is centred on ``center`` in all three axes.

SCALE-CORRECTNESS (the key fix vs the first draft): every linear part dimension comes from a
``dims`` dict **in the caller's file units** — there are NO hard-coded millimetre fallbacks in the
build path. The canonical FormX proportions live in ``CANON`` (millimetres); call
``dims_in_units(unit_scale)`` to convert them to file units. The golden generator passes mm
(unit_scale = 0.001); the converter passes feet/metres (its measured ``calculate_unit_scale``).
So a 130 mm lever is 130 mm in the golden and the correct fraction of a foot in a converted door —
never 130 *feet*.

FIRST-PASS SIMPLIFICATIONS (decision #3 — refine after viewer review; tracked in the algorithm doc):
  * The lining is a full 4-sided border built from FOUR solid bars (real door frames are usually
    3-sided, no sill); the bars' width is the drivable frame-border parameter. (Was a single
    ``IfcRectangleHollowProfileDef`` — switched to 4 solid bars because Gaudi mis-renders the hollow
    profile, leaving a pane↔frame "space"; CLAUDE.md §6.)
  * Bifold / combo panels are flat & coplanar (NOT articulated/folded). "side_by_side" simply
    divides the inner width into N panels separated by mullions.
  * Barn track = a straight horizontal bar above the leaf + two roller tabs; sliding = a head-rail
    bar across the top of the opening. No curved or detailed hardware.
  * Handles are canonical boxes proud of BOTH faces (lever = short horizontal bar near the
    leading/meeting edge at ~1 m; pull = vertical bar centred on the active leaf).
    NOTE (viewer-review): a real pocket door's pull is recessed, not proud — left proud here.
"""
import numpy as np

# ── canonical FormX door proportions, in MILLIMETRES ──────────────────────────────
# Everything linear the recipe uses lives here, so a single conversion (dims_in_units) makes
# the whole part set scale-correct. ``track_overhang`` is a dimensionless ratio.
CANON = dict(
    lining_depth = 120.0,   # frame depth into wall (extrusion depth of the lining)
    frame_thk    =  60.0,   # frame face width (width of each of the 4 lining bars — the border)
    slab_thk     =  45.0,   # opaque leaf / panel thickness
    glaze_thk    =  24.0,   # glazed-panel thickness
    bar_thk      =  60.0,   # mullion thickness (divider between leaves)
    rail_thk     =  50.0,   # sliding head-rail / barn-track bar thickness
    handle_h     = 1000.0,  # handle centre height above the door bottom (~1 m)
    lever_len    = 130.0,   # lever bar length (along width)
    lever_thk    =  22.0,   # lever bar thickness (along height)
    pull_len     = 250.0,   # flush/D-pull length (along height)
    pull_thk     =  26.0,   # pull thickness (along width)
    hw_proud     =  16.0,   # how far hardware projects beyond EACH face
    edge_inset   =  55.0,   # handle inset from the leaf's leading/meeting edge
    roller_w     =  40.0,   # barn roller-tab width
    roller_h     = 150.0,   # barn roller-tab height (overlaps the track so they read as connected)
    track_overhang = 1.25,  # barn track width as a multiple of door width (dimensionless)
)

# Convenience module constants (mm) for callers that author non-geometry props (lining depth etc).
LINING_DEPTH = CANON["lining_depth"]
LINING_THK   = CANON["frame_thk"]
SLAB_THK     = CANON["slab_thk"]
GLAZE_THK    = CANON["glaze_thk"]
BAR_THK      = CANON["bar_thk"]


def dims_in_units(unit_scale):
    """Convert the canonical mm proportions to the caller's file units.

    ``unit_scale`` = metres per file unit (ifcopenshell ``calculate_unit_scale``):
      mm file  → 0.001  → returns the canonical mm values unchanged (identity);
      m  file  → 1.0    → millimetres ÷ 1000;
      ft file  → 0.3048 → millimetres ÷ 304.8.
    """
    mm_to_units = 0.001 / float(unit_scale)
    out = {}
    for k, v in CANON.items():
        out[k] = v if k == "track_overhang" else v * mm_to_units
    return out


# ── low-level entity helpers (verbatim from window golden_geometry.py) ───────────
def _pt(f, *c):
    return f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(x) for x in c))

def _dir(f, *c):
    return f.create_entity("IfcDirection", DirectionRatios=tuple(float(x) for x in c))

def _ax2(f, cx=0.0, cy=0.0):
    return f.create_entity("IfcAxis2Placement2D",
                           Location=_pt(f, cx, cy), RefDirection=_dir(f, 1, 0))


def _extrude_along(f, profile, center, depth_dir, width_dir, depth):
    """Extrude ``profile`` by ``depth`` along ``depth_dir``, centred on ``center``.

    Maps profile-local +Z → ``depth_dir`` and profile-local +X → ``width_dir`` (so profile X is
    door width and profile Y is door height). The placement Location is pulled back half the depth
    so the solid straddles ``center`` in the through-wall direction; any in-plane offset of the
    part lives in the profile's own 2D Position (cx, cy).
    """
    loc = np.asarray(center, float) - np.asarray(depth_dir, float) * (depth / 2.0)
    pos = f.create_entity("IfcAxis2Placement3D", Location=_pt(f, *loc),
                          Axis=_dir(f, *depth_dir), RefDirection=_dir(f, *width_dir))
    return f.create_entity("IfcExtrudedAreaSolid", SweptArea=profile, Position=pos,
                           ExtrudedDirection=_dir(f, 0, 0, 1), Depth=float(depth))


def _rect(f, xdim, ydim, cx=0.0, cy=0.0):
    return f.create_entity("IfcRectangleProfileDef", ProfileType="AREA",
                           Position=_ax2(f, cx, cy), XDim=float(xdim), YDim=float(ydim))


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ── layout helper ─────────────────────────────────────────────────────────────────
def _panel_layout(inner_w, n, bar_thk):
    """Lay out ``n`` equal panels across ``inner_w`` separated by (n-1) mullions of ``bar_thk``.

    Returns (panes, mullions) where panes = [(cx, pane_w), …] and mullions = [cx, …], all centred
    so the whole group is symmetric about 0. The caller is responsible for clamping ``bar_thk`` so
    ``pane_w`` stays positive (see ``build_door_items``).
    """
    if n <= 0:
        return [], []
    pane_w = (inner_w - (n - 1) * bar_thk) / n
    panes, mullions = [], []
    x = -inner_w / 2.0
    for i in range(n):
        panes.append((x + pane_w / 2.0, pane_w))
        x += pane_w
        if i < n - 1:
            mullions.append(x + bar_thk / 2.0)
            x += bar_thk
    return panes, mullions


def _build_handles(f, recipe, d, inner_h, height, depth, bar_thk, panes,
                   center, depth_dir, width_dir):
    """Author canonical handle(s) per the recipe. Returns [(solid, "handle"), …].

    lever → short horizontal bar near a leaf's leading/meeting edge at ~handle_h above the bottom;
            one per leaf for a 2-leaf door (meeting at the central mullion), else one on the leading
            (right) edge.
    pull  → vertical bar centred on the active (rightmost) leaf.
    none  → nothing.

    All hardware dims come from ``d`` (file units) and every position is clamped to keep the part
    inside its leaf — so the recipe survives narrow / odd-aspect doors at feet scale.
    """
    kind = recipe.get("handle", "none")
    if kind == "none" or not panes:
        return []

    hw_depth = depth + 2.0 * d["hw_proud"]            # proud of BOTH faces
    cy = _clamp(-height / 2.0 + d["handle_h"],
                -inner_h / 2.0 + d["lever_len"], inner_h / 2.0 - d["lever_len"])

    out = []
    if kind == "lever":
        lever_len = d["lever_len"]
        if len(panes) >= 2:
            # two levers meeting at the central mullion (between the two centre-most leaves)
            mid = len(panes) // 2
            lcx, lpw = panes[mid - 1]
            rcx, rpw = panes[mid]
            ll = min(lever_len, 0.8 * lpw)
            rl = min(lever_len, 0.8 * rpw)
            lx = _clamp(lcx + lpw / 2.0 - bar_thk / 2.0 - d["edge_inset"],
                        lcx - lpw / 2.0 + ll / 2.0, lcx + lpw / 2.0 - ll / 2.0)
            rx = _clamp(rcx - rpw / 2.0 + bar_thk / 2.0 + d["edge_inset"],
                        rcx - rpw / 2.0 + rl / 2.0, rcx + rpw / 2.0 - rl / 2.0)
            out.append((_extrude_along(f, _rect(f, ll, d["lever_thk"], cx=lx, cy=cy),
                                       center, depth_dir, width_dir, hw_depth), "handle"))
            out.append((_extrude_along(f, _rect(f, rl, d["lever_thk"], cx=rx, cy=cy),
                                       center, depth_dir, width_dir, hw_depth), "handle"))
        else:
            cx, pw = panes[0]
            ll = min(lever_len, 0.8 * pw)
            lx = _clamp(cx + pw / 2.0 - d["edge_inset"] - ll / 2.0,
                        cx - pw / 2.0 + ll / 2.0, cx + pw / 2.0 - ll / 2.0)
            out.append((_extrude_along(f, _rect(f, ll, d["lever_thk"], cx=lx, cy=cy),
                                       center, depth_dir, width_dir, hw_depth), "handle"))
    elif kind == "pull":
        cx, pw = panes[-1]                            # active (rightmost) leaf
        pt = min(d["pull_thk"], 0.5 * pw)
        px = _clamp(cx + pw / 2.0 - d["edge_inset"] - pt / 2.0,
                    cx - pw / 2.0 + pt / 2.0, cx + pw / 2.0 - pt / 2.0)
        out.append((_extrude_along(f, _rect(f, pt, d["pull_len"], cx=px, cy=0.0),
                                   center, depth_dir, width_dir, hw_depth), "handle"))
    return out


# ── the recipe ───────────────────────────────────────────────────────────────────
def build_door_items(f, width, height, depth, *, recipe, dims,
                     center=(0.0, 0.0, 0.0), depth_dir=(0.0, 0.0, 1.0),
                     width_dir=(1.0, 0.0, 0.0)):
    """Author one door's body as clean parametric swept solids.

    Returns a list of ``(solid, role)`` where role ∈
        {"frame", "panel", "mullion", "rail", "track", "roller", "handle"}.

    ``recipe`` (geometry knobs, NOT semantics):
        panels      : int   number of leaves (0 → cased opening, lining only)
        arrangement : str   "side_by_side" (only mode in first pass)
        glazed      : bool  panels use the glazed thickness (style applied by the caller)
        handle      : str   "lever" | "pull" | "none"
        head_rail   : bool  add a sliding head-rail bar across the top of the opening
        barn_track  : bool  add an exposed barn track bar above the leaf + two roller tabs

    ``dims`` : a dict of linear part dimensions IN THE CALLER'S FILE UNITS (see ``dims_in_units``).
    ``width`` / ``height`` / ``depth`` are also in file units (depth = measured through-wall depth).

    Clamps keep every feature non-degenerate for small / odd-aspect instances at any scale.
    """
    n      = int(recipe.get("panels", 1))
    glazed = bool(recipe.get("glazed", False))
    d      = dims

    # Frame border ≤ 1/5 of the smaller face dim; keeps a positive inner opening.
    frame_thk = _clamp(d["frame_thk"], 0.0, 0.2 * min(width, height))
    inner_w = width - 2.0 * frame_thk
    inner_h = height - 2.0 * frame_thk

    # Mullion thickness clamped so the subdivided panes stay positive (>= half the inner width
    # of total pane area). For n=1 there are no mullions, so this is a no-op.
    bar_thk = d["bar_thk"]
    if n >= 2:
        bar_thk = _clamp(bar_thk, 0.0, 0.5 * inner_w / n)
    panel_th = min(d["glaze_thk"] if glazed else d["slab_thk"], 0.9 * depth)
    rail_thk = min(d["rail_thk"], 0.4 * inner_h)

    items = []
    # 1) Outer lining frame — FOUR solid bars (head + sill span full width; the two jambs span the
    #    inner height between them), forming a border of thickness `frame_thk`. Replaces the single
    #    IfcRectangleHollowProfileDef, which Gaudi mis-renders (the pane↔frame "space" — §6); four
    #    plain IfcRectangleProfileDef bars render flush in every viewer including Gaudi.
    items += [
        (_extrude_along(f, _rect(f, width, frame_thk, cy=(height - frame_thk) / 2.0),
                        center, depth_dir, width_dir, depth), "frame"),
        (_extrude_along(f, _rect(f, width, frame_thk, cy=-(height - frame_thk) / 2.0),
                        center, depth_dir, width_dir, depth), "frame"),
        (_extrude_along(f, _rect(f, frame_thk, inner_h, cx=-(width - frame_thk) / 2.0),
                        center, depth_dir, width_dir, depth), "frame"),
        (_extrude_along(f, _rect(f, frame_thk, inner_h, cx=(width - frame_thk) / 2.0),
                        center, depth_dir, width_dir, depth), "frame"),
    ]

    # 2) Panels + mullions (cased opening has none).
    panes, mullions = _panel_layout(inner_w, n, bar_thk) if n > 0 else ([], [])
    for (cx, pw) in panes:
        items.append((_extrude_along(f, _rect(f, pw, inner_h, cx=cx),
                                     center, depth_dir, width_dir, panel_th), "panel"))
    for mx in mullions:
        items.append((_extrude_along(f, _rect(f, bar_thk, inner_h, cx=mx),
                                     center, depth_dir, width_dir, depth), "mullion"))

    # 3) Sliding head rail — a horizontal bar across the top of the opening, full depth.
    if recipe.get("head_rail") and n > 0:
        ry = inner_h / 2.0 - rail_thk / 2.0
        items.append((_extrude_along(f, _rect(f, inner_w, rail_thk, cy=ry),
                                     center, depth_dir, width_dir, depth), "rail"))

    # 4) Barn track — an exposed bar ABOVE the leaf (proud of the face) + two roller tabs that
    #    overlap the track vertically so they read as hanging from it.
    if recipe.get("barn_track"):
        track_w  = width * d["track_overhang"]
        track_y  = height / 2.0 + rail_thk
        track_dp = depth + 2.0 * d["hw_proud"]
        items.append((_extrude_along(f, _rect(f, track_w, rail_thk, cy=track_y),
                                     center, depth_dir, width_dir, track_dp), "track"))
        roller_h = d["roller_h"]
        roller_y = track_y - roller_h / 2.0          # roller top reaches into the track band
        for rx in (-width / 4.0, width / 4.0):
            items.append((_extrude_along(f, _rect(f, d["roller_w"], roller_h, cx=rx, cy=roller_y),
                                         center, depth_dir, width_dir, track_dp), "roller"))

    # 5) Canonical handle(s).
    items += _build_handles(f, recipe, d, inner_h, height, depth, bar_thk, panes,
                            center, depth_dir, width_dir)

    return items
