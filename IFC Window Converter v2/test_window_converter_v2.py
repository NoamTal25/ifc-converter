#!/usr/bin/env python3
"""
test_window_converter_v2.py — acceptance/regression tester for the v2 window converter.

Mirrors the v1 tester (and Gal's methodology): analytical, **no Blender**. A converted window
is manipulable because its geometry is now clean parametric rectangular swept solids, and that
is tested far more reliably in code than by eye. The harness, per fixture:

  - runs ``convert(src, tmp)`` into a THROWAWAY temp,
  - re-derives every invariant from scratch (does NOT call the converter's verify()),
  - and MANIPULATES windows (parametric resize via the shared recipe, plus per-instance move /
    rotate), asserting it behaves well (frame border constant, grows along the driven axis,
    moves rigidly, stays valid).

Generalised for v2's golden topologies: a rebuilt window is **a 4-bar IfcRectangleProfileDef
lining + ≥1 inset IfcRectangleProfileDef pane (+ optional mullion/transom bar)**, all styled,
with NO IfcRectangleHollowProfileDef (Gaudi mis-renders the hollow profile — §6). Single,
double-horizontal (vertical mullion) and double-vertical / double-hung (horizontal transom)
all satisfy this. The parametric-resize check drives the shared recipe directly (the function
that authored every rebuilt window) rather than mutating one profile.

Teeth / negative control: the SAME manipulable-state test on the ORIGINAL baked windows MUST
fail (a brep has no drivable width parameter), and the converter must rebuild exactly the
pinned baseline count per fixture — so a no-op / silent regression cannot pass.

Run with python3.11 (ifcopenshell 0.8.5):
    python3.11 test_window_converter_v2.py            # all fixtures in INPUT_IFC_FILES_HERE/
    python3.11 test_window_converter_v2.py -v         # verbose
    python3.11 test_window_converter_v2.py <file.ifc> # one fixture
"""
import hashlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import ifcopenshell
import ifcopenshell.util.placement as ifc_placement
import ifcopenshell.validate as ifc_validate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONVERTER = HERE / "IFC_window_converter_V2.py"
FIXTURE_DIRS = [ROOT / "INPUT_IFC_FILES_HERE"]


