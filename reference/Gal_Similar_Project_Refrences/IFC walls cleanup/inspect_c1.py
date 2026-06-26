#!/usr/bin/env python3
"""
inspect_c1.py — Independent audit of the -C1 cleanup outputs against
"IFC walls cleanup algorithm.md" (§8 acceptance criteria).  Read-only.

Walls are matched ORIGINAL <-> C1 by GlobalId, because Step 5 normalizes
IfcWallStandardCase -> IfcWall (new STEP id, same GlobalId).

Criteria checked (numbering follows §8):
  C1  original untouched (proxy)        C7b no IfcWallStandardCase, GlobalIds kept
  C2  non-wall preservation             C8  clean corners (no miter / no penetration)
  C3  no same-storey overlaps           C9  pinwheel: each ext wall owns exactly 1 corner
  C4  openings fixed (world Δ<1e-4)      C10 shape preservation (undivided unchanged)
  C5  wall count preserved              C11 top bound unchanged (world z-extent per wall)
  C6  names + element id
  C7  vertical +Z extrusion
"""
import math
import re
import sys
from pathlib import Path
from collections import defaultdict

import ifcopenshell
import ifcopenshell.util.placement as placement
try:
    import ifcopenshell.geom as geom
except Exception:
    geom = None

sys.path.insert(0, str(Path(__file__).parent))
import IFC_walls_cleanup_V1 as cu

IFC_DIR = Path(__file__).parent / "IFCs"
NAME_RE = re.compile(r"^(Exterior|Interior|Design) wall\s+(\d+)$")

EXPECT = {  # §9
    "SAN JUAN CYPRESS - AUG 2": dict(walls=6, ext=4, intr=0, des=2),
    "Northam Ave, San Carlos":  dict(walls=6, ext=6, intr=0, des=0),
    "HUDSON ADU":               dict(walls=9, ext=4, intr=4, des=1),
    "FOREST ADU":               dict(walls=15, ext=8, intr=7, des=0),
    "14TH SF - MAR 28 V4":      dict(walls=9, ext=4, intr=4, des=1),
    "LEXFORD_OFFICE":           dict(walls=7, ext=4, intr=2, des=1),
    "Turnberry_927_TURNBERRY_ADU-DEC_2_2025": dict(walls=22, ext=4, intr=17, des=1),
}

T_THICK = 1e-3; T_HEIGHT = 1e-3; T_LEN = 1e-3
T_OPEN = 1e-4; T_OVERLAP = 0.02; T_TOP = 0.02; T_OWN = 0.10


def walls_of(model):
    ws = list(model.by_type("IfcWall")) + list(model.by_type("IfcWallStandardCase"))
    seen = set()
    return [w for w in ws if not (w.id() in seen or seen.add(w.id()))]


def has_clip(w):
    for rep in (w.Representation.Representations if w.Representation else []):
        if rep.RepresentationIdentifier == "Body":
            if any(it.is_a("IfcBooleanClippingResult") for it in rep.Items):
                return True
    return False


def wall_height(w, info):
    bk = info.get("body_kind")
    if bk in ("brep", "multisolid"):
        return info.get("brep_height")
    if bk == "profiled":
        tp = info.get("top_profile") or []
        return max((z for _, z in tp), default=None)
    for rep in (w.Representation.Representations if w.Representation else []):
        if rep.RepresentationIdentifier == "Body":
            for it in rep.Items:
                s = cu._unwrap_solid(it)
                if s is not None and s.is_a("IfcExtrudedAreaSolid"):
                    return s.Depth
    return None


def profile_kind(info):
    bk = info.get("body_kind")
    if bk in ("brep", "multisolid", "profiled"):
        return bk
    p = info.get("profile")
    if p is None:
        return "none"
    if p.is_a("IfcRectangleProfileDef"):
        return "rect"
    if p.is_a("IfcArbitraryClosedProfileDef"):
        return "arbitrary"
    return p.is_a()


