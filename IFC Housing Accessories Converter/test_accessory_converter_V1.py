#!/usr/bin/env python3
"""
test_accessory_converter_V1.py — acceptance/regression tester for the accessories converter.

The accessories converter is PRESERVE-AND-TAG, so the tester's job is different from the
window/door testers: instead of proving geometry was *rebuilt*, it proves geometry was
*preserved exactly* and that the only change is the added FormX_Accessory psets. The harness, per
fixture, runs ``convert(src, tmp)`` into a throwaway temp, re-derives every invariant independently
(does NOT call the converter's verify()), and checks 6 layers:

  A — Conservation       full entity histogram identical EXCEPT +T IfcPropertySet / +T
                         IfcRelDefinesByProperties / +6T IfcPropertySingleValue; product GlobalId
                         multiset identical; geometry/style entity counts identical.
  B — Preservation       every product's placement matrix + Name/Description/ObjectType +
                         representation fingerprint unchanged; shared IfcRepresentationMap count
                         unchanged; source file byte-identical (sha256 + mtime).
  C — Tag-correctness    exactly one well-formed FormX_Accessory pset per tagged accessory (6
                         correctly-typed props; AccessoryType ∈ TYPES; Movable True; Location ∈
                         {Indoor,Outdoor}; SourceClass == is_a(); CatalogReference non-empty, no
                         trailing :<digits>).
  D — Idempotency        re-run on the output adds nothing (0 new tags; histogram delta zero).
  E — Negative-control TEETH   T == BASELINE_TAGGED and T > 0 (a no-op "tagged nothing" run fails);
                         0 accessories pre-tagged in source; and three self-tests that deliberately
                         corrupt the output and assert the SHIPPED verify() FAILS (broken placement,
                         a stray pset leaked onto a wall, a stray geometry entity).
  F — Classification TEETH   the tagged AccessoryType multiset matches the pinned per-fixture ground
                         truth (forcing every accessory to one type passes A–E but fails here).

Run with python3.11 (ifcopenshell 0.8.5):
    python3.11 test_accessory_converter_V1.py            # all fixtures in INPUT_IFC_FILES_HERE/
    python3.11 test_accessory_converter_V1.py -v         # verbose
    python3.11 test_accessory_converter_V1.py <file.ifc> # one fixture
"""
import hashlib
import importlib.util
import io
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from contextlib import redirect_stdout
from pathlib import Path

import numpy as np
import ifcopenshell
import ifcopenshell.util.placement as ifc_placement

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CONVERTER = HERE / "IFC_accessory_converter_V1.py"
FIXTURE_DIRS = [ROOT / "INPUT_IFC_FILES_HERE"]

sys.path.insert(0, str(HERE))
import accessory_types as at   # single source of truth — TYPES vocab for the correctness checks


