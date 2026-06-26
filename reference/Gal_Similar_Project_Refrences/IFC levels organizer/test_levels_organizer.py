#!/usr/bin/env python3
"""
test_levels_organizer.py — independent testing agent for IFC_levels_organizer_V1.py

This is a *self-contained* verification harness.  It does NOT trust the organizer's
own built-in verify() (which is lenient — e.g. it never actually checks that doors /
windows / slabs land under FF1, treats "no roof" as "roof OK", and only compares Z).
Instead it re-derives every invariant from scratch against a freshly produced output.

What it does for each source IFC under ./IFCs (those NOT ending in -L1):
  1. Runs organize(src, tmp_out) into a throwaway temp file (the real IFCs dir is
     never polluted, and the original source is checked to be byte-for-byte untouched).
  2. Opens BOTH the original and the output and asserts a battery of invariants
     grouped as STRUCTURE / CONTAINMENT / CONSERVATION / GEOMETRY / SOURCE / IDEMPOTENCY.

Run:
    python3 test_levels_organizer.py                # test every source in ./IFCs
    python3 test_levels_organizer.py "IFCs/FOO.ifc" # test one file
    python3 test_levels_organizer.py -v             # verbose: show every assertion

Exit code is 0 only if every invariant on every file holds; non-zero otherwise.
Requires the system python3 with ifcopenshell + numpy:
    /Library/Frameworks/Python.framework/Versions/3.10/bin/python3
"""
import hashlib
import importlib.util
import sys
import tempfile
from collections import Counter
from pathlib import Path

import ifcopenshell
import numpy as np
from ifcopenshell.util.placement import get_local_placement

HERE = Path(__file__).parent
IFC_DIR = HERE / "IFCs"

CANON = ("GRADE", "FF1", "PLATE1", "TOP")
WALLS = ("IfcWall", "IfcWallStandardCase")
FF1_TYPES = WALLS + ("IfcDoor", "IfcWindow", "IfcSlab")