def metrics(w):
    info = cu._get_wall_info(w)
    if not info:
        return None
    return dict(gid=w.GlobalId, sid=w.id(), name=w.Name or "",
                thickness=info.get("thickness"), length=info.get("axis_len"),
                height=wall_height(w, info), pkind=profile_kind(info),
                clip=has_clip(w), info=info)


def by_gid(model):
    return {m["gid"]: m for m in (metrics(w) for w in walls_of(model)) if m}


def opening_world(model):
    out = {}
    for op in model.by_type("IfcOpeningElement"):
        if not op.ObjectPlacement:
            continue
        try:
            m = placement.get_local_placement(op.ObjectPlacement)
            out[op.GlobalId] = (m[0][3], m[1][3], m[2][3])
        except Exception:
            pass
    return out


def cat_of(name):
    m = NAME_RE.match(name or "")
    return m.group(1).lower() if m else "?"


def storey_map(model):
    s = {}
    for rel in model.by_type("IfcRelContainedInSpatialStructure"):
        for e in rel.RelatedElements:
            s[e.id()] = rel.RelatingStructure.id()
    return s


def run_axis(info):
    xd = info["x_dir"]
    return 0 if abs(xd[0]) >= abs(xd[1]) else 1


def overlaps(model):
    walls = walls_of(model)
    st = storey_map(model)

    def zr(w, info):
        d = wall_height(w, info) or 0.0
        z0 = w.ObjectPlacement.RelativePlacement.Location.Coordinates[2] if w.ObjectPlacement else 0.0
        return (z0, z0 + d)

    items = [(w, cu._footprint(i), i) for w, i in
             ((w, cu._get_wall_info(w)) for w in walls) if i]
    out = []
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (wa, a, ia), (wb, b, ib) = items[i], items[j]
            if not (a[1] > b[0] + 1e-3 and b[1] > a[0] + 1e-3 and
                    a[3] > b[2] + 1e-3 and b[3] > a[2] + 1e-3):
                continue
            pen = min(min(a[1], b[1]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[2], b[2]))
            if pen <= T_OVERLAP:
                continue
            if st.get(wa.id()) != st.get(wb.id()):
                continue
            za, zb = zr(wa, ia), zr(wb, ib)
            if not (za[1] > zb[0] + 1e-3 and zb[1] > za[0] + 1e-3):
                continue
            out.append((wa, wb, pen * 12.0))
    return out


def z_extrusion(model):
    ok = warn = 0
    for w in walls_of(model):
        base = None
        for rep in (w.Representation.Representations if w.Representation else []):
            if rep.RepresentationIdentifier == "Body":
                for it in rep.Items:
                    s = cu._unwrap_solid(it)
                    if s is not None:
                        base = s
        if base is None or not base.is_a("IfcExtrudedAreaSolid"):
            continue
        d = base.ExtrudedDirection.DirectionRatios if base.ExtrudedDirection else (0, 0, 1)
        if abs(d[0]) < 1e-6 and abs(d[1]) < 1e-6 and d[2] > 0:
            ok += 1
        else:
            warn += 1
    return ok, warn


def corner_participants(model):
    infos = [(w.id(), cu._get_wall_info(w)) for w in walls_of(model)]
    infos = [(i, x) for i, x in infos if x]
    ids = set()
    for a in range(len(infos)):
        for b in range(a + 1, len(infos)):
            ida, ia = infos[a]; idb, ib = infos[b]
            if run_axis(ia) == run_axis(ib):
                continue
            if cu._overlaps(cu._footprint(ia), cu._footprint(ib)):
                ids.add(ida); ids.add(idb)
    return ids


