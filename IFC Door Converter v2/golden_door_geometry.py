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
  * Double doors (``leaf_frame=True``) author each leaf as its OWN framed door — a 4-bar leaf frame
    (stiles + rails) around an inset glass/slab, laid edge-to-edge so the abutting meeting stiles
    read as the centre divider (no separate mullion), with the knob mounted on the meeting STILE
    (the frame), not the glass. Matches the FormX "double = two doors" template. Other multi-leaf
    types still use the simpler N-panes-plus-mullions layout (``leaf_frame=False``).
  * Bifold / combo panels are flat & coplanar (NOT articulated/folded). "side_by_side" simply
    divides the inner width into N panels separated by mullions.
  * Barn track = a straight horizontal bar above the leaf + two roller tabs.
  * Sliding doors (``sliding=True``) mimic the real San Juan Cypress sliding glass door: two equal
    framed-glass sashes, each a bit over half-width so they overlap at the centre, on two depth
    tracks (one toward the back face, one toward the front). No mullion, no handle.
  * Handles are canonical boxes proud of BOTH faces (lever = a compact proud KNOB hugging the
    leading/meeting edge at ~1 m — refined from the original flat lever bar to match the FormX
    template's knobs; pull = vertical bar centred on the active leaf).
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
    leaf_frame_thk = 70.0,  # per-leaf frame (stile/rail) face-width when leaf_frame=True (double doors)
    rail_thk     =  50.0,   # sliding head-rail / barn-track bar thickness
    handle_h     = 1000.0,  # handle centre height above the door bottom (~1 m)
    knob_w       =  55.0,   # door-knob width  (along width)  — compact so it reads as a knob, not a flat tab
    knob_h       =  55.0,   # door-knob height (along height)
    knob_proud   =  30.0,   # how far the knob projects beyond EACH face (proud → 3D, not flat)
    pull_len     = 250.0,   # flush/D-pull length (along height)
    pull_thk     =  26.0,   # pull thickness (along width)
    hw_proud     =  16.0,   # how far hardware projects beyond EACH face
    edge_inset   =  55.0,   # handle inset from the leaf's leading/meeting edge
    roller_w     =  40.0,   # barn roller-tab width
    roller_h     = 150.0,   # barn roller-tab height (overlaps the track so they read as connected)
    track_overhang   = 1.25,  # barn track width as a multiple of door width (dimensionless)
    # sliding door (mimics the San Juan Cypress sliding glass door): two equal framed-glass sashes,
    # each a bit over half-width so they OVERLAP at the centre, sitting on two depth tracks.
    sash_frame_thk   = 72.0,  # mm: sash stile/rail face-width (= (sash_w − glass_w)/2)
    sash_overlap     = 0.06,  # fraction of inner_w the two sashes overlap at the centre (dimensionless)
    sash_depth_ratio = 0.42,  # each sash's through-wall depth as a fraction of the lining depth (dimensionless)
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
    _dimensionless = {"track_overhang", "sash_overlap", "sash_depth_ratio"}  # ratios — no unit conversion
    out = {}
    for k, v in CANON.items():
        out[k] = v if k in _dimensionless else v * mm_to_units
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


def _border_bars(f, w, h, t, center, depth_dir, width_dir, depth, cx=0.0, cy=0.0, sill=True):
    """Solid IfcRectangleProfileDef bars forming a rectangular border of face-width ``t``, centred
    at (cx, cy), each extruded the full ``depth``. With ``sill=True`` (default): four bars — head +
    sill (full width) and two jambs spanning the inner height between them (the outer lining and each
    leaf_frame). With ``sill=False``: THREE bars — head + two jambs that run the full height to the
    floor (a 3-sided doorway lining, no threshold). Returns [(solid, "frame"), …]."""
    bars = [
        (_extrude_along(f, _rect(f, w, t, cx=cx, cy=cy + (h - t) / 2.0),
                        center, depth_dir, width_dir, depth), "frame"),           # head
    ]
    if sill:
        bars.append((_extrude_along(f, _rect(f, w, t, cx=cx, cy=cy - (h - t) / 2.0),
                                    center, depth_dir, width_dir, depth), "frame"))  # sill
        jamb_h, jamb_cy = h - 2.0 * t, cy
    else:
        jamb_h, jamb_cy = h - t, cy - t / 2.0      # jambs reach the floor; shifted down by half the head
    bars.append((_extrude_along(f, _rect(f, t, jamb_h, cx=cx - (w - t) / 2.0, cy=jamb_cy),
                                center, depth_dir, width_dir, depth), "frame"))      # left jamb
    bars.append((_extrude_along(f, _rect(f, t, jamb_h, cx=cx + (w - t) / 2.0, cy=jamb_cy),
                                center, depth_dir, width_dir, depth), "frame"))      # right jamb
    return bars


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
                   center, depth_dir, width_dir, stile_thk=None, up_sign=1.0):
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

    hw_depth = depth + 2.0 * d["hw_proud"]            # pull proud of BOTH faces
    # ``handle_h`` is measured above the door's WORLD bottom. ``up_sign`` (+1 / -1) flips the sign
    # when the instance's local height axis points down in world (Revit doors are exported either
    # way) — so the knob lands ~1 m above the floor on every door, not near the top on flipped ones.
    cy = _clamp(up_sign * (-height / 2.0 + d["handle_h"]),
                -inner_h / 2.0 + d["knob_h"], inner_h / 2.0 - d["knob_h"])

    out = []
    if kind == "lever":
        # A compact proud KNOB at each active leaf's leading/meeting edge (refined from the original
        # flat lever bar to match the FormX template). One knob per active leaf: a 2-leaf door gets
        # two knobs flanking the centre; a single leaf gets one at its leading edge. Each knob is one
        # solid (so the recipe's per-door solid count is unchanged).
        #
        # Placement: when the leaves are framed (``stile_thk`` given, leaf_frame mode) the knob sits
        # ON the meeting STILE — its centre offset from the meeting edge by half the stile width — so
        # the handle reads as mounted on the frame, not floating in the glass. Otherwise it is inset
        # into the leaf by ``edge_inset`` (single-leaf / non-framed doors).
        knob_h  = d["knob_h"]
        knob_dp = depth + 2.0 * d["knob_proud"]       # proud enough to read as 3D, not a flat tab
        offset  = (stile_thk / 2.0) if stile_thk is not None else d["edge_inset"]

        def _knob(cx, pw, kx):
            kw = min(d["knob_w"], 0.5 * pw)           # keep the knob inside narrow leaves
            kx = _clamp(kx, cx - pw / 2.0 + kw / 2.0, cx + pw / 2.0 - kw / 2.0)
            return (_extrude_along(f, _rect(f, kw, knob_h, cx=kx, cy=cy),
                                   center, depth_dir, width_dir, knob_dp), "handle")

        if len(panes) >= 2:
            mid = len(panes) // 2
            lcx, lpw = panes[mid - 1]
            rcx, rpw = panes[mid]
            out.append(_knob(lcx, lpw, lcx + lpw / 2.0 - offset))  # left leaf — on/near its right (meeting) edge
            out.append(_knob(rcx, rpw, rcx - rpw / 2.0 + offset))  # right leaf — on/near its left (meeting) edge
        else:
            cx, pw = panes[0]
            out.append(_knob(cx, pw, cx + pw / 2.0 - offset))      # single — on/near the leading (right) edge
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
                     width_dir=(1.0, 0.0, 0.0), up_sign=1.0):
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
    n          = int(recipe.get("panels", 1))
    glazed     = bool(recipe.get("glazed", False))
    leaf_frame = bool(recipe.get("leaf_frame", False))
    sliding    = bool(recipe.get("sliding", False))
    pocket     = bool(recipe.get("pocket", False))
    d          = dims

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
    # 1) Outer lining frame — FOUR solid bars (NOT IfcRectangleHollowProfileDef, which Gaudi
    #    mis-renders as a pane↔frame "space" — §6; four plain bars render flush everywhere).
    #    SKIPPED in pocket mode: a pocket door's measured bbox is ~2× the opening (it includes the
    #    in-wall pocket), so the lining must frame only the OPENING half, not the whole bbox — the
    #    pocket branch builds that frame itself.
    if not pocket:
        items += _border_bars(f, width, height, frame_thk, center, depth_dir, width_dir, depth)

    # 2) Leaves. Authoring modes:
    #    pocket=True     → measured bbox = opening half + in-wall pocket half (mimics the real
    #                      Sunflower pocket door). Lining frames the OPENING half; the thin leaf sits
    #                      RETRACTED in the pocket half, leading edge revealed at the jamb, flush pull
    #                      on that edge. (Real door: frame ⊂ one half, leaf ⊂ the other.)
    #    sliding=True    → two equal framed-glass SASHES, each a bit over half-width so they overlap
    #                      at the centre, sitting on two depth tracks (one toward the back, one toward
    #                      the front of the lining). Mimics the real San Juan Cypress sliding glass
    #                      door: each sash = a 4-bar frame + inset glass. No mullion, no handle.
    #    leaf_frame=True → each leaf is its OWN framed door: a 4-bar leaf frame (stiles + rails)
    #                      around an inset glass/slab infill, laid edge-to-edge so the abutting
    #                      meeting stiles form the centre divider (no separate mullion). The handle
    #                      mounts on the meeting stile. Matches the FormX "double = two doors" look.
    #    default         → the simpler first pass: N infill panes separated by (N-1) mullions.
    # `panes` = (centre_x, leaf_or_pane_width) per leaf, used to anchor the handle(s).
    panes = []
    stile_thk = None
    if n > 0 and pocket:
        # Opening = LEFT half; in-wall pocket = RIGHT half. 3-SIDED lining (head + 2 jambs, NO sill).
        opening_w = width / 2.0
        open_cx   = -width / 2.0 + opening_w / 2.0
        of_thk    = _clamp(d["frame_thk"], 0.0, 0.2 * min(opening_w, height))
        items += _border_bars(f, opening_w, height, of_thk,
                              center, depth_dir, width_dir, depth, cx=open_cx, sill=False)
        # Leaf RETRACTED into the pocket half: fills the right half, runs to the floor (no sill),
        # reaches the full measured width like the real baked door (no short edge → no drift).
        # Leading (left) edge at the bbox centre.
        leaf_w  = opening_w
        leaf_h  = height - of_thk                       # head inset only; bottom to the floor
        leaf_cx = width / 4.0                           # centre of the right (pocket) half
        items.append((_extrude_along(f, _rect(f, leaf_w, leaf_h, cx=leaf_cx, cy=-of_thk / 2.0),
                                     center, depth_dir, width_dir, panel_th), "panel"))
        # Pull at the leaf's LEADING (left) edge — flush against the interior of the frame opening —
        # projecting from ONE face only (the interior, facing inward), NOT proud of both faces.
        pt     = min(d["pull_thk"], 0.5 * leaf_w)
        px     = leaf_cx - leaf_w / 2.0 + pt / 2.0      # hug the leading edge (toward the opening)
        proj   = 2.0 * d["hw_proud"]                    # stand-off from the interior face
        pull_c = tuple(np.asarray(center, float)
                       + np.asarray(depth_dir, float) * (panel_th / 2.0 + proj / 2.0))
        items.append((_extrude_along(f, _rect(f, pt, d["pull_len"], cx=px, cy=0.0),
                                     pull_c, depth_dir, width_dir, proj), "handle"))
    elif n > 0 and sliding:
        # Two equal sashes overlapping at the centre on two depth tracks.
        overlap_w = d["sash_overlap"] * inner_w
        sash_w    = (inner_w + overlap_w) / 2.0             # each sash a bit over half → they overlap
        left_cx   = -inner_w / 2.0 + sash_w / 2.0           # back-track sash, left
        right_cx  =  inner_w / 2.0 - sash_w / 2.0           # front-track sash, right
        sash_thk  = _clamp(d["sash_frame_thk"], 0.0, 0.3 * sash_w)
        sash_dp   = _clamp(d["sash_depth_ratio"] * depth, panel_th, 0.48 * depth)
        # depth offsets keep each sash fully INSIDE the lining depth (no poking out the wall — that
        # was the earlier failure): back sash flush to the back face, front sash flush to the front.
        back_c  = tuple(np.asarray(center, float) - np.asarray(depth_dir, float) * (depth - sash_dp) / 2.0)
        front_c = tuple(np.asarray(center, float) + np.asarray(depth_dir, float) * (depth - sash_dp) / 2.0)
        # LEFT sash on the FRONT track, RIGHT sash on the back track.
        for (cx, sc) in ((left_cx, front_c), (right_cx, back_c)):
            items += _border_bars(f, sash_w, inner_h, sash_thk,
                                  sc, depth_dir, width_dir, sash_dp, cx=cx)
            gw = sash_w - 2.0 * sash_thk
            gh = inner_h - 2.0 * sash_thk
            items.append((_extrude_along(f, _rect(f, gw, gh, cx=cx),
                                         sc, depth_dir, width_dir, panel_th), "panel"))
            panes.append((cx, sash_w))
    elif n > 0 and leaf_frame:
        # leaf frame ≤ ~1/3 of the per-leaf width so the inset infill stays positive on narrow doors
        stile_thk = _clamp(d["leaf_frame_thk"], 0.0, 0.35 * (inner_w / n))
        leaf_w = inner_w / n
        for i in range(n):
            lcx = -inner_w / 2.0 + leaf_w * (i + 0.5)
            items += _border_bars(f, leaf_w, inner_h, stile_thk,
                                  center, depth_dir, width_dir, depth, cx=lcx)
            gw = leaf_w - 2.0 * stile_thk
            gh = inner_h - 2.0 * stile_thk
            items.append((_extrude_along(f, _rect(f, gw, gh, cx=lcx),
                                         center, depth_dir, width_dir, panel_th), "panel"))
            panes.append((lcx, leaf_w))
    elif n > 0:
        panes, mullions = _panel_layout(inner_w, n, bar_thk)
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

    # 5) Canonical handle(s). (Pocket mode authored its own leading-edge pull above.)
    if not pocket:
        items += _build_handles(f, recipe, d, inner_h, height, depth, bar_thk, panes,
                                center, depth_dir, width_dir, stile_thk=stile_thk, up_sign=up_sign)

    return items