def _load_converter():
    spec = importlib.util.spec_from_file_location("winconv2", CONVERTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


WC = _load_converter()
MARK = WC.MARK


# ── tiny assertion framework ──────────────────────────────────────────────────────
class Checker:
    def __init__(self, name, verbose=False):
        self.name = name
        self.verbose = verbose
        self.fails = []
        self.n = 0

    def check(self, cond, label):
        self.n += 1
        if cond:
            if self.verbose:
                print(f"    ok   {label}")
        else:
            self.fails.append(label)
            print(f"    FAIL {label}")
        return bool(cond)

    @property
    def ok(self):
        return not self.fails


# ── helpers (re-derived independently of the converter) ─────────────────────────────
def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _silent_convert(src, out):
    with redirect_stdout(io.StringIO()):
        WC.convert(str(src), str(out))


def _nerr(model):
    log = ifc_validate.json_logger()
    ifc_validate.validate(model, log)
    return len(log.statements)


def _marked(model):
    return [w for w in model.by_type("IfcWindow") if (w.Description or "") == MARK]


def _body(w):
    """The window's 'Body' IfcShapeRepresentation, or None."""
    rep = w.Representation
    if not rep:
        return None
    for r in (rep.Representations or []):
        if r.RepresentationIdentifier == "Body":
            return r
    return None


def _ax3_matrix(p):
    """IfcAxis2Placement3D → 4×4 (kernel-free; immune to the geom iterator's nondeterministic
    empty tessellation on freshly-authored solids — §6)."""
    loc = np.array(p.Location.Coordinates, float)
    z = np.array(p.Axis.DirectionRatios, float) if p.Axis else np.array([0, 0, 1.0])
    x = np.array(p.RefDirection.DirectionRatios, float) if p.RefDirection else np.array([1, 0, 0.0])
    z = z / np.linalg.norm(z)
    x = x - np.dot(x, z) * z; x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    M = np.eye(4); M[:3, 0] = x; M[:3, 1] = y; M[:3, 2] = z; M[:3, 3] = loc
    return M


def _solid_corners(solid):
    """8 corners of a rectangle/hollow-profile IfcExtrudedAreaSolid, in the representation frame."""
    pr = solid.SweptArea
    xd, yd = pr.XDim, pr.YDim
    cx, cy = (pr.Position.Location.Coordinates if pr.Position else (0.0, 0.0))
    depth = solid.Depth
    M = _ax3_matrix(solid.Position)
    pts = []
    for sx in (-0.5, 0.5):
        for sy in (-0.5, 0.5):
            for sz in (0.0, 1.0):
                pts.append((M @ np.array([cx + sx * xd, cy + sy * yd, sz * depth, 1.0]))[:3])
    return np.array(pts)


def _solids(w):
    """All IfcExtrudedAreaSolid items of the Body, or [] (mapped/baked → none)."""
    b = _body(w)
    if not b:
        return []
    items = b.Items or []
    if any(i.is_a("IfcMappedItem") for i in items):
        return []
    return [i for i in items if i.is_a("IfcExtrudedAreaSolid")]


def _window_local_bbox(w):
    pts = np.vstack([_solid_corners(s) for s in _solids(w)])
    return pts.min(0), pts.max(0)


def _window_world_bbox(w):
    M = ifc_placement.get_local_placement(w.ObjectPlacement)
    mn, mx = _window_local_bbox(w)
    corners = np.array([[x, y, z, 1.0] for x in (mn[0], mx[0])
                        for y in (mn[1], mx[1]) for z in (mn[2], mx[2])])
    wc = (M @ corners.T).T[:, :3]
    return wc.min(0), wc.max(0)


def _solid_local_bbox(solid):
    pts = _solid_corners(solid)
    return pts.min(0), pts.max(0)


def _is_manipulable(model, w):
    """True iff w is in the clean, parameter-drivable state the converter produces: its Body is ≥5
    plain rectangular swept solids — a 4-bar lining + ≥1 inset pane (+ optional mullion/transom) —
    with NO IfcRectangleHollowProfileDef (Gaudi mis-renders it, §6), every item styled, and at least
    one solid strictly inset on the face plane (a glazing pane within the lining border). Baked /
    mapped windows have no rect swept solids → fail (the test's teeth)."""
    solids = _solids(w)
    if len(solids) < 5:
        return False
    for s in solids:
        pr = s.SweptArea
        # reject hollow profiles (test hollow FIRST — it IS-A IfcRectangleProfileDef, §6) + non-rects
        if pr.is_a("IfcRectangleHollowProfileDef") or not pr.is_a("IfcRectangleProfileDef"):
            return False
    styled = {st.Item for st in model.by_type("IfcStyledItem")}
    if any(s not in styled for s in solids):
        return False
    mn, mx = _window_local_bbox(w)
    face = [i for i in range(3) if i != int(np.argmin(mx - mn))]   # two face-plane axes
    for s in solids:
        smn, smx = _solid_local_bbox(s)
        if all(smn[i] > mn[i] + 1e-6 and smx[i] < mx[i] - 1e-6 for i in face):
            return True                                            # a pane inset within the border
    return False


# ══════════════════════════════════════════════════════════════════════════════
# Invariant layers
# ══════════════════════════════════════════════════════════════════════════════
PRESERVE = [
    "IfcWindow", "IfcWall", "IfcWallStandardCase", "IfcSlab", "IfcRoof", "IfcDoor",
    "IfcOpeningElement", "IfcSpace", "IfcBuildingStorey", "IfcCovering",
    "IfcFurnishingElement", "IfcBuildingElementProxy",
    "IfcRelFillsElement", "IfcRelVoidsElement", "IfcRelContainedInSpatialStructure",
    "IfcRelDefinesByType", "IfcRelAggregates",   # converter never mints a type → count invariant
]


def layer_A_conservation(c, before, after):
    for t in PRESERVE:
        nb, na = len(before.by_type(t)), len(after.by_type(t))
        if nb == 0 and na == 0:
            continue
        c.check(nb == na, f"[A] count {t} unchanged ({nb}->{na})")
    gb = sorted(w.GlobalId for w in before.by_type("IfcWindow"))
    ga = sorted(w.GlobalId for w in after.by_type("IfcWindow"))
    c.check(gb == ga, "[A] window GlobalId multiset identical")
    eb = sum(len(before.by_type(t)) for t in ("IfcRelFillsElement", "IfcRelVoidsElement"))
    ea = sum(len(after.by_type(t)) for t in ("IfcRelFillsElement", "IfcRelVoidsElement"))
    c.check(eb == ea, "[A] fill/void relationship edges identical")


def layer_B_preservation(c, before, after, src, sha0, mtime0):
    bpl = {o.GlobalId: ifc_placement.get_local_placement(o.ObjectPlacement)
           for o in before.by_type("IfcOpeningElement") if o.ObjectPlacement}
    moved = 0
    for o in after.by_type("IfcOpeningElement"):
        m0 = bpl.get(o.GlobalId)
        if m0 is not None and o.ObjectPlacement is not None:
            if not np.allclose(m0, ifc_placement.get_local_placement(o.ObjectPlacement), atol=1e-6):
                moved += 1
    c.check(moved == 0, f"[B] openings unmoved ({moved} moved)")

    bpl = {w.GlobalId: ifc_placement.get_local_placement(w.ObjectPlacement)
           for w in before.by_type("IfcWindow") if w.ObjectPlacement}
    bad = 0
    for w in _marked(after):
        m0 = bpl.get(w.GlobalId)
        if m0 is None or w.ObjectPlacement is None or \
           not np.allclose(m0, ifc_placement.get_local_placement(w.ObjectPlacement), atol=1e-9):
            bad += 1
    c.check(bad == 0, f"[B] rebuilt windows keep original placement ({bad} drifted)")

    # FootPrint preserved where the original had one (we swap only Body).
    fp_before = {w.GlobalId for w in before.by_type("IfcWindow")
                 if w.Representation and any(r.RepresentationIdentifier == "FootPrint"
                                             for r in w.Representation.Representations)}
    fp_lost = 0
    for w in after.by_type("IfcWindow"):
        if w.GlobalId in fp_before:
            has = w.Representation and any(r.RepresentationIdentifier == "FootPrint"
                                          for r in w.Representation.Representations)
            if not has:
                fp_lost += 1
    c.check(fp_lost == 0, f"[B] FootPrint representation preserved ({fp_lost} lost)")

    c.check(_sha(src) == sha0 and os.path.getmtime(src) == mtime0, "[B] source file untouched")


def layer_C_manipulable_state(c, after):
    marked = _marked(after)
    for w in marked:
        c.check(_is_manipulable(after, w), f"[C] manipulable state: {w.Name!r}")
    return marked


def _recipe_window_items(split, width, height):
    """Build one window via the SHARED recipe (the same code that authored every rebuilt window),
    axis-aligned (width=X, height=Y, depth=Z), in a throwaway file."""
    f = ifcopenshell.file(schema="IFC4")
    return WC.gg.build_window_items(
        f, width, height, WC.gg.LINING_DEPTH,
        frame_thk=WC.gg.LINING_THK, glaze_thk=WC.gg.GLAZE_THK, bar_thk=WC.gg.BAR_THK,
        split=split, center=(0.0, 0.0, WC.gg.LINING_DEPTH / 2.0),
        depth_dir=(0, 0, 1), width_dir=(1, 0, 0))


def _frame_inner_extents(items):
    """(outer_ext, inner_ext) as (X, Y, Z) arrays: outer = bbox of the 'frame' bars, inner = bbox
    of the 'pane' solids (None if leafless)."""
    fo = np.vstack([_solid_corners(s) for s, r in items if r == "frame"])
    outer = fo.max(0) - fo.min(0)
    panes = [s for s, r in items if r == "pane"]
    inner = None
    if panes:
        pi = np.vstack([_solid_corners(s) for s in panes])
        inner = pi.max(0) - pi.min(0)
    return outer, inner


def _parametric_resize_check(c, split, axis, label):
    """Drive the SHARED recipe 1.5x along one face axis (0=width/X, 1=height/Y) and confirm the
    lining grows on that axis ONLY, with the frame border held constant — the real 'make it wider'.
    The frame is now 4 bars, so this tests the generative function (which authored every rebuilt
    window) rather than mutating a single hollow profile."""
    base_w, base_h = 1200.0, 1500.0
    o0, i0 = _frame_inner_extents(_recipe_window_items(split, base_w, base_h))
    o1, i1 = _frame_inner_extents(_recipe_window_items(
        split, base_w * (1.5 if axis == 0 else 1.0), base_h * (1.5 if axis == 1 else 1.0)))
    other = 1 - axis
    c.check(abs(o1[axis] / o0[axis] - 1.5) < 0.02, f"[D-{label}] lining grew ~1.5x")
    c.check(abs(o1[other] - o0[other]) < 1e-6 * max(1.0, o0[other]),
            f"[D-{label}] other axis unchanged")
    if i0 is not None and i1 is not None:
        b0 = (o0[axis] - i0[axis]) / 2.0
        b1 = (o1[axis] - i1[axis]) / 2.0
        c.check(abs(b1 - b0) < 1e-6 * max(1.0, b0), f"[D-{label}] frame border constant")


def layer_D_manipulate(c, out, base_err):
    # PARAMETRIC RESIZE — exercise the shared recipe (it authored every rebuilt window) along each
    # face axis, for single + both split topologies. Border held constant = real 'make it wider'.
    for split in (None, "V", "H"):
        tag = split or "single"
        _parametric_resize_check(c, split, 0, f"width/{tag}")
        _parametric_resize_check(c, split, 1, f"height/{tag}")

    # MOVE — translate placement; window shifts rigidly (size preserved, GlobalId intact)
    m = ifcopenshell.open(str(out))
    for w in _marked(m):
        wb0 = _window_world_bbox(w)
        size0 = wb0[1] - wb0[0]; ctr0 = (wb0[0] + wb0[1]) / 2
        gid = w.GlobalId
        pl = w.ObjectPlacement.RelativePlacement
        loc = list(pl.Location.Coordinates)
        pl.Location = m.create_entity("IfcCartesianPoint",
                                      Coordinates=(loc[0] + 1.0, loc[1], loc[2]))
        wb1 = _window_world_bbox(w); size1 = wb1[1] - wb1[0]; ctr1 = (wb1[0] + wb1[1]) / 2
        c.check(np.allclose(size1, size0, atol=1e-4), f"[D-move] size preserved (rigid): {w.Name!r}")
        c.check(np.linalg.norm(ctr1 - ctr0) > 1e-6, f"[D-move] window moved: {w.Name!r}")
        c.check(w.GlobalId == gid, f"[D-move] GlobalId intact: {w.Name!r}")
    c.check(_nerr(m) <= base_err, "[D-move] no new validate errors")

    # ROTATE — 90° about the placement's own Axis; geometry untouched, must stay valid.
    m = ifcopenshell.open(str(out))
    for w in _marked(m):
        body = _body(w)
        rep_items0 = len(body.Items or [])
        gid = w.GlobalId
        pl = w.ObjectPlacement.RelativePlacement
        ax = np.array(pl.Axis.DirectionRatios if pl.Axis else (0.0, 0.0, 1.0))
        rd = np.array(pl.RefDirection.DirectionRatios if pl.RefDirection else (1.0, 0.0, 0.0))
        nrd = np.cross(ax, rd)
        if np.linalg.norm(nrd) < 1e-9:
            continue
        pl.RefDirection = m.create_entity("IfcDirection", DirectionRatios=tuple(map(float, nrd)))
        c.check(len(_body(w).Items or []) == rep_items0, f"[D-rotate] geometry untouched: {w.Name!r}")
        c.check(w.GlobalId == gid, f"[D-rotate] GlobalId intact: {w.Name!r}")
    c.check(_nerr(m) <= base_err, "[D-rotate] no new validate errors")


def layer_E_idempotency(c, out):
    out2 = str(out) + ".rerun.ifc"
    _silent_convert(out, out2)
    m1 = ifcopenshell.open(str(out)); m2 = ifcopenshell.open(out2)
    a = {w.GlobalId: len(_body(w).Items) for w in _marked(m1)}
    b = {w.GlobalId: len(_body(w).Items) for w in _marked(m2)}
    c.check(a == b and len(_marked(m2)) == len(_marked(m1)),
            "[E] idempotent — re-run leaves rebuilt windows unchanged")
    try:
        os.remove(out2)
    except OSError:
        pass


# Pinned per-fixture rebuilt counts. A no-op / silent regression must trip this.
BASELINE_REBUILT = {
    "LEXFORD_OFFICE-C1.ifc": 6,                   # 7 windows, 1 trapezoid gated
    "SAN_JUAN_CYPRESS_-_AUG_2-W1-L1.ifc": 5,      # 6 windows, 1 skylight gated
    "Sunflower_Sunflower_A_Sunflower_A_.ifc": 4,  # 5 windows, 1 bodiless 'Square Opening' skipped
    "Turnberry_927_TURNBERRY_ADU-DEC_2_2025-C1.ifc": 8,   # 6 single + 2 double-horizontal
}


def layer_F_negative_control(c, fixture_name, before, after):
    n_before = sum(1 for w in before.by_type("IfcWindow") if _is_manipulable(before, w))
    n_after = sum(1 for w in after.by_type("IfcWindow") if _is_manipulable(after, w))
    n_marked = len(_marked(after))
    c.check(n_before == 0,
            f"[F] no ORIGINAL window was already manipulable — test has teeth ({n_before} were)")
    c.check(n_after == n_marked, f"[F] every rebuilt window is manipulable ({n_after}/{n_marked})")
    expected = BASELINE_REBUILT.get(fixture_name)
    if expected is not None:
        c.check(n_marked == expected,
                f"[F] rebuilt count matches baseline ({n_marked} == {expected})")
    else:
        c.check(n_marked > 0, f"[F] converter rebuilt at least one window (not a no-op) [{n_marked}]")


# ══════════════════════════════════════════════════════════════════════════════
def test_fixture(path, verbose):
    c = Checker(path.name, verbose)
    print(f"\n{'='*78}\n{path.name}\n{'='*78}")
    sha0 = _sha(path); mt0 = os.path.getmtime(path)
    tmpdir = tempfile.mkdtemp(prefix="winconv2_test_")
    try:
        out = Path(tmpdir) / f"{path.stem}-WIN2.ifc"
        _silent_convert(path, out)

        before = ifcopenshell.open(str(path))
        after = ifcopenshell.open(str(out))
        base_err = _nerr(after)
        marked = _marked(after)
        print(f"  windows: {len(after.by_type('IfcWindow'))} total | "
              f"{len(marked)} rebuilt | schema {after.schema}")

        layer_A_conservation(c, before, after)
        layer_B_preservation(c, before, after, str(path), sha0, mt0)
        layer_C_manipulable_state(c, after)
        layer_D_manipulate(c, out, base_err)
        layer_E_idempotency(c, out)
        layer_F_negative_control(c, path.name, before, after)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"  → {'PASS' if c.ok else 'FAIL'} ({c.n} checks, {len(c.fails)} failed)")
    return c