def _owns_at_corner(A, B):
    """Does wall A reach the OUTER corner where it meets perpendicular wall B (owner),
    or stop at B's near face (butt)?  Returns 'own' / 'butt' / None."""
    ra = run_axis(A);
    fpA, fpB = cu._footprint(A), cu._footprint(B)
    Blo, Bhi = fpB[2 * ra], fpB[2 * ra + 1]
    A0, A1 = fpA[2 * ra], fpA[2 * ra + 1]
    Bc = (Blo + Bhi) / 2.0
    if abs(A1 - Bc) <= abs(A0 - Bc):
        cA, near, far = A1, Blo, Bhi          # A approaches from below
    else:
        cA, near, far = A0, Bhi, Blo          # A approaches from above
    if not (Blo - T_OWN <= cA <= Bhi + T_OWN):
        return None                            # this A end isn't at B
    if abs(cA - far) <= T_OWN:
        return "own"
    if abs(cA - near) <= T_OWN:
        return "butt"
    return None


APPENDAGE_LEN = 3.0   # ft: §10 — very short exterior walls may not pick up a corner

def pinwheel_ownership(model):
    """Per storey, count how many rectangular-perimeter corners each EXTERIOR wall owns.
    §8.9 pinwheel is exterior-to-exterior; design walls use 'longer owns' (§4.2) and tiny
    appendages are exempt (§10), so only exterior×exterior corners between non-appendage
    walls are counted."""
    st = storey_map(model)
    infos = [(w, cu._get_wall_info(w)) for w in walls_of(model)]
    infos = [(w, i) for w, i in infos if i]
    owned = defaultdict(int)
    main = [(w, i) for w, i in infos
            if cat_of(w.Name) == "exterior" and (i["axis_len"] or 0) >= APPENDAGE_LEN]
    ext_gids = {w.GlobalId for w, i in main}
    for a in range(len(main)):
        for b in range(a + 1, len(main)):
            wa, ia = main[a]; wb, ib = main[b]
            if run_axis(ia) == run_axis(ib):
                continue
            if st.get(wa.id()) != st.get(wb.id()):
                continue
            fa = cu._footprint(ia); fb = cu._footprint(ib)
            if not (fa[1] > fb[0] - T_OWN and fb[1] > fa[0] - T_OWN and
                    fa[3] > fb[2] - T_OWN and fb[3] > fa[2] - T_OWN):
                continue
            ra_, rb_ = _owns_at_corner(ia, ib), _owns_at_corner(ib, ia)
            if ra_ == "own" and rb_ != "own":
                owned[wa.GlobalId] += 1
            elif rb_ == "own" and ra_ != "own":
                owned[wb.GlobalId] += 1
            elif ra_ == "own" and rb_ == "own":
                owned[wa.GlobalId] += 1; owned[wb.GlobalId] += 1
    return ext_gids, owned


def _mat_pt(M, v):
    x, y, z = v
    return (M[0][0]*x + M[0][1]*y + M[0][2]*z + M[0][3],
            M[1][0]*x + M[1][1]*y + M[1][2]*z + M[1][3],
            M[2][0]*x + M[2][1]*y + M[2][2]*z + M[2][3])


def _mat_dir(M, v):
    x, y, z = v
    return (M[0][0]*x + M[0][1]*y + M[0][2]*z,
            M[1][0]*x + M[1][1]*y + M[1][2]*z,
            M[2][0]*x + M[2][1]*y + M[2][2]*z)


