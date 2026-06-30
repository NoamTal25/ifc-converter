#!/usr/bin/env python3
"""
test_door_converter_v2.py — acceptance/regression tester for the v2 door converter.

Mirrors the window v2 tester (and Gal's methodology): analytical, **no Blender**. A converted door
is manipulable because its geometry is now clean parametric rectangular swept solids, and that is
tested far more reliably in code than by eye. The harness, per fixture:

  - runs ``convert(src, tmp)`` into a THROWAWAY temp,
  - re-derives every invariant from scratch (does NOT call the converter's verify()),
  - and MANIPULATES doors (parametric resize via the shared recipe, plus per-instance move /
    rotate), asserting it behaves well (frame border constant, lining grows along the driven axis,
    moves rigidly, stays valid).

Generalised for v2's door topologies: a rebuilt door is **a 4-bar IfcRectangleProfileDef lining +
N≥0 IfcRectangleProfileDef parts (panels / mullions / rails / track / rollers / handles)**, all
styled, with NO IfcRectangleHollowProfileDef (Gaudi mis-renders the hollow profile — §6). Single,
double, pocket, sliding, bifold and the cased opening (lining only) all satisfy this. The
parametric-resize check drives the shared recipe directly (the function that authored every rebuilt
door), measuring the 'frame' bars + 'panel's so proud handles / barn track don't skew it.

Teeth / negative control: the SAME manipulable-state test on the ORIGINAL baked doors MUST fail
(a baked brep / mapped item has no drivable hollow-lining parameter), and the converter must rebuild
exactly the pinned baseline count per fixture — so a no-op / silent regression cannot pass.

Run with python3.11 (ifcopenshell 0.8.5):
    python3.11 test_door_converter_v2.py            # all fixtures in INPUT_IFC_FILES_HERE/
    python3.11 test_door_converter_v2.py -v         # verbose
    python3.11 test_door_converter_v2.py <file.ifc> # one fixture
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
import ifcopenshell.util.element as ifc_element
import ifcopenshell.validate as ifc_validate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONVERTER = HERE / "IFC_door_converter_V2.py"
FIXTURE_DIRS = [ROOT / "INPUT_IFC_FILES_HERE"]

sys.path.insert(0, str(HERE))
import door_types   # single source of truth — used to cross-check per-door rebuilt topology


def _load_converter():
    spec = importlib.util.spec_from_file_location("doorconv2", CONVERTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DC = _load_converter()
MARK = DC.MARK


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
        DC.convert(str(src), str(out))


def _nerr(model):
    log = ifc_validate.json_logger()
    ifc_validate.validate(model, log)
    return len(log.statements)


def _marked(model):
    return [d for d in model.by_type("IfcDoor") if (d.Description or "") == MARK]


def _body(d):
    rep = d.Representation
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


def _solids(d):
    """All IfcExtrudedAreaSolid items of the Body, or [] (mapped/baked → none)."""
    b = _body(d)
    if not b:
        return []
    items = b.Items or []
    if any(i.is_a("IfcMappedItem") for i in items):
        return []
    return [i for i in items if i.is_a("IfcExtrudedAreaSolid")]


def _door_local_bbox(d):
    pts = np.vstack([_solid_corners(s) for s in _solids(d)])
    return pts.min(0), pts.max(0)


def _solid_local_bbox(solid):
    pts = _solid_corners(solid)
    return pts.min(0), pts.max(0)


def _door_world_bbox(d):
    M = ifc_placement.get_local_placement(d.ObjectPlacement)
    mn, mx = _door_local_bbox(d)
    corners = np.array([[x, y, z, 1.0] for x in (mn[0], mx[0])
                        for y in (mn[1], mx[1]) for z in (mn[2], mx[2])])
    wc = (M @ corners.T).T[:, :3]
    return wc.min(0), wc.max(0)


def _is_manipulable(model, d):
    """True iff d is in the clean, parameter-drivable state the converter produces: its Body is ≥4
    plain rectangular swept solids (a 4-bar lining + any panels / mullions / rails / track / rollers
    / handles), with NO IfcRectangleHollowProfileDef (Gaudi mis-renders it, §6), every item styled.
    A leafless cased opening is the 4 lining bars alone → minimum 4. Baked / mapped doors have no
    rect swept solids → fail (the test's teeth)."""
    solids = _solids(d)
    if len(solids) < 4:
        return False
    for s in solids:
        pr = s.SweptArea
        # reject hollow profiles (test hollow FIRST — it IS-A IfcRectangleProfileDef, §6) + non-rects
        if pr.is_a("IfcRectangleHollowProfileDef") or not pr.is_a("IfcRectangleProfileDef"):
            return False
    styled = {st.Item for st in model.by_type("IfcStyledItem")}
    if any(s not in styled for s in solids):
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
    "IfcRelDefinesByType", "IfcRelAggregates",   # converter never mints a type → count invariant
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

    bpl = {d.GlobalId: ifc_placement.get_local_placement(d.ObjectPlacement)
           for d in before.by_type("IfcDoor") if d.ObjectPlacement}
    bad = 0
    for d in _marked(after):
        m0 = bpl.get(d.GlobalId)
        if m0 is None or d.ObjectPlacement is None or \
           not np.allclose(m0, ifc_placement.get_local_placement(d.ObjectPlacement), atol=1e-9):
            bad += 1
    c.check(bad == 0, f"[B] rebuilt doors keep original placement ({bad} drifted)")

    # FootPrint preserved where the original had one (we swap only Body).
    fp_before = {d.GlobalId for d in before.by_type("IfcDoor")
                 if d.Representation and any(r.RepresentationIdentifier == "FootPrint"
                                             for r in d.Representation.Representations)}
    fp_lost = 0
    for d in after.by_type("IfcDoor"):
        if d.GlobalId in fp_before:
            has = d.Representation and any(r.RepresentationIdentifier == "FootPrint"
                                          for r in d.Representation.Representations)
            if not has:
                fp_lost += 1
    c.check(fp_lost == 0, f"[B] FootPrint representation preserved ({fp_lost} lost)")

    c.check(_sha(src) == sha0 and os.path.getmtime(src) == mtime0, "[B] source file untouched")


def layer_C_manipulable_state(c, after):
    marked = _marked(after)
    for d in marked:
        c.check(_is_manipulable(after, d), f"[C] manipulable state: {d.Name!r}")
        od = ifc_element.get_psets(d).get("FormX_Door_Window", {}).get("OpeningDirection")
        c.check(bool(od), f"[C] OpeningDirection present: {d.Name!r} → {od!r}")
    return marked


def _recipe_door_items(formx_type, width, height):
    """Build one door via the SHARED recipe (the same code that authored every rebuilt door),
    axis-aligned (width=X, height=Y, depth=Z), in a throwaway file."""
    f = ifcopenshell.file(schema="IFC4")
    td = door_types.TYPES[formx_type]
    return DC.gg.build_door_items(
        f, width, height, DC.gg.LINING_DEPTH, recipe=td["recipe"],
        dims=DC.gg.dims_in_units(0.001),
        center=(0.0, 0.0, DC.gg.LINING_DEPTH / 2.0), depth_dir=(0, 0, 1), width_dir=(1, 0, 0))


def _frame_inner_extents(items):
    """(outer_ext, inner_ext) as (X, Y, Z) arrays: outer = bbox of the 'frame' bars, inner = bbox
    of the 'panel' solids (None if leafless)."""
    fo = np.vstack([_solid_corners(s) for s, r in items if r == "frame"])
    outer = fo.max(0) - fo.min(0)
    panels = [s for s, r in items if r == "panel"]
    inner = None
    if panels:
        pi = np.vstack([_solid_corners(s) for s in panels])
        inner = pi.max(0) - pi.min(0)
    return outer, inner


def _parametric_resize_check(c, formx_type, axis, label):
    """Drive the SHARED recipe 1.5x along one face axis (0=width/X, 1=height/Y) and confirm the
    lining grows on that axis ONLY, with the frame border held constant — the real 'make it wider'.
    The lining is now 4 bars, so this tests the generative function (which authored every rebuilt
    door) rather than mutating a single hollow profile. Measured on the 'frame' bars + 'panel's, so
    proud handles / barn track don't skew it."""
    base_w, base_h = 1000.0, 2100.0
    o0, i0 = _frame_inner_extents(_recipe_door_items(formx_type, base_w, base_h))
    o1, i1 = _frame_inner_extents(_recipe_door_items(
        formx_type, base_w * (1.5 if axis == 0 else 1.0), base_h * (1.5 if axis == 1 else 1.0)))
    other = 1 - axis
    c.check(abs(o1[axis] / o0[axis] - 1.5) < 0.02, f"[D-{label}] lining grew ~1.5x")
    c.check(abs(o1[other] - o0[other]) < 1e-6 * max(1.0, o0[other]),
            f"[D-{label}] other axis unchanged")
    if i0 is not None and i1 is not None:
        b0 = (o0[axis] - i0[axis]) / 2.0
        b1 = (o1[axis] - i1[axis]) / 2.0
        c.check(abs(b1 - b0) < 1e-6 * max(1.0, b0), f"[D-{label}] frame border constant")


def layer_D_manipulate(c, out, base_err):
    # PARAMETRIC RESIZE — exercise the shared recipe (it authored every rebuilt door) along each
    # face axis, for a single-leaf and a 2-leaf type. Border held constant = real 'make it wider'.
    # NOTE: the 2-leaf probe is DOOR_INTERIOR_DOUBLE (leaf-frame → border = frame_thk+leaf_frame_thk,
    # constant across the range). DOOR_DOUBLE is now panelled, whose per-leaf stile width hits its
    # proportional clamp on narrow leaves, so its lining→panel border is NOT scale-invariant.
    for ft in ("DOOR_SINGLE", "DOOR_INTERIOR_DOUBLE"):
        _parametric_resize_check(c, ft, 0, f"width/{ft}")
        _parametric_resize_check(c, ft, 1, f"height/{ft}")

    # MOVE — translate placement; door shifts rigidly (size preserved, GlobalId intact)
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

    # ROTATE — 90° about the placement's own Axis; geometry untouched, must stay valid.
    m = ifcopenshell.open(str(out))
    for d in _marked(m):
        body = _body(d)
        rep_items0 = len(body.Items or [])
        gid = d.GlobalId
        pl = d.ObjectPlacement.RelativePlacement
        ax = np.array(pl.Axis.DirectionRatios if pl.Axis else (0.0, 0.0, 1.0))
        rd = np.array(pl.RefDirection.DirectionRatios if pl.RefDirection else (1.0, 0.0, 0.0))
        nrd = np.cross(ax, rd)
        if np.linalg.norm(nrd) < 1e-9:
            continue
        pl.RefDirection = m.create_entity("IfcDirection", DirectionRatios=tuple(map(float, nrd)))
        c.check(len(_body(d).Items or []) == rep_items0, f"[D-rotate] geometry untouched: {d.Name!r}")
        c.check(d.GlobalId == gid, f"[D-rotate] GlobalId intact: {d.Name!r}")
    c.check(_nerr(m) <= base_err, "[D-rotate] no new validate errors")


def layer_E_idempotency(c, out):
    out2 = str(out) + ".rerun.ifc"
    _silent_convert(out, out2)
    m1 = ifcopenshell.open(str(out)); m2 = ifcopenshell.open(out2)
    a = {d.GlobalId: len(_body(d).Items) for d in _marked(m1)}
    b = {d.GlobalId: len(_body(d).Items) for d in _marked(m2)}
    c.check(a == b and len(_marked(m2)) == len(_marked(m1)),
            "[E] idempotent — re-run leaves rebuilt doors unchanged")
    try:
        os.remove(out2)
    except OSError:
        pass


# Pinned per-fixture rebuilt counts. A no-op / silent regression must trip this.
BASELINE_REBUILT = {
    "LEXFORD_OFFICE-C1.ifc": 2,                            # interior-double-glass + single-flush
    "SAN_JUAN_CYPRESS_-_AUG_2-W1-L1.ifc": 1,              # one sliding
    "Sunflower_Sunflower_A_Sunflower_A_.ifc": 3,          # sliding + 2 pocket
    "Turnberry_927_TURNBERRY_ADU-DEC_2_2025-C1.ifc": 5,   # flush + pocket + four-fold + 2 interior-double
}

# Pinned per-fixture rebuilt FormX-type multiset (independent ground truth from the grounding scan).
# This is the teeth against MISCLASSIFICATION: classification is the structural core of the
# golden-template-swap method (a wrong class = a wrong golden = wrong geometry), and the
# count/manipulable layers alone can't see it (every single-leaf rebuild satisfies them). Forcing
# every door to one type, or swapping POCKET↔SLIDING, trips this multiset.
BASELINE_TYPES = {
    "LEXFORD_OFFICE-C1.ifc": {"DOOR_INTERIOR_DOUBLE": 1, "DOOR_SINGLE_FLUSH": 1},
    "SAN_JUAN_CYPRESS_-_AUG_2-W1-L1.ifc": {"DOOR_SLIDING": 1},
    "Sunflower_Sunflower_A_Sunflower_A_.ifc": {"DOOR_SLIDING": 1, "DOOR_POCKET": 2},
    "Turnberry_927_TURNBERRY_ADU-DEC_2_2025-C1.ifc":
        {"DOOR_SINGLE_FLUSH": 1, "DOOR_POCKET": 1, "DOOR_BIFOLDING_GLASS": 1,
         "DOOR_INTERIOR_DOUBLE": 2},
}


def _formx_type_of(d):
    """The rebuilt door's FormX type, read from its canonical Name ('FORMX_TYPE:eid')."""
    nm = d.Name or ""
    return nm.split(":", 1)[0] if nm else ""


def _expected_item_count(knobs):
    """Body solid count a recipe must produce — mirrors golden_door_geometry.build_door_items."""
    n = int(knobs.get("panels", 1))
    if knobs.get("shower"):
        # threshold + pivot jamb + glass + 3 hinges + towel pull + 2 standoffs = 9. No outer lining.
        return 1 + 1 + 1 + 3 + 1 + 2
    if knobs.get("combo"):
        # divider(1) + sliding sash[4 frame+1 glass] + track(1)+2 rollers+guide(1)+pull(1) +
        # panelled swing leaf(7) + knob(1) + 2 hinges + outer lining(4) = 25. Self-contained handles.
        return 4 + 1 + (5 + 1 + 2 + 1 + 1) + (7 + 1 + 2)
    if knobs.get("barn"):
        # per leaf: plank slab + 3 ledger battens + 2 straps + pull = 7; shared: track + 2 stops +
        # floor guide = 4. No lining. Self-contained pull.
        return 7 * n + 4
    if knobs.get("casing"):
        # 3-sided lining (head + 2 jambs) + architrave (head + 2 legs) on both faces = 3 + 2*3 = 9.
        return 3 + 2 * 3
    if knobs.get("pocket"):
        items = 3 + 1                             # 3-sided opening lining (head+2 jambs, no sill) + retracted leaf
    elif knobs.get("panelled") and n > 0:
        items = 4 + n * 7                         # outer lining + per-leaf (2 stiles + 3 rails + 2 recessed panels)
    elif (knobs.get("leaf_frame") or knobs.get("sliding")) and n > 0:
        items = 4 + n * 5                         # outer lining + per-sash/leaf (4 frame bars + 1 infill); no mullion
        if knobs.get("muntins"):
            items += 4 * n                        # per glazed leaf: lock rail + 1 vertical + 2 horizontal muntins
    else:
        items = 4 + n + max(0, n - 1)             # 4-bar lining + panels + (n-1) mullions
    if knobs.get("head_rail") and n > 0:
        items += 1                                # head rail
    if knobs.get("astragal") and n >= 2:
        items += 1                                # meeting-joint astragal cover bar
    if knobs.get("bifold") and n >= 2:
        items += 2 * (n - 1) + 1                  # 2 fold-hinge knuckles per joint + 1 top track guide
    if knobs.get("hinges") and n > 0:
        items += 3 * n                            # 3 butt hinges per leaf
    h = knobs.get("handle", "none")
    if h == "lever":
        items += 2 if n >= 2 else 1
    elif h == "pull":
        items += 1
    return items


def layer_F_negative_control(c, fixture_name, before, after):
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


def layer_G_classification(c, fixture_name, after):
    """TEETH against misclassification — the structural core of golden-template-swap. Asserts the
    rebuilt FormX-type multiset matches the pinned ground truth, and that each rebuilt door's Body
    topology (solid count) matches what its own type's recipe implies."""
    from collections import Counter
    got = Counter(_formx_type_of(d) for d in _marked(after))
    expected = BASELINE_TYPES.get(fixture_name)
    if expected is not None:
        c.check(dict(got) == expected,
                f"[G] rebuilt type multiset matches baseline ({dict(got)} == {expected})")
    for d in _marked(after):
        ft = _formx_type_of(d)
        td = door_types.TYPES.get(ft)
        c.check(td is not None, f"[G] rebuilt door has a known FormX type: {ft!r}")
        if td is None:
            continue
        body = _body(d)
        n_items = len(body.Items or []) if body else 0
        exp = _expected_item_count(td["recipe"])
        c.check(n_items == exp,
                f"[G] {ft} topology matches its recipe ({n_items} == {exp} solids): {d.Name!r}")


# ══════════════════════════════════════════════════════════════════════════════
def test_fixture(path, verbose):
    c = Checker(path.name, verbose)
    print(f"\n{'='*78}\n{path.name}\n{'='*78}")
    sha0 = _sha(path); mt0 = os.path.getmtime(path)
    tmpdir = tempfile.mkdtemp(prefix="doorconv2_test_")
    try:
        out = Path(tmpdir) / f"{path.stem}-D2.ifc"
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
        layer_G_classification(c, path.name, after)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"  → {'PASS' if c.ok else 'FAIL'} ({c.n} checks, {len(c.fails)} failed)")
    return c


def _fixtures(args):
    if args:
        return [Path(a) for a in args]
    out, seen = [], set()
    for dd in FIXTURE_DIRS:
        if not dd.is_dir():
            continue
        for p in sorted(dd.glob("*.ifc")):
            if p.stem.endswith(DC.SUFFIX) or p.name in seen:
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

    print(f"\n{'='*78}\nDOOR CONVERTER v2 — TEST REPORT (ifcopenshell {ifcopenshell.version})\n{'='*78}")
    npass = sum(1 for r in results if r.ok)
    for r in results:
        tag = "PASS" if r.ok else "FAIL"
        detail = "" if r.ok else "  — " + "; ".join(r.fails[:3]) + ("…" if len(r.fails) > 3 else "")
        print(f"  {tag}  {r.name}{detail}")
    print(f"\nRESULT: {'ALL PASS' if npass == len(results) else 'FAIL'}  ({npass}/{len(results)} fixtures)")
    sys.exit(0 if npass == len(results) else 1)


if __name__ == "__main__":
    main()