def _fixtures(args):
    if args:
        return [Path(a) for a in args]
    out, seen = [], set()
    for d in FIXTURE_DIRS:
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.ifc")):
            if p.stem.endswith(WC.SUFFIX) or p.name in seen:
                continue
            seen.add(p.name)
            out.append(p)
    return out


def main():
    verbose = "-v" in sys.argv
    args = [a for a in sys.argv[1:] if a != "-v"]
    try:
        _ = ifcopenshell.version
    except Exception as e:
        print(f"PREFLIGHT FAIL: ifcopenshell not importable ({e!r}) — run with python3.11")
        sys.exit(2)

    fixtures = _fixtures(args)
    if not fixtures:
        print("No fixtures found in INPUT_IFC_FILES_HERE/")
        sys.exit(1)

    results = [test_fixture(p, verbose) for p in fixtures]

    print(f"\n{'='*78}\nWINDOW CONVERTER v2 — TEST REPORT (ifcopenshell {ifcopenshell.version})\n{'='*78}")
    npass = sum(1 for r in results if r.ok)
    for r in results:
        tag = "PASS" if r.ok else "FAIL"
        detail = "" if r.ok else "  — " + "; ".join(r.fails[:3]) + ("…" if len(r.fails) > 3 else "")
        print(f"  {tag}  {r.name}{detail}")
    print(f"\nRESULT: {'ALL PASS' if npass == len(results) else 'FAIL'}  ({npass}/{len(results)} fixtures)")
    sys.exit(0 if npass == len(results) else 1)


if __name__ == "__main__":
    main()
