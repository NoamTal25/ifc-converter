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
    # ── richer-design constants (one canonical value per physical part; per-mode CLAMPS shrink
    #    them on narrow/short doors — positional RATIOS are inlined as literals in build_door_items,
    #    NOT stored here, so they never get unit-converted). ──────────────────────────────────────
    stile_w      = 110.0,   # rail-and-stile leaf stile/member face width (panelled solid leaves)
    top_rail_h   = 140.0,   # panelled-leaf top rail height
    lock_rail_h  = 180.0,   # panelled-leaf lock/mid rail height (sits at handle height; widest but bottom)
    bottom_rail_h= 230.0,   # panelled-leaf bottom rail height (widest)
    muntin_thk   =  24.0,   # applied glazing-bar (muntin) face width/height (French / divided lites)
    muntin_proud =   6.0,   # how far a muntin bar stands proud of EACH glass face
    glass_lock_rail_h = 110.0,  # glazed French leaf mid (lock) cross-rail height
    hinge_w      =  30.0,   # ONE canonical butt-hinge leaf width (along door width); clamped per mode
    hinge_h      = 100.0,   # ONE canonical butt-hinge leaf height (along door height); clamped per mode
    hinge_proud  =  14.0,   # how far a hinge knuckle projects beyond ONE face (single-sided)
    astragal_w   =  50.0,   # double-door astragal cover-bar face width (over the meeting joint)
    astragal_proud = 18.0,  # how far the astragal projects beyond ONE (interior) face
    guide_w      =  90.0,   # bifold top track-guide block width
    guide_h      =  40.0,   # bifold top track-guide block height (tucks under the head)
    guide_proud  =  20.0,   # bifold/combo guide block proud of ONE face
    barn_batten_h =  90.0,  # barn-door horizontal ledger batten height (proud of the front face)
    barn_strap_w  =  44.0,  # barn-door vertical strap-hanger width
    casing_band  =  90.0,   # cased-opening architrave band face width (wider than the lining border)
    casing_reveal=  18.0,   # how far the casing laps onto the wall beyond the opening edge
    casing_proud =  16.0,   # cased-opening architrave through-wall thickness (proud of each face)
    # ── shower (semi-frameless) — its own sh_* namespace (no clashes) ──────────────────────────
    sh_jamb_w    =  28.0,   # slim pivot-side wall jamb face width
    sh_sill_h    =  32.0,   # low threshold / water-dam bar height
    sh_glaze_thk =  10.0,   # tempered shower-glass lite thickness
    sh_edge_gap  =   6.0,   # clearance between glass leading edge and the latch-side opening edge
    sh_top_gap   =  10.0,   # clearance between glass top and the head (open at top)
    sh_hinge_w   =  70.0,   # shower pivot hinge-block face width
    sh_hinge_h   =  55.0,   # shower pivot hinge-block face height
    sh_hinge_proud = 18.0,  # shower hinge-block proud of EACH glass face
    sh_pull_w    =  30.0,   # shower towel-bar / ladder-pull bar face width
    sh_pull_len  = 900.0,   # shower ladder-pull bar length (along height)
    sh_pull_thk  =  26.0,   # shower ladder-pull bar through-wall thickness
    sh_pull_edge =  70.0,   # shower pull inset from the latch (leading) edge
    sh_pull_standoff = 28.0,# shower pull stand-off from the glass face (≤30mm cap)
    sh_standoff_h =  80.0,  # shower pull standoff-pad height
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


