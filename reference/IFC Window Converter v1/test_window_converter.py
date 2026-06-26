#!/usr/bin/env python3
"""
test_window_converter.py — acceptance/regression tester for the IFC window converter.

Mirrors Gal's testing methodology (test_levels_organizer.py + FLOORS_DEFINER_TESTING_AGENT.md):
analytical, **no Blender** — a converted window is manipulable because its geometry is now a
clean parametric profile (`IfcRectangleHollowProfileDef.XDim` / `WallThickness`), and that is
tested far more reliably in code than by eye in a viewer. The harness:

  - runs `convert(src, tmp)` into a THROWAWAY temp (never pollutes OUTPUT_IFC_FILES_HERE/),
  - re-derives every invariant from scratch (does NOT call the converter's own verify()),
  - and — the window-specific part — actually MANIPULATES each rebuilt window (parametric
    resize / move / rotate) and asserts it behaves well (frame border stays constant, window
    moves rigidly, geometry stays valid).

Teeth / negative control: the same "is this window manipulable?" test is run on the ORIGINAL
(pre-conversion, baked) windows — they MUST fail. A brep has no drivable width parameter, so
this single before/after contrast both proves the tests have teeth and proves the converter's
whole purpose.

Run with python3.11 (ifcopenshell 0.8.5):
    python3.11 test_window_converter.py            # all fixtures in INPUT_IFC_FILES_HERE/
    python3.11 test_window_converter.py -v         # verbose: show every passing assertion
    python3.11 test_window_converter.py <file.ifc> # one fixture
Exits non-zero if any fixture fails any layer.
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
ROOT = HERE.parent.parent      # repo root (this converter now lives under reference/)
CONVERTER = HERE / "IFC_window_converter_V1.py"
FIXTURE_DIRS = [ROOT / "INPUT_IFC_FILES_HERE"]

def _load_converter():
    spec = importlib.util.spec_from_file_location("winconv", CONVERTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


WC = _load_converter()
MARK = WC.MARK


# ── tiny assertion framework (collects failures per file instead of aborting) ──
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


# ── geometry / structure helpers (re-derived independently of the converter) ──
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


def _ax3_matrix(p):
    """IfcAxis2Placement3D → 4×4 (kernel-free, so it's immune to the geom iterator's
    nondeterministic empty-tessellation on freshly-authored solids)."""
    loc = np.array(p.Location.Coordinates, float)
    z = np.array(p.Axis.DirectionRatios, float) if p.Axis else np.array([0, 0, 1.0])
    x = np.array(p.RefDirection.DirectionRatios, float) if p.RefDirection else np.array([1, 0, 0.0])
    z = z / np.linalg.norm(z)
    x = x - np.dot(x, z) * z; x = x / np.linalg.norm(x)
    y = np.cross(z, x)
    M = np.eye(4); M[:3, 0] = x; M[:3, 1] = y; M[:3, 2] = z; M[:3, 3] = loc
    return M


def _solid_corners(solid):
    """8 corners of a rectangle-profile IfcExtrudedAreaSolid, in the representation frame."""
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


def _window_local_bbox(w):
    pts = np.vstack([_solid_corners(s) for s in w.Representation.Representations[0].Items
                     if s.is_a("IfcExtrudedAreaSolid")])
    return pts.min(0), pts.max(0)


def _window_world_bbox(w):
    M = ifc_placement.get_local_placement(w.ObjectPlacement)
    mn, mx = _window_local_bbox(w)
    corners = np.array([[x, y, z, 1.0] for x in (mn[0], mx[0])
                        for y in (mn[1], mx[1]) for z in (mn[2], mx[2])])
    wc = (M @ corners.T).T[:, :3]
    return wc.min(0), wc.max(0)


def _win_solids(w):
    """The clean parametric form the converter authors, or None. Returns
    (frame_solid, pane_solid, frame_profile, pane_profile)."""
    rep = w.Representation
    if not rep:
        return None
    bodies = [r for r in rep.Representations if r.RepresentationIdentifier == "Body"]
    if not bodies:
        return None
    items = bodies[0].Items
    if any(i.is_a("IfcMappedItem") for i in items):
        return None
    solids = [i for i in items if i.is_a("IfcExtrudedAreaSolid")]
    if len(solids) != 2:
        return None
    frame = pane = fp = pp = None
    for s in solids:
        pr = s.SweptArea
        if pr.is_a("IfcRectangleHollowProfileDef"):
            frame, fp = s, pr
        elif pr.is_a("IfcRectangleProfileDef"):
            pane, pp = s, pr
    if frame is None or pane is None:
        return None
    return frame, pane, fp, pp


def _is_manipulable(model, w):
    """True iff the window is in the clean, parameter-drivable state the converter produces:
    one non-mapped Body of exactly 2 swept solids (hollow frame + inset pane), both styled,
    with a real frame border thickness. Baked/mapped originals fail this."""
    s = _win_solids(w)
    if s is None:
        return False
    frame, pane, fp, pp = s
    if not fp.WallThickness or fp.WallThickness <= 0:
        return False
    if not (pp.XDim < fp.XDim and pp.YDim < fp.YDim):     # pane inset inside frame
        return False
    styled = {st.Item for st in model.by_type("IfcStyledItem")}
    if frame not in styled or pane not in styled:          # glass + frame appearance present
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Invariant layers
# ══════════════════════════════════════════════════════════════════════════════
PRESERVE = [
    "IfcWindow", "IfcWall", "IfcWallStandardCase", "IfcSlab", "IfcRoof", "IfcDoor",
    "IfcOpeningElement", "IfcSpace", "IfcBuildingStorey", "IfcCovering",
    "IfcFurnishingElement", "IfcBuildingElementProxy",
    "IfcRelFillsElement", "IfcRelVoidsElement", "IfcRelContainedInSpatialStructure",
    "IfcRelDefinesByType", "IfcRelAggregates",
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

    # Window stays located: its ObjectPlacement (4×4) is preserved exactly vs the original,
    # so a rebuilt window cannot have drifted out of its opening. (Kernel-free, stronger than
    # a bbox-drift tolerance.)
    bpl = {w.GlobalId: ifc_placement.get_local_placement(w.ObjectPlacement)
           for w in before.by_type("IfcWindow") if w.ObjectPlacement}
    bad = 0
    for w in _marked(after):
        m0 = bpl.get(w.GlobalId)
        if m0 is None or w.ObjectPlacement is None:
            bad += 1
            continue
        if not np.allclose(m0, ifc_placement.get_local_placement(w.ObjectPlacement), atol=1e-9):
            bad += 1
    c.check(bad == 0, f"[B] rebuilt windows keep original placement ({bad} drifted)")
    c.check(_sha(src) == sha0 and os.path.getmtime(src) == mtime0, "[B] source file untouched")


def layer_C_manipulable_state(c, after):
    marked = _marked(after)
    for w in marked:
        c.check(_is_manipulable(after, w), f"[C] manipulable state: {w.Name!r}")
    return marked


def _resize_axis(c, out, base_err, dim, label):
    """Drive the frame profile's `dim` (XDim or YDim) to 1.5x, keep border constant, and
    confirm the window actually grew along that axis only — the parametric 'make it wider'."""
    m = ifcopenshell.open(str(out))
    # XDim of the profile maps to local axis u, YDim to v (see _extrude_along in the converter)
    for w in _marked(m):
        s = _win_solids(w)
        if s is None:
            continue
        frame, pane, fp, pp = s
        mn0, mx0 = _window_local_bbox(w); ext0 = mx0 - mn0
        d = int(np.argmin(ext0)); u, v = [i for i in range(3) if i != d]
        grow_axis = u if dim == "XDim" else v
        keep_axis = v if dim == "XDim" else u
        thk0 = fp.WallThickness
        setattr(fp, dim, getattr(fp, dim) * 1.5)
        setattr(pp, dim, getattr(fp, dim) - 2 * thk0)
        mn1, mx1 = _window_local_bbox(w); ext1 = mx1 - mn1
        c.check(abs(fp.WallThickness - thk0) < 1e-9, f"[D-{label}] frame border constant: {w.Name!r}")
        c.check(getattr(pp, dim) < getattr(fp, dim), f"[D-{label}] pane still inset: {w.Name!r}")
        c.check(abs(ext1[grow_axis] / ext0[grow_axis] - 1.5) < 0.05,
                f"[D-{label}] grew ~1.5x: {w.Name!r}")
        c.check(abs(ext1[keep_axis] - ext0[keep_axis]) < 1e-3 * max(1.0, ext0[keep_axis]),
                f"[D-{label}] other axis unchanged: {w.Name!r}")
    c.check(_nerr(m) <= base_err, f"[D-{label}] no new validate errors")


def layer_D_manipulate(c, out, base_err):
    _resize_axis(c, out, base_err, "XDim", "width")
    _resize_axis(c, out, base_err, "YDim", "height")

    # MOVE — translate placement; window should shift rigidly (size preserved, GlobalId intact)
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

    # ROTATE — 90° about the placement's own Axis (RefDirection := Axis × RefDirection stays
    # orthonormal); geometry untouched, must stay valid.
    m = ifcopenshell.open(str(out))
    for w in _marked(m):
        rep_items0 = len(w.Representation.Representations[0].Items)
        gid = w.GlobalId
        pl = w.ObjectPlacement.RelativePlacement
        ax = np.array(pl.Axis.DirectionRatios if pl.Axis else (0.0, 0.0, 1.0))
        rd = np.array(pl.RefDirection.DirectionRatios if pl.RefDirection else (1.0, 0.0, 0.0))
        nrd = np.cross(ax, rd)
        if np.linalg.norm(nrd) < 1e-9:
            continue
        pl.RefDirection = m.create_entity("IfcDirection", DirectionRatios=tuple(map(float, nrd)))
        c.check(len(w.Representation.Representations[0].Items) == rep_items0,
                f"[D-rotate] geometry untouched: {w.Name!r}")
        c.check(w.GlobalId == gid, f"[D-rotate] GlobalId intact: {w.Name!r}")
    c.check(_nerr(m) <= base_err, "[D-rotate] no new validate errors")


def layer_E_idempotency(c, out):
    out2 = str(out) + ".rerun.ifc"
    _silent_convert(out, out2)
    m1 = ifcopenshell.open(str(out)); m2 = ifcopenshell.open(out2)
    a = {w.GlobalId: len(w.Representation.Representations[0].Items) for w in _marked(m1)}
    b = {w.GlobalId: len(w.Representation.Representations[0].Items) for w in _marked(m2)}
    c.check(a == b and len(_marked(m2)) == len(_marked(m1)),
            "[E] idempotent — re-run leaves rebuilt windows unchanged")
    try:
        os.remove(out2)
    except OSError:
        pass


# Pinned per-fixture rebuilt-window counts (Gal-style baseline). A no-op or a regression
# that silently rebuilds fewer windows must trip this. Update only with an intended change.
# Keyed by fixtures in INPUT_IFC_FILES_HERE/; a new file without an entry gets the looser
# "rebuilt >= 1" check.
BASELINE_REBUILT = {
    "LEXFORD_OFFICE-C1.ifc": 6,                   # 7 windows, 1 trapezoid kept
    "SAN_JUAN_CYPRESS_-_AUG_2-W1-L1.ifc": 6,
    "Sunflower_Sunflower_A_Sunflower_A_.ifc": 4,  # 5 windows, 1 bodiless skipped
    "Turnberry_927_TURNBERRY_ADU-DEC_2_2025-C1.ifc": 8,
}


def layer_F_negative_control(c, fixture_name, before, after):
    """Teeth: the SAME manipulable-state test on the original baked windows must fail, AND the
    converter must actually have produced the expected number of manipulable windows (so a
    no-op / silent regression cannot pass vacuously)."""
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
    tmpdir = tempfile.mkdtemp(prefix="winconv_test_")
    try:
        out = Path(tmpdir) / f"{path.stem}-WIN1.ifc"
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
    for d in FIXTURE_DIRS:                       # reference fixtures first, then INPUT/
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.ifc")):
            if p.stem.endswith(WC.SUFFIX) or p.name in seen:
                continue                         # skip -WIN1 + the same file present in both dirs
            seen.add(p.name)
            out.append(p)
    return out


def main():
    verbose = "-v" in sys.argv
    args = [a for a in sys.argv[1:] if a != "-v"]

    try:
        import ifcopenshell as _io
        _ = _io.version
    except Exception as e:
        print(f"PREFLIGHT FAIL: ifcopenshell not importable ({e!r}) — run with python3.11")
        sys.exit(2)

    fixtures = _fixtures(args)
    if not fixtures:
        print("No fixtures found in FormX Designs IFC/ or INPUT_IFC_FILES_HERE/")
        sys.exit(1)

    results = [test_fixture(p, verbose) for p in fixtures]

    print(f"\n{'='*78}\nWINDOW CONVERTER — TEST REPORT (ifcopenshell {ifcopenshell.version})\n{'='*78}")
    npass = sum(1 for r in results if r.ok)
    for r in results:
        tag = "PASS" if r.ok else "FAIL"
        detail = "" if r.ok else "  — " + "; ".join(r.fails[:3]) + ("…" if len(r.fails) > 3 else "")
        print(f"  {tag}  {r.name}{detail}")
    print(f"\nRESULT: {'ALL PASS' if npass == len(results) else 'FAIL'}  ({npass}/{len(results)} fixtures)")
    sys.exit(0 if npass == len(results) else 1)


if __name__ == "__main__":
    main()
