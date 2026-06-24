#!/usr/bin/env python3
"""
test_door_converter.py — acceptance/regression tester for the IFC door converter.

Mirrors the window tester (../IFC Window Converter/test_window_converter.py), which follows
Gal's testing methodology: analytical, **no Blender** — a converted door is manipulable because
its geometry is now a clean parametric profile (`IfcRectangleHollowProfileDef.XDim` /
`WallThickness` for the lining, `IfcRectangleProfileDef.XDim/YDim` for the leaf), and that is
tested far more reliably in code than by eye in a viewer. The harness:

  - runs `convert(src, tmp)` into a THROWAWAY temp (never pollutes OUTPUT_IFC_FILES_HERE/),
  - re-derives every invariant from scratch (does NOT call the converter's own verify()),
  - and — the door-specific part — actually MANIPULATES each rebuilt door (parametric resize /
    move / rotate) and asserts it behaves well (frame border stays constant, leaf stays inset,
    door moves rigidly, geometry stays valid),
  - and asserts the door-specific preservation: the 2D FootPrint representation is left intact.

Teeth / negative control: the same "is this door manipulable?" test is run on the ORIGINAL
(pre-conversion, baked) doors — they MUST fail. A brep has no drivable width parameter, so this
single before/after contrast both proves the tests have teeth and proves the converter's purpose.

Run with python3.11 (ifcopenshell 0.8.5):
    python3.11 test_door_converter.py            # all fixtures in INPUT_IFC_FILES_HERE/
    python3.11 test_door_converter.py -v         # verbose: show every passing assertion
    python3.11 test_door_converter.py <file.ifc> # one fixture
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
ROOT = HERE.parent
CONVERTER = HERE / "IFC_door_converter_V1.py"
FIXTURE_DIRS = [ROOT / "INPUT_IFC_FILES_HERE"]


def _load_converter():
    spec = importlib.util.spec_from_file_location("doorconv", CONVERTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DC = _load_converter()
MARK = DC.MARK


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
        DC.convert(str(src), str(out))


def _nerr(model):
    log = ifc_validate.json_logger()
    ifc_validate.validate(model, log)
    return len(log.statements)


def _marked(model):
    return [d for d in model.by_type("IfcDoor") if (d.Description or "") == MARK]


def _has_footprint(d):
    rep = d.Representation
    return bool(rep and any((r.RepresentationIdentifier or "") == "FootPrint"
                            for r in (rep.Representations or [])))


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


def _body_rep(d):
    """The door's 'Body' IfcShapeRepresentation, or None. Selected by identifier (NOT
    Representations[0]) because a door's FootPrint sibling can come first."""
    rep = d.Representation
    if not rep:
        return None
    bodies = [r for r in rep.Representations if r.RepresentationIdentifier == "Body"]
    return bodies[0] if bodies else None


def _body_solids(d):
    body = _body_rep(d)
    if body is None:
        return []
    return [i for i in (body.Items or []) if i.is_a("IfcExtrudedAreaSolid")]


def _door_local_bbox(d):
    pts = np.vstack([_solid_corners(s) for s in _body_solids(d)])
    return pts.min(0), pts.max(0)


def _door_world_bbox(d):
    M = ifc_placement.get_local_placement(d.ObjectPlacement)
    mn, mx = _door_local_bbox(d)
    corners = np.array([[x, y, z, 1.0] for x in (mn[0], mx[0])
                        for y in (mn[1], mx[1]) for z in (mn[2], mx[2])])
    wc = (M @ corners.T).T[:, :3]
    return wc.min(0), wc.max(0)


def _door_solids(d):
    """The clean parametric form the converter authors, or None. Returns
    (outer_frame_solid, outer_frame_profile, fill_solids) where fills = everything else: per-leaf
    sub-frames + panes + dividers + handle solids.

    The Body now contains MULTIPLE hollow profiles (the outer lining + one per framed leaf), so
    the **outer** lining is the largest-area hollow; the rest are fills. (Subtype trap:
    IfcRectangleHollowProfileDef IS-A IfcRectangleProfileDef — match hollows separately.)"""
    body = _body_rep(d)
    if body is None:
        return None
    items = body.Items or []
    if any(i.is_a("IfcMappedItem") for i in items):
        return None
    solids = [i for i in items if i.is_a("IfcExtrudedAreaSolid")]
    hollows = [s for s in solids if s.SweptArea.is_a("IfcRectangleHollowProfileDef")]
    if not hollows or len(solids) - len(hollows) < 1:       # ≥1 hollow + ≥1 non-hollow fill
        return None
    outer = max(hollows, key=lambda s: s.SweptArea.XDim * s.SweptArea.YDim)   # largest = lining
    fills = [s for s in solids if s is not outer]
    return outer, outer.SweptArea, fills