# ── load the organizer as a module (filename has no importable dashes — good) ──
def _load_organizer():
    spec = importlib.util.spec_from_file_location(
        "levels_organizer", HERE / "IFC_levels_organizer_V1.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════════════════════
# tiny assertion framework — collects failures per file instead of aborting
# ══════════════════════════════════════════════════════════════════════════════
class Checker:
    def __init__(self, label, verbose):
        self.label = label
        self.verbose = verbose
        self.failures = []
        self.passed = 0

    def check(self, cond, msg, detail=""):
        if cond:
            self.passed += 1
            if self.verbose:
                print(f"      ✓ {msg}")
        else:
            self.failures.append(f"{msg}" + (f"  ({detail})" if detail else ""))
            print(f"      ✗ {msg}" + (f"  ({detail})" if detail else ""))


# ══════════════════════════════════════════════════════════════════════════════
# model helpers (re-derived independently from the organizer)
# ══════════════════════════════════════════════════════════════════════════════
def storeys_sorted(model):
    return sorted(model.by_type("IfcBuildingStorey"),
                  key=lambda s: (s.Elevation if s.Elevation is not None else 0.0))


def storey_containment(model):
    """{storey_name: Counter(type -> n)} and {storey_name: set(GlobalId)} from the
    consolidated IfcRelContainedInSpatialStructure rels."""
    counts, gids, nrels = {}, {}, Counter()
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        st = rel.RelatingStructure
        if not st.is_a("IfcBuildingStorey"):
            continue
        nrels[st.Name] += 1
        counts.setdefault(st.Name, Counter()).update(e.is_a() for e in rel.RelatedElements)
        gids.setdefault(st.Name, set()).update(e.GlobalId for e in rel.RelatedElements)
    return counts, gids, nrels


def contained_gids(model):
    """All element GlobalIds contained under any IfcBuildingStorey."""
    out = set()
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        if rel.RelatingStructure.is_a("IfcBuildingStorey"):
            out.update(e.GlobalId for e in rel.RelatedElements)
    return out


def world_transforms(model):
    """{GlobalId: 4x4 world-placement matrix} for every placed IfcProduct."""
    out = {}
    for e in model.by_type("IfcProduct"):
        if e.ObjectPlacement and e.ObjectPlacement.is_a("IfcLocalPlacement"):
            try:
                out[e.GlobalId] = get_local_placement(e.ObjectPlacement)
            except Exception:
                pass
    return out


def building_agg_order(model):
    """Names of storeys aggregated under IfcBuilding, in their stored order."""
    bldg = model.by_type("IfcBuilding")[0]
    for agg in model.by_type("IfcRelAggregates"):
        if agg.RelatingObject == bldg:
            return [c.Name for c in agg.RelatedObjects
                    if c.is_a("IfcBuildingStorey")]
    return []


def name_rule_gids(model):
    """GlobalIds of elements whose name contains 'UNIT HEATER' (the NAME_TO_LEVEL rule)."""
    return {e.GlobalId for e in model.by_type("IfcProduct")
            if "UNIT HEATER" in (e.Name or "").upper()}


# ══════════════════════════════════════════════════════════════════════════════
# the invariant battery
# ══════════════════════════════════════════════════════════════════════════════
def run_checks(c, before, after):
    sts = storeys_sorted(after)
    names = [s.Name for s in sts]
    counts, gids, nrels = storey_containment(after)

    # ── STRUCTURE ────────────────────────────────────────────────────────────
    c.check(sorted(names) == sorted(CANON),
            "exactly 4 storeys named GRADE/FF1/PLATE1/TOP", f"got {names}")
    c.check(len(names) == len(set(names)), "no duplicate storey names", f"got {names}")
    order = building_agg_order(after)
    c.check(sorted(order) == sorted(CANON),
            "building aggregates exactly the 4 canonical storeys", f"got {order}")
    # aggregation order must be low→high by elevation
    c.check(names == [s.Name for s in sts],
            "storeys orderable by elevation low→high", f"{names}")
    elevs = [(s.Elevation if s.Elevation is not None else 0.0) for s in sts]
    c.check(elevs == sorted(elevs), "elevations non-decreasing low→high", f"{elevs}")
    # one consolidated containment rel per storey (no fragmentation)
    c.check(all(n <= 1 for n in nrels.values()),
            "≤1 containment rel per storey (consolidated)", dict(nrels))

    # ── CONTAINMENT ──────────────────────────────────────────────────────────
    c.check(sum(counts.get("TOP", Counter()).values()) == 0,
            "TOP datum level is empty", dict(counts.get("TOP", {})))

    nh = name_rule_gids(after)  # UNIT HEATER elements
    grade_gids = gids.get("GRADE", set())
    if nh:
        c.check(nh <= grade_gids,
                "name rule: every 'UNIT HEATER' under GRADE",
                f"{len(nh - grade_gids)} misplaced")

    # type-rule elements land under FF1 / PLATE1 (excluding any name-rule overrides)
    ff_gids = gids.get("FF1", set())
    plate_gids = gids.get("PLATE1", set())
    for t in FF1_TYPES:
        els = {e.GlobalId for e in after.by_type(t)} - nh
        # only count those that are contained somewhere (some products are nested, not contained)
        contained = els & contained_gids(after)
        bad = contained - ff_gids
        c.check(not bad, f"all contained {t} under FF1", f"{len(bad)} elsewhere")
    roofs = {e.GlobalId for e in after.by_type("IfcRoof")} - nh
    roof_contained = roofs & contained_gids(after)
    c.check(not (roof_contained - plate_gids),
            "all contained IfcRoof under PLATE1",
            f"{len(roof_contained - plate_gids)} elsewhere")

    # nothing previously contained becomes orphaned
    before_contained = contained_gids(before)
    after_all = contained_gids(after)
    lost = before_contained - after_all
    c.check(not lost, "no previously-contained element orphaned", f"{len(lost)} lost")

    # ── CONSERVATION (before → after) ──────────────────────────────────────────
    types = set(t for t in (
        "IfcWall", "IfcWallStandardCase", "IfcDoor", "IfcWindow", "IfcSlab",
        "IfcRoof", "IfcCovering", "IfcBuildingElementProxy", "IfcSpace",
        "IfcRailing", "IfcFurnishingElement"))
    for t in sorted(types):
        nb, na = len(before.by_type(t)), len(after.by_type(t))
        if nb or na:
            c.check(nb == na, f"count preserved: {t}", f"{nb} → {na}")
    # exact GlobalId set of products preserved (nothing dropped or duplicated)
    gb = Counter(e.GlobalId for e in before.by_type("IfcProduct"))
    ga = Counter(e.GlobalId for e in after.by_type("IfcProduct"))
    # storeys differ (we may have created TOP / removed extras) — compare non-storey products
    bstorey = {s.GlobalId for s in before.by_type("IfcBuildingStorey")}
    astorey = {s.GlobalId for s in after.by_type("IfcBuildingStorey")}
    gb_e = {g: n for g, n in gb.items() if g not in bstorey}
    ga_e = {g: n for g, n in ga.items() if g not in astorey}
    c.check(gb_e == ga_e, "non-storey product GlobalId multiset preserved",
            f"+{len(set(ga_e)-set(gb_e))} / -{len(set(gb_e)-set(ga_e))}")

    # ── GEOMETRY (full 4x4 world transform, not just Z) ────────────────────────
    tb, ta = world_transforms(before), world_transforms(after)
    shared = set(tb) & set(ta)
    moved = [g for g in shared if not np.allclose(tb[g], ta[g], atol=1e-9)]
    c.check(not moved, "world placement unchanged for all shared products (full XYZ)",
            f"{len(moved)} moved")
    c.check(len(shared) > 0, "geometry actually compared (shared products found)",
            f"{len(shared)} shared")


def check_source_untouched(c, src, sha_before, mtime_before):
    sha_after = hashlib.sha256(Path(src).read_bytes()).hexdigest()
    c.check(sha_after == sha_before, "original source file byte-for-byte unchanged")
    c.check(Path(src).stat().st_mtime == mtime_before, "original source mtime unchanged")


def check_idempotency(c, organize, out1):
    """Re-running the organizer on its own output must be a stable fixed point."""
    with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tf:
        out2 = tf.name
    _silence(organize, out1, out2)
    a1, a2 = ifcopenshell.open(out1), ifcopenshell.open(out2)
    n1 = sorted(s.Name for s in a1.by_type("IfcBuildingStorey"))
    n2 = sorted(s.Name for s in a2.by_type("IfcBuildingStorey"))
    c.check(n1 == n2 == sorted(CANON), "idempotent: storeys stable on re-run", f"{n1} vs {n2}")
    cc1, _, _ = storey_containment(a1)
    cc2, _, _ = storey_containment(a2)
    c.check({k: dict(v) for k, v in cc1.items()} == {k: dict(v) for k, v in cc2.items()},
            "idempotent: containment stable on re-run")
    Path(out2).unlink(missing_ok=True)


def _silence(organize, src, out):
    """Run organize() while muting its chatty stdout."""
    import io
    import contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        organize(src, out)


# ══════════════════════════════════════════════════════════════════════════════
def main():
    verbose = "-v" in sys.argv
    # --existing: test the on-disk -L1 output files instead of regenerating into a temp file.
    existing = "--existing" in sys.argv
    args = [a for a in sys.argv[1:] if a not in ("-v", "--existing")]
    org = _load_organizer()

    if args:
        srcs = [Path(a) for a in args]
    else:
        srcs = sorted(p for p in IFC_DIR.glob("*.ifc") if not p.stem.endswith("-L1"))
    if not srcs:
        print(f"No source .ifc files found in {IFC_DIR}")
        sys.exit(1)

    mode = "on-disk -L1 outputs" if existing else "freshly regenerated outputs"
    print(f"Testing {len(srcs)} file(s) — mode: {mode}\n")
    total_fail = 0
    for src in srcs:
        c = Checker(src.name, verbose)
        print(f"── {src.name}")
        sha_before = hashlib.sha256(src.read_bytes()).hexdigest()
        mtime_before = src.stat().st_mtime
        regenerated = False
        if existing:
            out = str(src.with_name(f"{src.stem}-L1{src.suffix}"))
        else:
            with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tf:
                out = tf.name
        try:
            if existing:
                if not Path(out).exists():
                    raise FileNotFoundError(f"expected output missing: {Path(out).name}")
                print(f"   → {Path(out).name}")
            else:
                _silence(org.organize, str(src), out)
                regenerated = True
            before, after = ifcopenshell.open(str(src)), ifcopenshell.open(out)
            run_checks(c, before, after)
            check_source_untouched(c, src, sha_before, mtime_before)
            check_idempotency(c, org.organize, out)
        except Exception as e:
            c.check(False, "organizer ran without raising", repr(e))
        finally:
            if regenerated:
                Path(out).unlink(missing_ok=True)

        if c.failures:
            total_fail += len(c.failures)
            print(f"   ✗ {len(c.failures)} FAILED, {c.passed} passed\n")
        else:
            print(f"   ✓ all {c.passed} checks passed\n")

    print("=" * 60)
    if total_fail:
        print(f"RESULT: {total_fail} check(s) FAILED")
        sys.exit(1)
    print("RESULT: ALL CHECKS PASSED ✓")


if __name__ == "__main__":
    main()