# ── role → colour bucket (SINGLE source of truth for BOTH the golden generator and the converter) ──
# Every (solid, role) the recipe emits is coloured by its role's bucket. "panel" is special: it is
# glass when the door is glazed, else an opaque wood slab — resolved by ``bucket_for(role, glazed)``.
# To add a new part role, map it here ONCE; both style sites pick it up.
#   frame  → painted frame/lining colour (lining, stiles, rails, mullions, muntins, casing, astragal)
#   slab   → opaque wood door body (leaf, plank)
#   metal  → dark hardware (handle, pull, knob, track, roller, hinge, floor guide, strap)
#   glass  → transparent glazing (explicit glass/lite roles; also "panel" when glazed)
_ROLE_BUCKET = {
    "frame": "frame", "mullion": "frame", "rail": "frame",
    "stile": "frame", "muntin": "frame", "casing": "frame", "astragal": "frame",
    "sill": "frame",
    "plank": "slab",
    "handle": "metal", "track": "metal", "roller": "metal",
    "hinge": "metal", "guide": "metal", "strap": "metal", "pull": "metal",
    "track_guide": "metal", "standoff": "metal",
    "glass": "glass", "lite": "glass",
}
# Roles that should take the GLASS style when the door is glazed (the converter keys on this set).
GLASS_ROLES = {"panel", "glass", "lite"}


def bucket_for(role, glazed=False):
    """Colour bucket for a part role: 'glass' | 'frame' | 'slab' | 'metal'.
    ``panel`` resolves to glass when glazed, else slab; everything else is looked up."""
    if role == "panel":
        return "glass" if glazed else "slab"
    return _ROLE_BUCKET.get(role, "frame")


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


def _hinge_stack(f, d, cx, inner_h, leaf_dp, center, depth_dir, width_dir, n=3, max_w=None):
    """``n`` butt-hinge knuckles stacked on a leaf's hinge-side edge at ``cx``, each proud of ONE
    face only (the swing side). Fixed count = ``n``; sizes clamped, positions clamped inside the
    leaf, so it never drops a solid. Returns [(solid, "hinge"), …]."""
    hw = min(d["hinge_w"], 0.8 * max_w) if max_w else d["hinge_w"]
    hh = _clamp(d["hinge_h"], 0.0, 0.22 * inner_h)
    hdp = leaf_dp + d["hinge_proud"]                       # proud of ONE face
    hc = tuple(np.asarray(center, float) + np.asarray(depth_dir, float) * (d["hinge_proud"] / 2.0))
    out = []
    for i in range(n):
        frac = 0.0 if n == 1 else (-0.34 + 0.68 * i / (n - 1))
        cy = _clamp(frac * inner_h, -inner_h / 2.0 + hh / 2.0, inner_h / 2.0 - hh / 2.0)
        out.append((_extrude_along(f, _rect(f, hw, hh, cx=cx, cy=cy),
                                   hc, depth_dir, width_dir, hdp), "hinge"))
    return out