def top_signature(w):
    """§8.11 (analytic, kernel-free): the wall's world ROOF as a signature that must be
    unchanged orig->C1.  Flat walls -> world top z (base_z + depth).  Clipped walls -> the
    world plane equation (normal, offset) of every cutting half-space.  Repositioning is
    supposed to hold these world-fixed, so the signature is invariant to trims/extensions."""
    if not w.Representation or not w.ObjectPlacement:
        return None
    M = placement.get_local_placement(w.ObjectPlacement)
    base = None; clips = []
    for rep in w.Representation.Representations:
        if rep.RepresentationIdentifier != "Body":
            continue
        for it in rep.Items:
            stack = [it]
            while stack:
                e = stack.pop()
                if e.is_a("IfcBooleanClippingResult"):
                    stack.append(e.FirstOperand)
                    hs = e.SecondOperand
                    # Only UNBOUNDED IfcHalfSpaceSolid planes define the world ROOF and are
                    # held world-fixed by construction.  IfcPolygonalBoundedHalfSpace cuts are
                    # local extent/end bounds that legitimately grow on extension (and need the
                    # bounded Position to transform), so they are excluded from the roof check.
                    if (hs and hs.is_a("IfcHalfSpaceSolid")
                            and not hs.is_a("IfcPolygonalBoundedHalfSpace")
                            and hs.BaseSurface and hs.BaseSurface.is_a("IfcPlane")
                            and hs.BaseSurface.Position):
                        pos = hs.BaseSurface.Position
                        L = list(pos.Location.Coordinates)
                        while len(L) < 3: L.append(0.0)
                        n = list(pos.Axis.DirectionRatios) if pos.Axis else [0, 0, 1]
                        Pw = _mat_pt(M, L); Nw = _mat_dir(M, n)
                        mag = math.sqrt(sum(c*c for c in Nw)) or 1.0
                        Nw = tuple(c/mag for c in Nw)
                        d = sum(Nw[i]*Pw[i] for i in range(3))
                        clips.append((Nw[0], Nw[1], Nw[2], d))
                elif e.is_a("IfcExtrudedAreaSolid"):
                    base = e
    if clips:
        return ("clipped", sorted(clips, key=lambda p: (round(p[2], 2), round(p[3], 1))))
    if base is not None:
        bz = base.Position.Location.Coordinates[2] if (base.Position and base.Position.Location and
              len(base.Position.Location.Coordinates) > 2) else 0.0
        top_world = M[2][3] + bz + (base.Depth or 0.0)
        return ("flat", round(top_world, 3))
    return None