def _load_converter():
    spec = importlib.util.spec_from_file_location("accconv1", CONVERTER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DC = _load_converter()
PSET = DC.PSET_NAME
NPROP = DC.N_PROPS


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


def _silent_verify(src, out):
    with redirect_stdout(io.StringIO()):
        return DC.verify(str(src), str(out))


def _formx_pset(el):
    """The element's FormX_Accessory IfcPropertySet (first one), or None."""
    for rel in (getattr(el, "IsDefinedBy", None) or []):
        if rel.is_a("IfcRelDefinesByProperties"):
            pd = rel.RelatingPropertyDefinition
            if pd and pd.is_a("IfcPropertySet") and pd.Name == PSET:
                return pd
    return None


def _marked(model):
    return [p for p in model.by_type("IfcProduct") if _formx_pset(p) is not None]


def _histogram(model):
    h = Counter()
    for e in model:
        h[e.is_a()] += 1
    return h


def _rep_fingerprint(p):
    r = getattr(p, "Representation", None)
    if not r or not getattr(r, "Representations", None):
        return None
    return tuple(sorted((sr.RepresentationIdentifier, sr.RepresentationType, len(sr.Items or []))
                        for sr in r.Representations))


def _pset_values(pset):
    out = {}
    for pr in (pset.HasProperties or []):
        if pr.is_a("IfcPropertySingleValue"):
            nv = pr.NominalValue
            out[pr.Name] = (nv.is_a(), nv.wrappedValue) if nv is not None else (None, None)
    return out


# ══════════════════════════════════════════════════════════════════════════════
# Invariant layers
# ══════════════════════════════════════════════════════════════════════════════
GEOM_STYLE = [
    "IfcShapeRepresentation", "IfcProductDefinitionShape", "IfcMappedItem",
    "IfcRepresentationMap", "IfcStyledItem", "IfcSurfaceStyle", "IfcSurfaceStyleRendering",
    "IfcExtrudedAreaSolid", "IfcFacetedBrep", "IfcAdvancedBrep", "IfcPolyloop",
    "IfcCartesianPoint", "IfcOwnerHistory",
]


def layer_A_conservation(c, before, after, T):
    hb, ha = _histogram(before), _histogram(after)
    added = {"IfcPropertySet": T, "IfcRelDefinesByProperties": T, "IfcPropertySingleValue": NPROP * T}
    bad = []
    for t in set(hb) | set(ha):
        if ha.get(t, 0) - hb.get(t, 0) != added.get(t, 0):
            bad.append(f"{t} {hb.get(t,0)}->{ha.get(t,0)}")
    c.check(not bad, f"[A] histogram identical except +{T}/+{T}/+{NPROP*T} pset/rel/value "
                     f"({'; '.join(bad[:4]) if bad else 'ok'})")
    gb = sorted(p.GlobalId for p in before.by_type("IfcProduct"))
    ga = sorted(p.GlobalId for p in after.by_type("IfcProduct"))
    c.check(gb == ga, "[A] product GlobalId multiset identical")
    for t in GEOM_STYLE:
        nb, na = len(DC._safe_by_type(before, t)), len(DC._safe_by_type(after, t))
        if nb or na:
            c.check(nb == na, f"[A] geometry/style count {t} unchanged ({nb}->{na})")


def layer_B_preservation(c, before, after, src, sha0, mtime0):
    bpl = {p.GlobalId: ifc_placement.get_local_placement(p.ObjectPlacement)
           for p in before.by_type("IfcProduct") if p.ObjectPlacement}
    moved = 0
    for p in after.by_type("IfcProduct"):
        m0 = bpl.get(p.GlobalId)
        if m0 is not None and p.ObjectPlacement is not None:
            if not np.allclose(m0, ifc_placement.get_local_placement(p.ObjectPlacement), atol=1e-9):
                moved += 1
    c.check(moved == 0, f"[B] all product placements unchanged ({moved} moved)")

    # Name / Description / ObjectType verbatim unchanged on EVERY product (we never edit them).
    attrs0 = {p.GlobalId: (p.Name, p.Description, getattr(p, "ObjectType", None))
              for p in before.by_type("IfcProduct")}
    changed = 0
    for p in after.by_type("IfcProduct"):
        a0 = attrs0.get(p.GlobalId)
        if a0 is not None and a0 != (p.Name, p.Description, getattr(p, "ObjectType", None)):
            changed += 1
    c.check(changed == 0, f"[B] Name/Description/ObjectType unchanged on all products ({changed} changed)")

    # representation fingerprint unchanged (geometry shape preserved).
    fp0 = {p.GlobalId: _rep_fingerprint(p) for p in before.by_type("IfcProduct")}
    rbad = 0
    for p in after.by_type("IfcProduct"):
        if p.GlobalId in fp0 and fp0[p.GlobalId] != _rep_fingerprint(p):
            rbad += 1
    c.check(rbad == 0, f"[B] representation fingerprint unchanged ({rbad} changed)")

    c.check(len(DC._safe_by_type(before, "IfcRepresentationMap")) ==
            len(DC._safe_by_type(after, "IfcRepresentationMap")),
            "[B] shared IfcRepresentationMap count unchanged")

    c.check(_sha(src) == sha0 and os.path.getmtime(src) == mtime0, "[B] source file untouched")


def layer_C_tag_correctness(c, after):
    marked = _marked(after)
    for p in marked:
        psets = [rel.RelatingPropertyDefinition for rel in (p.IsDefinedBy or [])
                 if rel.is_a("IfcRelDefinesByProperties")
                 and rel.RelatingPropertyDefinition.is_a("IfcPropertySet")
                 and rel.RelatingPropertyDefinition.Name == PSET]
        if not c.check(len(psets) == 1, f"[C] exactly one FormX_Accessory pset: {p.Name!r}"):
            continue
        vals = _pset_values(psets[0])
        c.check(len(vals) == NPROP, f"[C] {NPROP} properties present: {p.Name!r} ({len(vals)})")
        at_type = vals.get("AccessoryType", (None, None))
        c.check(at_type[0] == "IfcLabel" and at_type[1] in at.TYPES,
                f"[C] AccessoryType ∈ TYPES: {p.Name!r} → {at_type[1]!r}")
        c.check(vals.get("Movable", (None, None))[1] is True, f"[C] Movable True: {p.Name!r}")
        loc = vals.get("Location", (None, None))
        c.check(loc[1] in ("Indoor", "Outdoor"), f"[C] Location valid: {p.Name!r} → {loc[1]!r}")
        sc = vals.get("SourceClass", (None, None))
        c.check(sc[1] == p.is_a(), f"[C] SourceClass == is_a(): {p.Name!r} ({sc[1]!r} vs {p.is_a()!r})")
        cref = vals.get("CatalogReference", (None, None))
        c.check(cref[0] == "IfcIdentifier" and bool(cref[1]) and not re.search(r":\d+$", cref[1] or ""),
                f"[C] CatalogReference clean: {p.Name!r} → {cref[1]!r}")
    return marked


def layer_D_idempotency(c, out):
    out2 = str(out) + ".rerun.ifc"
    _silent_convert(out, out2)
    m1, m2 = ifcopenshell.open(str(out)), ifcopenshell.open(out2)
    n1, n2 = len(_marked(m1)), len(_marked(m2))
    c.check(n1 == n2, f"[D] re-run tags nothing new ({n1} → {n2})")
    h1, h2 = _histogram(m1), _histogram(m2)
    c.check(h1 == h2, "[D] re-run histogram delta is zero")
    try:
        os.remove(out2)
    except OSError:
        pass


# Pinned per-fixture baselines (recomputed from the converter's actual output this session).
BASELINE_TAGGED = {
    "LEXFORD_OFFICE-C1.ifc": 9,
    "SAN_JUAN_CYPRESS_-_AUG_2-W1-L1.ifc": 7,
    "Sunflower_Sunflower_A_Sunflower_A_.ifc": 36,
    "Turnberry_927_TURNBERRY_ADU-DEC_2_2025-C1.ifc": 51,
}

BASELINE_TYPES = {
    "LEXFORD_OFFICE-C1.ifc": {
        "PLANT": 2, "SEATING": 2, "TABLE": 2, "STORAGE": 1, "SANITARY_FIXTURE": 1, "DECOR": 1},
    "SAN_JUAN_CYPRESS_-_AUG_2-W1-L1.ifc": {
        "SEATING": 1, "TABLE": 1, "STORAGE": 1, "DECOR": 1, "GENERIC": 3},
    "Sunflower_Sunflower_A_Sunflower_A_.ifc": {
        "SEATING": 2, "TABLE": 4, "STORAGE": 10, "BED": 1, "APPLIANCE": 2,
        "SANITARY_FIXTURE": 4, "DECOR": 1, "GENERIC": 12},
    "Turnberry_927_TURNBERRY_ADU-DEC_2_2025-C1.ifc": {
        "PLANT": 2, "SEATING": 5, "TABLE": 7, "STORAGE": 15, "APPLIANCE": 5,
        "SANITARY_FIXTURE": 5, "LIGHTING": 2, "DECOR": 5, "OUTDOOR_FURNITURE": 2, "GENERIC": 3},
}


def _accessory_type_of(p):
    pset = _formx_pset(p)
    if not pset:
        return None
    return _pset_values(pset).get("AccessoryType", (None, None))[1]


def _break_placement(model):
    """Move a NON-accessory product (a wall) — a correct converter never does this."""
    for w in model.by_type("IfcWall"):
        pl = w.ObjectPlacement
        rp = getattr(pl, "RelativePlacement", None) if pl else None
        if rp and getattr(rp, "Location", None):
            loc = list(rp.Location.Coordinates)
            rp.Location = model.create_entity("IfcCartesianPoint",
                                              Coordinates=(loc[0] + 5.0, loc[1], loc[2]))
            return True
    return False


def _break_leak(model):
    """Leak a stray (non-FormX) pset onto a NON-accessory — the keystone IsDefinedBy/histogram teeth."""
    owner = next(iter(model.by_type("IfcOwnerHistory")), None)
    for w in model.by_type("IfcWall"):
        DC.sa.add_pset(model, owner, "Stray_Pset",
                       [DC.sa.psv(model, "X", "y", "IfcLabel")], w)
        return True
    return False


def _break_geometry(model):
    """Add a stray geometry entity — the geometry-count teeth."""
    model.create_entity("IfcCartesianPoint", Coordinates=(0.0, 0.0, 0.0))
    return True


def layer_E_negative_control(c, fixture_name, before, after, src, out):
    T = len(_marked(after))
    c.check(T > 0, f"[E] converter tagged at least one accessory (not a no-op) [{T}]")
    expected = BASELINE_TAGGED.get(fixture_name)
    if expected is not None:
        c.check(T == expected, f"[E] tagged count matches baseline ({T} == {expected})")
    c.check(len(_marked(before)) == 0,
            f"[E] no accessory was pre-tagged in source — test has teeth ({len(_marked(before))})")

    # Self-tests: corrupt the output three ways and assert the SHIPPED verify() FAILS on each.
    for label, mutate in (("broken placement", _break_placement),
                          ("leaked stray pset", _break_leak),
                          ("stray geometry entity", _break_geometry)):
        tmp = str(out) + f".broken.ifc"
        m = ifcopenshell.open(str(out))
        if not mutate(m):
            continue
        m.write(tmp)
        passed = _silent_verify(src, tmp)
        c.check(passed is False, f"[E] verify() catches {label} (teeth)")
        try:
            os.remove(tmp)
        except OSError:
            pass


def layer_F_classification(c, fixture_name, after):
    got = dict(Counter(_accessory_type_of(p) for p in _marked(after)))
    expected = BASELINE_TYPES.get(fixture_name)
    if expected is not None:
        c.check(got == expected, f"[F] AccessoryType multiset matches baseline ({got} == {expected})")
        c.check(sum(expected.values()) == BASELINE_TAGGED.get(fixture_name),
                "[F] baseline multiset sums to BASELINE_TAGGED")
    else:
        c.check(len(got) > 0, f"[F] at least one type assigned [{got}]")


# ══════════════════════════════════════════════════════════════════════════════
def test_fixture(path, verbose):
    c = Checker(path.name, verbose)
    print(f"\n{'='*78}\n{path.name}\n{'='*78}")
    sha0 = _sha(path); mt0 = os.path.getmtime(path)
    tmpdir = tempfile.mkdtemp(prefix="accconv1_test_")
    try:
        out = Path(tmpdir) / f"{path.stem}-ACC1.ifc"
        _silent_convert(path, out)

        before = ifcopenshell.open(str(path))
        after = ifcopenshell.open(str(out))
        marked = _marked(after)
        T = len(marked)
        print(f"  products: {len(after.by_type('IfcProduct'))} | {T} tagged | schema {after.schema}")

        layer_A_conservation(c, before, after, T)
        layer_B_preservation(c, before, after, str(path), sha0, mt0)
        layer_C_tag_correctness(c, after)
        layer_D_idempotency(c, out)
        layer_E_negative_control(c, path.name, before, after, str(path), out)
        layer_F_classification(c, path.name, after)
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

    print(f"\n{'='*78}\nACCESSORIES CONVERTER v1 — TEST REPORT (ifcopenshell {ifcopenshell.version})\n{'='*78}")
    npass = sum(1 for r in results if r.ok)
    for r in results:
        tag = "PASS" if r.ok else "FAIL"
        detail = "" if r.ok else "  — " + "; ".join(r.fails[:3]) + ("…" if len(r.fails) > 3 else "")
        print(f"  {tag}  {r.name}{detail}")
    print(f"\nRESULT: {'ALL PASS' if npass == len(results) else 'FAIL'}  ({npass}/{len(results)} fixtures)")
    sys.exit(0 if npass == len(results) else 1)


if __name__ == "__main__":
    main()