def _panelled_leaf(f, d, lcx, leaf_w, leaf_h, leaf_dp, glazed, height,
                   center, depth_dir, width_dir, up_sign):
    """A rail-and-stile leaf centred at (lcx, 0): 2 stiles + 3 rails (top / lock / bottom) + 2
    recessed panels (upper / lower). Frame members are the full leaf depth; the panels are ~half
    depth and centred, so the stiles/rails stand proud and the panels read recessed (no hollow
    profile, no boolean cut). FIXED 7 solids — dims are clamped to stay positive, never dropped.
    Returns (items, stile_w) where ``stile_w`` (the clamped stile width) anchors the latch handle."""
    sw = _clamp(d["stile_w"], 0.0, 0.18 * leaf_w)
    th = _clamp(d["top_rail_h"], 0.0, 0.18 * leaf_h)
    bh = _clamp(d["bottom_rail_h"], 0.0, 0.22 * leaf_h)
    lh = _clamp(d["lock_rail_h"], 0.0, 0.18 * leaf_h)
    field_w  = leaf_w - 2.0 * sw
    panel_dp = 0.5 * leaf_dp                              # recessed panel: thinner than the frame
    items = []
    for sx in (-(leaf_w - sw) / 2.0, (leaf_w - sw) / 2.0):     # 2 stiles (full leaf height)
        items.append((_extrude_along(f, _rect(f, sw, leaf_h, cx=lcx + sx),
                                     center, depth_dir, width_dir, leaf_dp), "stile"))
    top_inner = leaf_h / 2.0 - th
    bot_inner = -leaf_h / 2.0 + bh
    lock_cy = _clamp(up_sign * (-height / 2.0 + d["handle_h"]),
                     bot_inner + lh, top_inner - lh)           # at handle height, between top/bottom
    items.append((_extrude_along(f, _rect(f, field_w, th, cx=lcx, cy=leaf_h / 2.0 - th / 2.0),
                                 center, depth_dir, width_dir, leaf_dp), "rail"))    # top rail
    items.append((_extrude_along(f, _rect(f, field_w, bh, cx=lcx, cy=-leaf_h / 2.0 + bh / 2.0),
                                 center, depth_dir, width_dir, leaf_dp), "rail"))    # bottom rail
    items.append((_extrude_along(f, _rect(f, field_w, lh, cx=lcx, cy=lock_cy),
                                 center, depth_dir, width_dir, leaf_dp), "rail"))    # lock rail
    up_cy = (top_inner + (lock_cy + lh / 2.0)) / 2.0
    up_h  = max(top_inner - (lock_cy + lh / 2.0), 0.02 * leaf_h)
    lo_cy = ((lock_cy - lh / 2.0) + bot_inner) / 2.0
    lo_h  = max((lock_cy - lh / 2.0) - bot_inner, 0.02 * leaf_h)
    for (pcy, ph) in ((up_cy, up_h), (lo_cy, lo_h)):           # 2 recessed panels
        items.append((_extrude_along(f, _rect(f, field_w, ph, cx=lcx, cy=pcy),
                                     center, depth_dir, width_dir, panel_dp), "panel"))
    return items, sw