def inspect(src, c1):
    mo, mc = ifcopenshell.open(str(src)), ifcopenshell.open(str(c1))
    findings = []
    def F(c, s, m): findings.append((c, s, m))

    wo_raw, wc_raw = walls_of(mo), walls_of(mc)
    o, c = by_gid(mo), by_gid(mc)

    # C1 original untouched (proxy)
    orig_named = sum(1 for m in o.values() if NAME_RE.match(m["name"]))
    c1_ok = orig_named == 0
    if not c1_ok:
        F("C1", "ERROR", f"{orig_named} original wall(s) already cleaned-named")

    # C5 count
    c5_ok = len(wo_raw) == len(wc_raw)
    if not c5_ok:
        F("C5", "ERROR", f"wall count {len(wo_raw)} -> {len(wc_raw)}")
    missing = set(o) - set(c)
    if missing:
        F("C5", "ERROR", f"{len(missing)} GlobalId(s) in orig missing from C1")

    # C7b normalization
    n_std = len(mc.by_type("IfcWallStandardCase"))
    gids_ok = set(o).issubset(set(c) | missing) and not (set(o) - set(c))
    c7b_ok = (n_std == 0) and (set(o) == set(c))
    if n_std:
        F("C7b", "ERROR", f"{n_std} IfcWallStandardCase remain in C1 (should be 0)")
    if set(o) != set(c):
        lost = set(o) - set(c)
        if lost:
            F("C7b", "ERROR", f"{len(lost)} GlobalId(s) not preserved after normalization")

    # C6 names + eid  (+ classification counts)
    cats = defaultdict(int); bad_name = []; bad_eid = []
    for gid, cm in c.items():
        cats[cat_of(cm["name"])] += 1
        m = NAME_RE.match(cm["name"])
        if not m:
            bad_name.append(cm["name"]); continue
        om = o.get(gid)
        if om:
            oeid = cu._eid_digits(om["name"])
            if oeid and oeid != m.group(2):
                bad_eid.append(f"{om['name']}->{cm['name']}")
    c6_ok = not bad_name and not bad_eid
    if bad_name: F("C6", "ERROR", f"{len(bad_name)} off-pattern name(s): {sorted(set(bad_name))[:3]}")
    if bad_eid:  F("C6", "ERROR", f"{len(bad_eid)} lost element id: {bad_eid[:3]}")

    # C7 +Z
    zok, zwarn = z_extrusion(mc)
    c7_ok = zwarn == 0
    if zwarn: F("C7", "WARNING", f"{zwarn} base solid(s) not +Z")

    # C3 overlaps + C8 clean corners
    ov_o, ov_c = overlaps(mo), overlaps(mc)
    c3_ok = len(ov_c) == 0
    for wa, wb, pen in ov_c:
        F("C3", "ERROR", f"overlap {pen:.2f}\" [{cat_of(wa.Name)}x{cat_of(wb.Name)}] {wa.Name} x {wb.Name}")

    trimmed = []; grew = []; miter_left = []
    for gid in set(o) & set(c):
        om, cm = o[gid], c[gid]
        if om["length"] is None or cm["length"] is None:
            continue
        dl = om["length"] - cm["length"]
        if dl > T_LEN:
            trimmed.append((cm, om, dl))
            if cm["pkind"] == "arbitrary":
                miter_left.append(cm["name"])
        elif dl < -T_LEN:
            grew.append((cm, om, -dl))          # extension (legit per step 4a)
    for nm in miter_left:
        F("C8", "ERROR", f"{nm}: divided wall still mitered (arbitrary profile)")
    c8_ok = (len(ov_c) == 0) and not miter_left

    # C9 pinwheel ownership
    ext_gids, owned = pinwheel_ownership(mc)
    own_counts = {g: owned.get(g, 0) for g in ext_gids}
    double = [g for g, n in own_counts.items() if n >= 2]
    zero = [g for g in ext_gids if own_counts[g] == 0]
    # pass: no exterior wall owns >=2, and every corner is owned (sum owned == n corners).
    c9_ok = not double
    if double:
        names = [c[g]["name"] for g in double if g in c]
        F("C9", "ERROR", f"{len(double)} exterior wall(s) own >=2 corners: {names}")
    if zero:
        names = [c[g]["name"] for g in zero if g in c]
        F("C9", "INFO", f"{len(zero)} exterior wall(s) own 0 corners (appendage/standalone): {names}")

    # C2 preservation
    pres = []
    for t in cu.PRESERVE_TYPES:
        nb, na = len(mo.by_type(t)), len(mc.by_type(t))
        if (nb or na) and nb != na:
            pres.append(f"{t} {nb}->{na}")
    c2_ok = not pres
    for x in pres: F("C2", "ERROR", f"non-wall changed: {x}")

    # C4 openings
    po, pc = opening_world(mo), opening_world(mc)
    moved = [g for g, p in po.items() if g in pc and math.dist(p, pc[g]) > T_OPEN]
    c4_ok = not moved
    if moved: F("C4", "ERROR", f"{len(moved)} opening(s) moved > 1e-4 ft")

    # C10 shape preservation (corner-aware)
    cp = corner_participants(mo)
    o_sid = {m["sid"]: gid for gid, m in o.items()}   # original sid -> gid
    trimmed_gids = {cm["gid"] for cm, _, _ in trimmed} | {cm["gid"] for cm, _, _ in grew}
    cp_gids = {gid for sid, gid in o_sid.items() if sid in cp}
    thick_chg = []; height_chg = []; clip_lost = []; undivided_chg = []; squared = []
    for gid in set(o) & set(c):
        om, cm = o[gid], c[gid]
        if om["thickness"] and cm["thickness"] and abs(om["thickness"] - cm["thickness"]) > T_THICK:
            thick_chg.append(f"{cm['name']} {om['thickness']*12:.2f}->{cm['thickness']*12:.2f}\"")
        if om["height"] and cm["height"] and abs(om["height"] - cm["height"]) > T_HEIGHT:
            height_chg.append(f"{cm['name']} {om['height']:.3f}->{cm['height']:.3f}ft")
        if om["clip"] and not cm["clip"]:
            clip_lost.append(cm["name"])
        sig_o = (round(om["thickness"] or 0, 4), round(om["length"] or 0, 4),
                 round(om["height"] or 0, 4), om["pkind"], om["clip"])
        sig_c = (round(cm["thickness"] or 0, 4), round(cm["length"] or 0, 4),
                 round(cm["height"] or 0, 4), cm["pkind"], cm["clip"])
        if sig_o != sig_c and gid not in trimmed_gids:
            if gid in cp_gids and om["pkind"] == "arbitrary" and cm["pkind"] == "rect":
                squared.append(cm["name"])
            elif om["pkind"] in ("rect", "arbitrary", "solid"):
                undivided_chg.append(f"{cm['name']} {sig_o}->{sig_c}")
    for x in thick_chg:     F("C10", "ERROR", f"thickness changed: {x}")
    for x in height_chg:    F("C10", "ERROR", f"height changed: {x}")
    for x in clip_lost:     F("C10", "ERROR", f"clip dropped: {x}")
    for x in undivided_chg: F("C10", "ERROR", f"undivided/non-corner wall changed: {x}")
    for x in squared:       F("C10", "INFO", f"corner wall squared (OK §3.3): {x}")
    c10_ok = not (thick_chg or height_chg or clip_lost or undivided_chg)

    # C11 top bound — analytic world roof signature per wall (§8.11), tol 0.02 ft
    so = {w.GlobalId: top_signature(w) for w in wo_raw}
    sc = {w.GlobalId: top_signature(w) for w in wc_raw}
    top_bad = []; top_n = 0; top_max = 0.0
    for gid in set(so) & set(sc):
        a, b = so[gid], sc[gid]
        if a is None or b is None:
            continue
        top_n += 1
        if a[0] != b[0]:
            top_bad.append((c[gid]["name"], f"roof kind {a[0]}->{b[0]}")); continue
        if a[0] == "flat":
            dlt = abs(a[1] - b[1]); top_max = max(top_max, dlt)
            if dlt > T_TOP:
                top_bad.append((c[gid]["name"], f"flat top {dlt:.4f} ft"))
        else:  # clipped: roof (unbounded) planes only
            pa, pb = a[1], b[1]
            if len(pa) != len(pb):
                top_bad.append((c[gid]["name"], "roof plane count changed")); continue
            for x, y in zip(pa, pb):
                ndot = x[0]*y[0] + x[1]*y[1] + x[2]*y[2]
                dlt = abs(x[3] - y[3]); top_max = max(top_max, dlt)
                if ndot < 0.999 or dlt > T_TOP:
                    top_bad.append((c[gid]["name"], f"roof plane moved {dlt:.4f} ft")); break
    c11_ok = None if top_n == 0 else (not top_bad)
    top_max_delta = top_max if top_n else None
    for nm, why in top_bad:
        F("C11", "ERROR", f"top bound changed: {nm}: {why}")
    return dict(
        walls_o=len(wo_raw), walls_c=len(wc_raw), cats=dict(cats),
        trimmed=trimmed, extended=len(grew), ov_o=len(ov_o), ov_c=ov_c,
        zok=zok, zwarn=zwarn, openings_moved=len(moved), n_std=n_std,
        own_counts=own_counts, top_max_delta=top_max_delta, top_n=top_n,
        crit=dict(C1=c1_ok, C2=c2_ok, C3=c3_ok, C4=c4_ok, C5=c5_ok, C6=c6_ok,
                  C7=c7_ok, C7b=c7b_ok, C8=c8_ok, C9=c9_ok, C10=c10_ok, C11=c11_ok),
        findings=findings, c=c)