def _is_manipulable(model, d):
    """True iff the door is in the clean, parameter-drivable state the converter produces: a
    non-mapped Body with a hollow outer lining frame (real `WallThickness`, largest of the
    hollows) + ≥1 inset pane, every item styled. Baked/mapped originals fail this. Tolerant of
    the extra per-leaf sub-frames + handle solids (they're additional fills)."""
    s = _door_solids(d)
    if s is None:
        return False
    frame, fp, fills = s
    if not fp.WallThickness or fp.WallThickness <= 0:
        return False
    eps = 1e-6 * max(fp.XDim, fp.YDim)
    if not all(f.SweptArea.XDim <= fp.XDim + eps and f.SweptArea.YDim <= fp.YDim + eps
               for f in fills):
        return False                                        # every fill fits within the lining face
    panes = [f for f in fills if f.SweptArea.is_a("IfcRectangleProfileDef")
             and not f.SweptArea.is_a("IfcRectangleHollowProfileDef")]
    if not any(f.SweptArea.XDim < fp.XDim and f.SweptArea.YDim < fp.YDim for f in panes):
        return False                                        # at least one genuinely inset pane
    styled = {st.Item for st in model.by_type("IfcStyledItem")}
    if frame not in styled or any(f not in styled for f in fills):
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
# Invariant layers
# ══════════════════════════════════════════════════════════════════════════════
PRESERVE = [
    "IfcDoor", "IfcWindow", "IfcWall", "IfcWallStandardCase", "IfcSlab", "IfcRoof",
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
    gb = sorted(d.GlobalId for d in before.by_type("IfcDoor"))
    ga = sorted(d.GlobalId for d in after.by_type("IfcDoor"))
    c.check(gb == ga, "[A] door GlobalId multiset identical")
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

    # Door stays located: its ObjectPlacement (4×4) is preserved exactly vs the original, so a
    # rebuilt door cannot have drifted out of its opening / off the floor. (Kernel-free.)
    bpl = {d.GlobalId: ifc_placement.get_local_placement(d.ObjectPlacement)
           for d in before.by_type("IfcDoor") if d.ObjectPlacement}
    bad = 0
    for d in _marked(after):
        m0 = bpl.get(d.GlobalId)
        if m0 is None or d.ObjectPlacement is None:
            bad += 1
            continue
        if not np.allclose(m0, ifc_placement.get_local_placement(d.ObjectPlacement), atol=1e-9):
            bad += 1
    c.check(bad == 0, f"[B] rebuilt doors keep original placement ({bad} drifted)")

    # Door-specific: the 2D FootPrint representation is preserved (only Body was swapped).
    fp_before = {d.GlobalId for d in before.by_type("IfcDoor") if _has_footprint(d)}
    fp_after = {d.GlobalId for d in after.by_type("IfcDoor") if _has_footprint(d)}
    c.check(fp_before <= fp_after,
            f"[B] FootPrint reps preserved ({len(fp_before - fp_after)} lost of {len(fp_before)})")

    c.check(_sha(src) == sha0 and os.path.getmtime(src) == mtime0, "[B] source file untouched")


def layer_C_manipulable_state(c, after):
    marked = _marked(after)
    for d in marked:
        c.check(_is_manipulable(after, d), f"[C] manipulable state: {d.Name!r}")
    return marked


def _resize_axis(c, out, base_err, dim, label):
    """Drive the OUTER lining frame profile's `dim` (XDim or YDim) to 1.5x — the parametric
    'make it wider' — and confirm the door grew along that axis only, the border stayed constant,
    and the panes stay inside. (The outer frame is the drivable overall-size parameter; per-leaf
    reflow is a higher parametric layer, see the limitations.)"""
    m = ifcopenshell.open(str(out))
    for d in _marked(m):
        s = _door_solids(d)
        if s is None:
            continue
        frame, fp, fills = s
        # Which local axis does each profile dim grow along? Read it from the frame's OWN
        # placement (XDim → RefDirection = the divide axis; YDim → Axis × RefDirection = span),
        # because the divide axis is class-dependent now, not a fixed index.
        ref = np.array(frame.Position.RefDirection.DirectionRatios)
        axis = np.array(frame.Position.Axis.DirectionRatios)
        yloc = np.cross(axis, ref)
        grow = int(np.argmax(np.abs(ref if dim == "XDim" else yloc)))
        keep = int(np.argmax(np.abs(yloc if dim == "XDim" else ref)))
        mn0, mx0 = _door_local_bbox(d); ext0 = mx0 - mn0
        thk0 = fp.WallThickness
        setattr(fp, dim, getattr(fp, dim) * 1.5)
        mn1, mx1 = _door_local_bbox(d); ext1 = mx1 - mn1
        c.check(abs(fp.WallThickness - thk0) < 1e-9, f"[D-{label}] frame border constant: {d.Name!r}")
        c.check(abs(ext1[grow] / ext0[grow] - 1.5) < 0.05, f"[D-{label}] grew ~1.5x: {d.Name!r}")
        c.check(abs(ext1[keep] - ext0[keep]) < 1e-3 * max(1.0, ext0[keep]),
                f"[D-{label}] other axis unchanged: {d.Name!r}")
        c.check(all(f.SweptArea.XDim <= fp.XDim and f.SweptArea.YDim <= fp.YDim for f in fills),
                f"[D-{label}] panes stay inside frame: {d.Name!r}")
    c.check(_nerr(m) <= base_err, f"[D-{label}] no new validate errors")


def layer_D_manipulate(c, out, base_err):
    _resize_axis(c, out, base_err, "XDim", "width")
    _resize_axis(c, out, base_err, "YDim", "height")

    # MOVE — translate placement; door should shift rigidly (size preserved, GlobalId intact)
    m = ifcopenshell.open(str(out))
    for d in _marked(m):
        wb0 = _door_world_bbox(d)
        size0 = wb0[1] - wb0[0]; ctr0 = (wb0[0] + wb0[1]) / 2
        gid = d.GlobalId
        pl = d.ObjectPlacement.RelativePlacement
        loc = list(pl.Location.Coordinates)
        pl.Location = m.create_entity("IfcCartesianPoint",
                                      Coordinates=(loc[0] + 1.0, loc[1], loc[2]))
        wb1 = _door_world_bbox(d); size1 = wb1[1] - wb1[0]; ctr1 = (wb1[0] + wb1[1]) / 2
        c.check(np.allclose(size1, size0, atol=1e-4), f"[D-move] size preserved (rigid): {d.Name!r}")
        c.check(np.linalg.norm(ctr1 - ctr0) > 1e-6, f"[D-move] door moved: {d.Name!r}")
        c.check(d.GlobalId == gid, f"[D-move] GlobalId intact: {d.Name!r}")
    c.check(_nerr(m) <= base_err, "[D-move] no new validate errors")

    # ROTATE — 90° about the placement's own Axis (RefDirection := Axis × RefDirection stays
    # orthonormal); geometry untouched, must stay valid.
    m = ifcopenshell.open(str(out))
    for d in _marked(m):
        body_items0 = len(_body_rep(d).Items)
        gid = d.GlobalId
        pl = d.ObjectPlacement.RelativePlacement
        ax = np.array(pl.Axis.DirectionRatios if pl.Axis else (0.0, 0.0, 1.0))
        rd = np.array(pl.RefDirection.DirectionRatios if pl.RefDirection else (1.0, 0.0, 0.0))
        nrd = np.cross(ax, rd)
        if np.linalg.norm(nrd) < 1e-9:
            continue
        pl.RefDirection = m.create_entity("IfcDirection", DirectionRatios=tuple(map(float, nrd)))
        c.check(len(_body_rep(d).Items) == body_items0,
                f"[D-rotate] geometry untouched: {d.Name!r}")
        c.check(d.GlobalId == gid, f"[D-rotate] GlobalId intact: {d.Name!r}")
    c.check(_nerr(m) <= base_err, "[D-rotate] no new validate errors")


def layer_E_idempotency(c, out):
    out2 = str(out) + ".rerun.ifc"
    _silent_convert(out, out2)
    m1 = ifcopenshell.open(str(out)); m2 = ifcopenshell.open(out2)
    a = {d.GlobalId: len(_body_rep(d).Items) for d in _marked(m1)}
    b = {d.GlobalId: len(_body_rep(d).Items) for d in _marked(m2)}
    c.check(a == b and len(_marked(m2)) == len(_marked(m1)),
            "[E] idempotent — re-run leaves rebuilt doors unchanged")
    try:
        os.remove(out2)
    except OSError:
        pass


# Pinned per-fixture rebuilt-door counts (Gal-style baseline). A no-op or a regression that
# silently rebuilds fewer doors must trip this. Update only with an intended change. Keyed by
# fixtures in INPUT_IFC_FILES_HERE/; a new file without an entry gets the looser "rebuilt >= 1".
BASELINE_REBUILT = {
    "LEXFORD_OFFICE-C1.ifc": 2,
    "SAN_JUAN_CYPRESS_-_AUG_2-W1-L1.ifc": 1,
    "Sunflower_Sunflower_A_Sunflower_A_.ifc": 3,
    "Turnberry_927_TURNBERRY_ADU-DEC_2_2025-C1.ifc": 5,
}


def layer_F_negative_control(c, fixture_name, before, after):
    """Teeth: the SAME manipulable-state test on the original baked doors must fail, AND the
    converter must actually have produced the expected number of manipulable doors (so a no-op /
    silent regression cannot pass vacuously)."""
    n_before = sum(1 for d in before.by_type("IfcDoor") if _is_manipulable(before, d))
    n_after = sum(1 for d in after.by_type("IfcDoor") if _is_manipulable(after, d))
    n_marked = len(_marked(after))
    c.check(n_before == 0,
            f"[F] no ORIGINAL door was already manipulable — test has teeth ({n_before} were)")
    c.check(n_after == n_marked, f"[F] every rebuilt door is manipulable ({n_after}/{n_marked})")
    expected = BASELINE_REBUILT.get(fixture_name)
    if expected is not None:
        c.check(n_marked == expected,
                f"[F] rebuilt count matches baseline ({n_marked} == {expected})")
    else:
        c.check(n_marked > 0, f"[F] converter rebuilt at least one door (not a no-op) [{n_marked}]")


# ══════════════════════════════════════════════════════════════════════════════
def test_fixture(path, verbose):
    c = Checker(path.name, verbose)
    print(f"\n{'='*78}\n{path.name}\n{'='*78}")
    sha0 = _sha(path); mt0 = os.path.getmtime(path)
    tmpdir = tempfile.mkdtemp(prefix="doorconv_test_")
    try:
        out = Path(tmpdir) / f"{path.stem}-D1.ifc"
        _silent_convert(path, out)

        before = ifcopenshell.open(str(path))
        after = ifcopenshell.open(str(out))
        base_err = _nerr(after)
        marked = _marked(after)
        print(f"  doors: {len(after.by_type('IfcDoor'))} total | "
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
            if p.stem.endswith(DC.SUFFIX) or p.name in seen:
                continue                         # skip -D1 outputs + duplicates
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
        print("No fixtures found in INPUT_IFC_FILES_HERE/")
        sys.exit(1)

    results = [test_fixture(p, verbose) for p in fixtures]

    print(f"\n{'='*78}\nDOOR CONVERTER — TEST REPORT (ifcopenshell {ifcopenshell.version})\n{'='*78}")
    npass = sum(1 for r in results if r.ok)
    for r in results:
        tag = "PASS" if r.ok else "FAIL"
        detail = "" if r.ok else "  — " + "; ".join(r.fails[:3]) + ("…" if len(r.fails) > 3 else "")
        print(f"  {tag}  {r.name}{detail}")
    print(f"\nRESULT: {'ALL PASS' if npass == len(results) else 'FAIL'}  ({npass}/{len(results)} fixtures)")
    sys.exit(0 if npass == len(results) else 1)


if __name__ == "__main__":
    main()