def _muntin_grid(f, d, lcx, gw, gh, panel_th, height, center, depth_dir, width_dir, up_sign):
    """Divided-lite detail for a glazed leaf (French doors): a mid-height LOCK RAIL (in the glass
    plane) + an applied muntin grille (1 vertical bar full glass height + 2 horizontal bars full
    glass width at ±gh/6) standing slightly proud of both glass faces. FIXED 4 solids; slim bars
    clamped so the grille stays light on small lites. Lock rail = role 'rail', bars = role 'muntin'
    (both frame-coloured)."""
    mt   = _clamp(d["muntin_thk"], 0.0, 0.12 * gw)        # vertical-bar width
    mhz  = _clamp(d["muntin_thk"], 0.0, 0.08 * gh)        # horizontal-bar height
    mdp  = panel_th + 2.0 * d["muntin_proud"]             # proud of BOTH glass faces
    lh   = _clamp(d["glass_lock_rail_h"], 0.0, 0.25 * gh)
    lcy  = _clamp(up_sign * (-height / 2.0 + d["handle_h"]), -gh / 2.0 + lh, gh / 2.0 - lh)
    return [
        (_extrude_along(f, _rect(f, gw, lh, cx=lcx, cy=lcy),
                        center, depth_dir, width_dir, panel_th), "rail"),     # lock rail (glass plane)
        (_extrude_along(f, _rect(f, mt, gh, cx=lcx),
                        center, depth_dir, width_dir, mdp), "muntin"),         # vertical bar
        (_extrude_along(f, _rect(f, gw, mhz, cx=lcx, cy=+gh / 6.0),
                        center, depth_dir, width_dir, mdp), "muntin"),         # upper horizontal
        (_extrude_along(f, _rect(f, gw, mhz, cx=lcx, cy=-gh / 6.0),
                        center, depth_dir, width_dir, mdp), "muntin"),         # lower horizontal
    ]


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
    panelled   = bool(recipe.get("panelled", False))
    bifold     = bool(recipe.get("bifold", False))
    combo      = bool(recipe.get("combo", False))
    shower     = bool(recipe.get("shower", False))
    barn       = bool(recipe.get("barn", False))
    casing     = bool(recipe.get("casing", False))
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
    #    SKIPPED in pocket / shower / barn / casing modes (each builds its own lining: pocket frames
    #    only the opening half; shower is semi-frameless; barn hangs over the opening; casing uses a
    #    3-sided lining). Every other mode gets the standard 4-bar lining.
    if not pocket and not shower and not barn and not casing:
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
    leaf_depth = panel_th          # through-wall depth of a leaf (for hinge sizing); set per branch
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
    elif casing:
        # Cased opening (leafless): a 3-sided lining (head + 2 full-height jambs, no sill) PLUS an
        # architrave/casing trim band (head + 2 legs) on BOTH wall faces — a wider, thinner moulding
        # that frames the opening, butt-jointed (no mitres → axis-aligned). Fixed 9 solids.
        items += _border_bars(f, width, height, frame_thk, center, depth_dir, width_dir, depth, sill=False)
        cb  = _clamp(d["casing_band"], 0.0, 0.16 * min(width, height))
        cow = width + 2.0 * d["casing_reveal"]
        cpd = d["casing_proud"]
        leg_h = height - cb
        for sgn in (1.0, -1.0):                          # front + back wall faces
            cc = tuple(np.asarray(center, float) + np.asarray(depth_dir, float) * sgn * (depth / 2.0 + cpd / 2.0))
            items.append((_extrude_along(f, _rect(f, cow, cb, cx=0.0, cy=(height - cb) / 2.0),
                                         cc, depth_dir, width_dir, cpd), "casing"))     # head casing
            for lx in (-(cow - cb) / 2.0, (cow - cb) / 2.0):
                items.append((_extrude_along(f, _rect(f, cb, leg_h, cx=lx, cy=-cb / 2.0),
                                             cc, depth_dir, width_dir, cpd), "casing"))  # side legs
    elif shower:
        # Semi-frameless shower: NO 4-bar lining. A slim pivot JAMB (left) + a low THRESHOLD (full
        # width) + a large thin GLASS lite filling most of the opening + 3 pivot HINGE blocks + a tall
        # towel-bar PULL standing off ONE glass face on 2 STANDOFF pads. Fixed 9 solids.
        jamb_w  = _clamp(d["sh_jamb_w"], 0.0, 0.10 * width)
        sill_h  = _clamp(d["sh_sill_h"], 0.0, 0.06 * height)
        glz     = min(d["sh_glaze_thk"], 0.6 * depth)
        # threshold (full width, low) + pivot jamb (left, threshold→head)
        items.append((_extrude_along(f, _rect(f, width, sill_h, cx=0.0, cy=-height / 2.0 + sill_h / 2.0),
                                     center, depth_dir, width_dir, depth), "sill"))
        items.append((_extrude_along(f, _rect(f, jamb_w, height - sill_h,
                                     cx=-width / 2.0 + jamb_w / 2.0, cy=sill_h / 2.0),
                                     center, depth_dir, width_dir, depth), "frame"))
        # glass lite — between jamb and latch edge, seated on the threshold
        glass_w = width - jamb_w - d["sh_edge_gap"]
        glass_h = height - sill_h - d["sh_top_gap"]
        glass_cx = (jamb_w - d["sh_edge_gap"]) / 2.0
        glass_cy = (sill_h - d["sh_top_gap"]) / 2.0
        items.append((_extrude_along(f, _rect(f, glass_w, glass_h, cx=glass_cx, cy=glass_cy),
                                     center, depth_dir, width_dir, glz), "panel"))
        # 3 pivot hinge blocks straddling the jamb↔glass joint, proud of both glass faces
        hjx = -width / 2.0 + jamb_w
        hh  = min(d["sh_hinge_h"], 0.22 * glass_h)
        hdp = glz + 2.0 * d["sh_hinge_proud"]
        for fr in (0.34, 0.0, -0.34):
            cy = _clamp(glass_cy + fr * glass_h, glass_cy - glass_h / 2.0 + hh / 2.0,
                        glass_cy + glass_h / 2.0 - hh / 2.0)
            items.append((_extrude_along(f, _rect(f, d["sh_hinge_w"], hh, cx=hjx, cy=cy),
                                         center, depth_dir, width_dir, hdp), "hinge"))
        # towel-bar pull on the latch side, standing off the front glass face on 2 standoff pads
        pull_len = min(d["sh_pull_len"], 0.55 * height)
        pull_cx  = width / 2.0 - d["sh_pull_edge"] - d["sh_pull_w"] / 2.0
        pull_c   = tuple(np.asarray(center, float) + np.asarray(depth_dir, float)
                         * (glz / 2.0 + d["sh_pull_standoff"] + d["sh_pull_thk"] / 2.0))
        items.append((_extrude_along(f, _rect(f, d["sh_pull_w"], pull_len, cx=pull_cx, cy=glass_cy),
                                     pull_c, depth_dir, width_dir, d["sh_pull_thk"]), "handle"))
        so_c = tuple(np.asarray(center, float) + np.asarray(depth_dir, float) * (glz / 2.0 + d["sh_pull_standoff"] / 2.0))
        soh  = min(d["sh_standoff_h"], 0.30 * pull_len)
        for sfr in (0.5, -0.5):
            items.append((_extrude_along(f, _rect(f, d["sh_pull_w"], soh,
                                         cx=pull_cx, cy=glass_cy + sfr * (pull_len - soh)),
                                         so_c, depth_dir, width_dir, d["sh_pull_standoff"]), "standoff"))
    elif n > 0 and barn:
        # Barn door: ledged plank leaf/leaves (slab + 3 horizontal ledger battens, proud of the
        # front — NO diagonal brace, axis-aligned only) hung from an overhead TRACK band (+2 end
        # stops) by 2 vertical STRAP hangers per leaf, with a floor GUIDE and a bar PULL. No lining.
        # Fixed 7 solids per leaf + 4 shared (track + 2 stops + guide). Authors its own pull.
        track_thk = _clamp(d["rail_thk"], 0.0, 0.12 * height)
        body_h, body_cy = height - track_thk, -track_thk / 2.0
        body_top = height / 2.0 - track_thk
        bp = 1.8 * d["hw_proud"]                          # face stand-off of the surface-mounted barn parts
        def front_c(dp):
            return tuple(np.asarray(center, float) + np.asarray(depth_dir, float) * (panel_th / 2.0 + dp / 2.0))
        leaf_w  = width / n
        bat_h   = _clamp(d["barn_batten_h"], 0.0, 0.16 * body_h)
        for i in range(n):
            lcx = -width / 2.0 + leaf_w * (i + 0.5)
            items.append((_extrude_along(f, _rect(f, leaf_w, body_h, cx=lcx, cy=body_cy),
                                         center, depth_dir, width_dir, panel_th), "plank"))   # plank body
            for fr in (0.38, 0.0, -0.38):                 # 3 ledger battens, proud of the front
                cy = _clamp(body_cy + fr * body_h, body_cy - body_h / 2.0 + bat_h / 2.0,
                            body_cy + body_h / 2.0 - bat_h / 2.0)
                items.append((_extrude_along(f, _rect(f, leaf_w, bat_h, cx=lcx, cy=cy),
                                             front_c(bp), depth_dir, width_dir, bp), "plank"))
            strap_bot = body_top - 0.12 * body_h          # 2 strap hangers (door top → track)
            strap_h, strap_cy = height / 2.0 - strap_bot, (height / 2.0 + strap_bot) / 2.0
            for sx in (lcx - leaf_w / 4.0, lcx + leaf_w / 4.0):
                items.append((_extrude_along(f, _rect(f, d["barn_strap_w"], strap_h, cx=sx, cy=strap_cy),
                                             front_c(bp), depth_dir, width_dir, bp), "strap"))
            pt = min(d["pull_thk"], 0.4 * leaf_w)         # bar pull on the leading edge
            items.append((_extrude_along(f, _rect(f, pt, d["pull_len"],
                                         cx=lcx + leaf_w / 2.0 - d["edge_inset"] - pt / 2.0, cy=body_cy),
                                         center, depth_dir, width_dir, panel_th + 2.0 * d["hw_proud"]), "pull"))
        items.append((_extrude_along(f, _rect(f, width, track_thk, cx=0.0, cy=height / 2.0 - track_thk / 2.0),
                                     front_c(bp), depth_dir, width_dir, bp), "track"))   # overhead track band
        for ex in (-width / 2.0 + track_thk / 2.0, width / 2.0 - track_thk / 2.0):       # 2 end stops
            items.append((_extrude_along(f, _rect(f, track_thk, track_thk * 1.6, cx=ex, cy=height / 2.0 - track_thk * 0.8),
                                         front_c(bp * 1.2), depth_dir, width_dir, bp * 1.2), "track"))
        ghg = _clamp(d["guide_h"], 0.0, 0.05 * height)                                    # floor guide
        items.append((_extrude_along(f, _rect(f, leaf_w * 0.5, ghg, cx=0.0, cy=-height / 2.0 + ghg / 2.0),
                                     center, depth_dir, width_dir, panel_th + 2.0 * d["hw_proud"]), "guide"))
    elif n > 0 and combo:
        # Differentiated slide+swing combo: a central divider, a framed SLIDING SASH on the left
        # (track + 2 rollers + floor guide + bar pull, on a front depth track) and a panelled SWING
        # leaf on the right (rails + recessed panels + knob + 2 hinges). Authors its own hardware
        # (handle='none' in the recipe), so the generic handle/hinge steps stay no-ops. Fixed 25 solids.
        div_thk  = _clamp(d["bar_thk"], 0.0, 0.3 * inner_w)
        half_w   = (inner_w - div_thk) / 2.0
        left_cx  = -(div_thk / 2.0 + half_w / 2.0)
        right_cx =  (div_thk / 2.0 + half_w / 2.0)
        items.append((_extrude_along(f, _rect(f, div_thk, inner_h, cx=0.0),
                                     center, depth_dir, width_dir, depth), "mullion"))
        # ── left: sliding sash on a front depth track ──
        sash_thk = _clamp(d["sash_frame_thk"], 0.0, 0.3 * half_w)
        sash_dp  = _clamp(d["sash_depth_ratio"] * depth, panel_th, 0.48 * depth)
        front_c  = tuple(np.asarray(center, float) + np.asarray(depth_dir, float) * (depth - sash_dp) / 2.0)
        items += _border_bars(f, half_w, inner_h, sash_thk, front_c, depth_dir, width_dir, sash_dp, cx=left_cx)
        items.append((_extrude_along(f, _rect(f, half_w - 2.0 * sash_thk, inner_h - 2.0 * sash_thk, cx=left_cx),
                                     front_c, depth_dir, width_dir, panel_th), "panel"))
        # Sliding hardware is FRONT-mounted (matches the front-track sash): each part spans from the
        # lining centre plane out to hw_proud beyond the FRONT face, so nothing pokes out the back.
        hw_dp = depth / 2.0 + d["hw_proud"]
        hw_c  = tuple(np.asarray(center, float) + np.asarray(depth_dir, float) * (hw_dp / 2.0))
        track_w = min(half_w * 1.05, 0.95 * width)
        track_y = inner_h / 2.0 + rail_thk / 2.0
        items.append((_extrude_along(f, _rect(f, track_w, rail_thk, cx=left_cx, cy=track_y),
                                     hw_c, depth_dir, width_dir, hw_dp), "track"))
        for rx in (left_cx - half_w / 2.0 + sash_thk, left_cx + half_w / 2.0 - sash_thk):
            items.append((_extrude_along(f, _rect(f, d["roller_w"], d["roller_h"], cx=rx, cy=track_y - d["roller_h"] / 2.0),
                                         hw_c, depth_dir, width_dir, hw_dp), "roller"))
        ghg = _clamp(d["guide_h"], 0.0, 0.1 * inner_h)
        items.append((_extrude_along(f, _rect(f, half_w, ghg, cx=left_cx, cy=-inner_h / 2.0 + ghg / 2.0),
                                     hw_c, depth_dir, width_dir, hw_dp), "guide"))
        pt = min(d["pull_thk"], 0.5 * half_w)
        px = _clamp(left_cx - half_w / 2.0 + sash_thk + d["edge_inset"] + pt / 2.0,
                    left_cx - half_w / 2.0 + pt / 2.0, left_cx + half_w / 2.0 - pt / 2.0)
        items.append((_extrude_along(f, _rect(f, pt, d["pull_len"], cx=px, cy=0.0),
                                     hw_c, depth_dir, width_dir, hw_dp), "pull"))
        # ── right: panelled swing leaf + knob + 2 hinges ──
        leaf_items, sw = _panelled_leaf(f, d, right_cx, half_w, inner_h, panel_th, False, height,
                                        center, depth_dir, width_dir, up_sign)
        items += leaf_items
        kw  = min(d["knob_w"], 0.5 * half_w)
        kcy = _clamp(up_sign * (-height / 2.0 + d["handle_h"]),
                     -inner_h / 2.0 + d["knob_h"], inner_h / 2.0 - d["knob_h"])
        items.append((_extrude_along(f, _rect(f, kw, d["knob_h"], cx=right_cx - half_w / 2.0 + sw / 2.0, cy=kcy),
                                     center, depth_dir, width_dir, depth + 2.0 * d["knob_proud"]), "handle"))
        items += _hinge_stack(f, d, right_cx + half_w / 2.0 - d["hinge_w"] / 2.0, inner_h, panel_th,
                              center, depth_dir, width_dir, n=2, max_w=sw)
    elif n > 0 and panelled:
        # Rail-and-stile PANELLED leaves (single + double swing): each leaf is a 7-solid joinery
        # frame (2 stiles + 3 rails + 2 recessed panels). Laid side by side; the abutting stiles
        # read as the centre divider on a double. The leaf is the slab thickness, centred in depth.
        leaf_w = inner_w / n
        for i in range(n):
            lcx = -inner_w / 2.0 + leaf_w * (i + 0.5)
            leaf_items, stile_thk = _panelled_leaf(f, d, lcx, leaf_w, inner_h, panel_th, glazed,
                                                   height, center, depth_dir, width_dir, up_sign)
            items += leaf_items
            panes.append((lcx, leaf_w))
    elif n > 0 and leaf_frame:
        # Each leaf = its own 4-bar stile/rail frame + inset infill (glass or slab), laid edge-to-edge.
        # With ``muntins`` (French interior doubles) each glazed lite also gets a lock rail + applied
        # divided-lite grille. The leaf frame spans the full lining depth → hinges use that depth.
        stile_thk = _clamp(d["leaf_frame_thk"], 0.0, 0.35 * (inner_w / n))
        leaf_depth = depth
        muntins = bool(recipe.get("muntins"))
        leaf_w = inner_w / n
        for i in range(n):
            lcx = -inner_w / 2.0 + leaf_w * (i + 0.5)
            items += _border_bars(f, leaf_w, inner_h, stile_thk,
                                  center, depth_dir, width_dir, depth, cx=lcx)
            gw = leaf_w - 2.0 * stile_thk
            gh = inner_h - 2.0 * stile_thk
            items.append((_extrude_along(f, _rect(f, gw, gh, cx=lcx),
                                         center, depth_dir, width_dir, panel_th), "panel"))
            if muntins:
                items += _muntin_grid(f, d, lcx, gw, gh, panel_th, height,
                                      center, depth_dir, width_dir, up_sign)
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

    # 4b) Bifold folding hardware — what makes the (coplanar, leaf-framed) leaves read as a folding
    #     door: a pair of hinge knuckles (upper + lower) straddling each leaf-to-leaf joint, plus one
    #     top track-guide block riding under the head over the active leaf. All proud of ONE face.
    if bifold and n >= 2:
        leaf_w = inner_w / n
        hh  = _clamp(d["hinge_h"], 0.0, 0.22 * inner_h)
        hdp = depth + d["hinge_proud"]
        hc  = tuple(np.asarray(center, float) + np.asarray(depth_dir, float) * (d["hinge_proud"] / 2.0))
        for j in range(1, n):                              # interior joints
            jx = -inner_w / 2.0 + leaf_w * j
            for fr in (0.30, -0.30):                       # upper + lower knuckle
                cy = _clamp(fr * inner_h, -inner_h / 2.0 + hh / 2.0, inner_h / 2.0 - hh / 2.0)
                items.append((_extrude_along(f, _rect(f, d["hinge_w"], hh, cx=jx, cy=cy),
                                             hc, depth_dir, width_dir, hdp), "hinge"))
        gw  = _clamp(d["guide_w"], 0.0, 0.5 * leaf_w)
        gh  = _clamp(d["guide_h"], 0.0, 0.5 * frame_thk)
        gdp = depth + d["guide_proud"]
        gc  = tuple(np.asarray(center, float) + np.asarray(depth_dir, float) * (d["guide_proud"] / 2.0))
        gx  = -inner_w / 2.0 + leaf_w * (n - 1 + 0.5)      # active (rightmost) leaf centre
        items.append((_extrude_along(f, _rect(f, gw, gh, cx=gx, cy=inner_h / 2.0 - gh / 2.0),
                                     gc, depth_dir, width_dir, gdp), "track_guide"))

    # 5) Astragal — a cover bar over the meeting joint of a double, proud of one (interior) face.
    if recipe.get("astragal") and n >= 2 and panes:
        aw  = _clamp(d["astragal_w"], 0.0, 0.12 * (inner_w / n))
        adp = depth + d["astragal_proud"]                # proud of ONE face
        ac  = tuple(np.asarray(center, float) + np.asarray(depth_dir, float) * (d["astragal_proud"] / 2.0))
        items.append((_extrude_along(f, _rect(f, aw, inner_h, cx=0.0),
                                     ac, depth_dir, width_dir, adp), "astragal"))

    # 6) Hinges — 3 butt-hinge knuckles on each leaf's OUTER (hinge-side) edge, proud of one face.
    #    Makes a swing leaf read as a hung door. ``hinges`` opt-in per recipe; uses the leaf list in
    #    ``panes`` so it works for the panelled single/double, the flush slab, and the French leaves.
    if recipe.get("hinges") and panes:
        for (lcx, lpw) in panes:
            side = -1.0 if lcx <= 1e-9 else 1.0          # hinge on the OUTER edge (away from centre)
            hx = lcx + side * (lpw / 2.0 - d["hinge_w"] / 2.0)
            items += _hinge_stack(f, d, hx, inner_h, leaf_depth, center, depth_dir, width_dir,
                                  n=3, max_w=(stile_thk if stile_thk else lpw))

    # 7) Canonical handle(s). (Pocket mode authored its own leading-edge pull above.)
    if not pocket:
        items += _build_handles(f, recipe, d, inner_h, height, depth, bar_thk, panes,
                                center, depth_dir, width_dir, stile_thk=stile_thk, up_sign=up_sign)

    return items