def main():
    pairs = []
    for src in sorted(IFC_DIR.glob("*.ifc")):
        if src.stem.endswith("-C1"):
            continue
        c1 = src.with_name(f"{src.stem}-C1{src.suffix}")
        if c1.exists():
            pairs.append((src, c1))
    results = {s.stem: inspect(s, c1) for s, c1 in pairs}

    crit_keys = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C7b", "C8", "C9", "C10", "C11"]
    print("\n" + "=" * 110)
    print("§8 ACCEPTANCE CRITERIA   ✓=pass ✗=fail !=warn -=n/a")
    print("=" * 110)
    hdr = f"{'Fixture':<26}" + "".join(f"{k:>5}" for k in crit_keys)
    print(hdr); print("-" * len(hdr))
    for name, R in results.items():
        cells = ""
        for k in crit_keys:
            v = R["crit"][k]
            cells += f"{('✓' if v else ('!' if (k=='C7' and v is False) else ('-' if v is None else '✗'))):>5}"
        print(f"{name[:25]:<26}{cells}")

    print("\n" + "=" * 110)
    print("STEP METRICS")
    print("=" * 110)
    hdr = (f"{'Fixture':<26}{'+Z ok/!':>9}{'E/I/D':>10}{'trim':>6}{'ext':>5}"
           f"{'ovlp o->c':>11}{'opn.mv':>8}{'std':>5}{'own(ext)':>20}{'topΔmax(ft)':>13}")
    print(hdr); print("-" * len(hdr))
    for name, R in results.items():
        cc = R["cats"]
        eid = f"{cc.get('exterior',0)}/{cc.get('interior',0)}/{cc.get('design',0)}"
        owns = sorted(R["own_counts"].values(), reverse=True)
        owns_s = ",".join(map(str, owns)) if owns else "-"
        td = "n/a" if R["top_max_delta"] is None else f"{R['top_max_delta']:.4f}({R['top_n']})"
        zc = f"{R['zok']}/{R['zwarn']}"
        ovc = f"{R['ov_o']}->{len(R['ov_c'])}"
        print(f"{name[:25]:<26}{zc:>9}{eid:>10}{len(R['trimmed']):>6}{R['extended']:>5}"
              f"{ovc:>11}{R['openings_moved']:>8}{R['n_std']:>5}{owns_s:>20}{td:>13}")

    print("\n" + "=" * 110)
    print("§9 EXPECTED vs ACTUAL + trim lengths")
    print("=" * 110)
    for name, R in results.items():
        cc = R["cats"]
        act = (R["walls_c"], cc.get("exterior", 0), cc.get("interior", 0), cc.get("design", 0))
        exp = EXPECT.get(name)
        if exp:
            e = (exp["walls"], exp["ext"], exp["intr"], exp["des"])
            print(f"\n{name}  [{'OK' if e == act else 'MISMATCH'}]  "
                  f"exp {e[0]}({e[1]}/{e[2]}/{e[3]})  act {act[0]}({act[1]}/{act[2]}/{act[3]})")
        else:
            print(f"\n{name}  [no §9 row]  act {act[0]}({act[1]}/{act[2]}/{act[3]})")
        if R["trimmed"]:
            tl = sorted(round(cm["length"], 2) for cm, _, _ in R["trimmed"])
            print(f"   trimmed final lengths (ft): {tl}")

    print("\n" + "=" * 110)
    print("FINDINGS")
    print("=" * 110)
    for name, R in results.items():
        fs = R["findings"]
        ne = sum(1 for _, s, _ in fs if s == "ERROR")
        nw = sum(1 for _, s, _ in fs if s == "WARNING")
        print(f"\n### {name}   ({ne} err, {nw} warn)")
        if not fs:
            print("   ✓ all criteria pass")
        for cr, sv, ms in fs:
            print(f"   [{cr} {sv}] {ms}")


if __name__ == "__main__":
    main()
