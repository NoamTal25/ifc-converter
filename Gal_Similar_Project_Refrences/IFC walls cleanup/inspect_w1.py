#!/usr/bin/env python3
"""
inspect_w1.py — Independent audit of the -W1 cleanup outputs against
"IFC walls cleanup algorithm.md" (§8 acceptance criteria 1-13).  Read-only.

Walls are matched ORIGINAL <-> W1 by GlobalId.  Two kinds of original walls
intentionally disappear from the W1 wall set and are accounted for, not flagged:
  • glass/shower walls  -> re-typed to IfcFurniture (Step 0)
  • upper stacked segment-> merged into the lower full-height wall (Step 5)

Criteria:
  C1 original untouched      C6 names + element id       C10 shape preservation
  C2 non-wall preservation   C7 vertical +Z              C11 top bound (roof) unchanged
  C3 no same-storey overlaps C7b no IfcWallStandardCase  C12 vertical merge (stacked pair)
  C4 openings fixed          C8 clean corners            C13 glass -> furniture (Step 0)
  C5 wall count              C9 pinwheel ownership
"""
import math
import re
import sys
from pathlib import Path
from collections import defaultdict

import ifcopenshell
import ifcopenshell.util.placement as placement

sys.path.insert(0, str(Path(__file__).parent))
import IFC_walls_cleanup_V1 as cu

IFC_DIR = Path(__file__).parent / "IFCs"
SUFFIX = "-W1"
NAME_RE = re.compile(r"^(Exterior|Interior|Design) wall:\s*(\d+)$")
GLASS_RE = re.compile(r"^Shower glass:\s*(\d+)$")
FURN_TYPES = ("IfcFurniture", "IfcFurnishingElement")

# §9 expected final results (after Step 0 + Step 5)
EXPECT = {
    "SAN JUAN CYPRESS - AUG 2": dict(walls=6, ext=4, intr=0, des=2, glass=[], merges=0),
    "Northam Ave, San Carlos":  dict(walls=6, ext=4, intr=0, des=2, glass=[], merges=0),
    "HUDSON ADU":               dict(walls=8, ext=4, intr=3, des=1, glass=["591587"], merges=0),
    "FOREST ADU":               dict(walls=7, ext=4, intr=3, des=0, glass=["834582"], merges=7),
    "14TH SF - MAR 28 V4":      dict(walls=7, ext=4, intr=2, des=1, glass=["628962", "664259"], merges=0),
    "LEXFORD_OFFICE":           dict(walls=7, ext=4, intr=2, des=1, glass=[], merges=0),
    "Turnberry_927_TURNBERRY_ADU-DEC_2_2025":
                                dict(walls=20, ext=4, intr=15, des=1, glass=["1698782", "1699201"], merges=0),
}

T_THICK = 1e-3; T_HEIGHT = 1e-3; T_LEN = 1e-3
T_OPEN = 1e-4; T_OVERLAP = 0.02; T_TOP = 0.02; T_OWN = 0.10
APPENDAGE_LEN = 3.0


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
        return max((z for _, z in (info.get("top_profile") or [])), default=None)
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


def base_z_of(w):
    try:
        return placement.get_local_placement(w.ObjectPlacement)[2][3]
    except Exception:
        return 0.0


def stacked_pairs(model, glass_gids):
    """Original same-id stacked pairs that Step 5 will merge (mirrors _merge_stacked_walls).
    Returns {wall_id_digits: (lower_gid, upper_gid)}."""
    groups = defaultdict(list)
    for w in walls_of(model):
        if w.GlobalId in glass_gids:
            continue
        eid = cu._eid_digits(w.Name)
        if eid:
            groups[eid].append(w)
    pairs = {}
    for eid, ws in groups.items():
        if len(ws) != 2:
            continue
        a, b = ws
        ia, ib = cu._get_wall_info(a), cu._get_wall_info(b)
        if not ia or not ib:
            continue
        fa, fb = cu._footprint(ia), cu._footprint(ib)
        if any(abs(fa[k] - fb[k]) > 0.2 for k in range(4)):
            continue
        za, zb = base_z_of(a), base_z_of(b)
        if abs(za - zb) < 0.1:
            continue
        lower, upper = (a, b) if za < zb else (b, a)
        pairs[eid] = (lower.GlobalId, upper.GlobalId)
    return pairs


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
    st = storey_map(model)

    def zr(w, info):
        d = wall_height(w, info) or 0.0
        z0 = base_z_of(w)
        return (z0, z0 + d)

    items = [(w, cu._footprint(i), i) for w, i in
             ((w, cu._get_wall_info(w)) for w in walls_of(model)) if i]
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
    ra = run_axis(A)
    fpA, fpB = cu._footprint(A), cu._footprint(B)
    Blo, Bhi = fpB[2 * ra], fpB[2 * ra + 1]
    A0, A1 = fpA[2 * ra], fpA[2 * ra + 1]
    Bc = (Blo + Bhi) / 2.0
    if abs(A1 - Bc) <= abs(A0 - Bc):
        cA, near, far = A1, Blo, Bhi
    else:
        cA, near, far = A0, Bhi, Blo
    if not (Blo - T_OWN <= cA <= Bhi + T_OWN):
        return None
    if abs(cA - far) <= T_OWN:
        return "own"
    if abs(cA - near) <= T_OWN:
        return "butt"
    return None


def pinwheel_ownership(model):
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
            fa, fb = cu._footprint(ia), cu._footprint(ib)
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
    """World ROOF signature (§8.11/§8.12): flat top z, or the world plane equations of every
    UNBOUNDED IfcHalfSpaceSolid roof cut (bounded extent-cuts excluded — they grow with
    extension and need their own Position)."""
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
                    if (hs and hs.is_a("IfcHalfSpaceSolid")
                            and not hs.is_a("IfcPolygonalBoundedHalfSpace")
                            and hs.BaseSurface and hs.BaseSurface.is_a("IfcPlane")
                            and hs.BaseSurface.Position):
                        pos = hs.BaseSurface.Position
                        L = list(pos.Location.Coordinates)
                        while len(L) < 3: L.append(0.0)
                        n = list(pos.Axis.DirectionRatios) if pos.Axis else [0, 0, 1]
                        Pw, Nw = _mat_pt(M, L), _mat_dir(M, n)
                        mag = math.sqrt(sum(c*c for c in Nw)) or 1.0
                        Nw = tuple(c/mag for c in Nw)
                        clips.append((Nw[0], Nw[1], Nw[2], sum(Nw[i]*Pw[i] for i in range(3))))
                elif e.is_a("IfcExtrudedAreaSolid"):
                    base = e
    if clips:
        return ("clipped", sorted(clips, key=lambda p: (round(p[2], 2), round(p[3], 1))))
    if base is not None:
        bz = base.Position.Location.Coordinates[2] if (base.Position and base.Position.Location and
              len(base.Position.Location.Coordinates) > 2) else 0.0
        return ("flat", round(M[2][3] + bz + (base.Depth or 0.0), 3))
    return None


def _top_delta(a, b):
    """max world-z/offset delta between two top signatures, or None if incomparable.
    Clipped planes are de-duplicated first — the vertical merge can graft a roof plane that
    duplicates one the lower already had (idempotent cut), which must not read as a mismatch."""
    if a is None or b is None or a[0] != b[0]:
        return None
    if a[0] == "flat":
        return abs(a[1] - b[1])
    def dedup(planes):
        seen = {}
        for p in planes:
            seen[(round(p[0], 3), round(p[1], 3), round(p[2], 3), round(p[3], 2))] = p
        return list(seen.values())
    pa, pb = dedup(a[1]), dedup(b[1])
    if len(pa) != len(pb):
        return None
    worst = 0.0
    for x, y in zip(sorted(pa, key=lambda p: (round(p[2], 2), round(p[3], 1))),
                    sorted(pb, key=lambda p: (round(p[2], 2), round(p[3], 1)))):
        if x[0]*y[0] + x[1]*y[1] + x[2]*y[2] < 0.999:
            return None
        worst = max(worst, abs(x[3] - y[3]))
    return worst


def entity_by_gid(model):
    out = {}
    for e in model:
        g = getattr(e, "GlobalId", None)
        if g:
            out[g] = e
    return out


def inspect(src, w1):
    mo, mc = ifcopenshell.open(str(src)), ifcopenshell.open(str(w1))
    findings = []
    def F(cr, s, m): findings.append((cr, s, m))

    wo_raw, wc_raw = walls_of(mo), walls_of(mc)
    o, c = by_gid(mo), by_gid(mc)
    oent = entity_by_gid(mo); cent = entity_by_gid(mc)

    glass_gids = {w.GlobalId for w in wo_raw if cu._is_glass_wall(w)}
    pairs = stacked_pairs(mo, glass_gids)            # eid -> (lower_gid, upper_gid)
    upper_gids = {u for _, u in pairs.values()}
    lower_to_upper = {l: u for l, u in pairs.values()}
    expected_gone = glass_gids | upper_gids          # legitimately absent from W1 walls

    # C1 original untouched (proxy)
    orig_named = sum(1 for m in o.values() if NAME_RE.match(m["name"]) or GLASS_RE.match(m["name"]))
    c1_ok = orig_named == 0
    if not c1_ok:
        F("C1", "ERROR", f"{orig_named} original wall(s) already cleaned-named")

    # C5 wall count
    n_glass, n_merge = len(glass_gids), len(upper_gids)
    expected = len(wo_raw) - n_glass - n_merge
    c5_ok = len(wc_raw) == expected
    if not c5_ok:
        F("C5", "ERROR", f"wall count {len(wc_raw)} != orig {len(wo_raw)} - glass {n_glass} - merges {n_merge} = {expected}")
    unexpected_gone = (set(o) - set(c)) - expected_gone
    if unexpected_gone:
        F("C5", "ERROR", f"{len(unexpected_gone)} wall(s) vanished unexpectedly: {[o[g]['name'] for g in list(unexpected_gone)[:3]]}")

    # C7b normalization
    n_std = len(mc.by_type("IfcWallStandardCase"))
    c7b_ok = (n_std == 0)
    if n_std:
        F("C7b", "ERROR", f"{n_std} IfcWallStandardCase remain (should be 0)")
    kept_gids = (set(o) - expected_gone)
    lost_kept = kept_gids - set(c)
    if lost_kept:
        c7b_ok = False
        F("C7b", "ERROR", f"{len(lost_kept)} kept GlobalId(s) missing from W1")

    # C6 names + eid + classification counts
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
        F("C3", "ERROR", f"overlap {pen:.2f}\" {wa.Name} x {wb.Name}")
    trimmed = []; grew = []; miter_left = []
    merged_lowers = set(lower_to_upper)
    for gid in set(o) & set(c):
        om, cm = o[gid], c[gid]
        if gid in merged_lowers or om["length"] is None or cm["length"] is None:
            continue
        dl = om["length"] - cm["length"]
        if dl > T_LEN:
            trimmed.append((cm, om, dl))
            if cm["pkind"] == "arbitrary":
                miter_left.append(cm["name"])
        elif dl < -T_LEN:
            grew.append((cm, om, -dl))
    for nm in miter_left:
        F("C8", "ERROR", f"{nm}: divided wall still mitered")
    c8_ok = (len(ov_c) == 0) and not miter_left

    # C9 pinwheel ownership
    ext_gids, owned = pinwheel_ownership(mc)
    own_counts = {g: owned.get(g, 0) for g in ext_gids}
    double = [g for g, n in own_counts.items() if n >= 2]
    zero = [g for g in ext_gids if own_counts[g] == 0]
    c9_ok = not double
    if double:
        F("C9", "ERROR", f"{len(double)} ext wall(s) own >=2 corners: {[c[g]['name'] for g in double if g in c]}")
    if zero:
        F("C9", "INFO", f"{len(zero)} ext wall(s) own 0 corners: {[c[g]['name'] for g in zero if g in c]}")

    # C2 preservation (IfcFurnishingElement rises by n_glass)
    pres = []
    for t in cu.PRESERVE_TYPES:
        nb, na = len(mo.by_type(t)), len(mc.by_type(t))
        exp = nb + n_glass if t == "IfcFurnishingElement" else nb
        if (nb or na) and na != exp:
            pres.append(f"{t} {nb}->{na} (exp {exp})")
    c2_ok = not pres
    for x in pres: F("C2", "ERROR", f"non-wall changed: {x}")

    # C4 openings
    po, pc = opening_world(mo), opening_world(mc)
    moved = [g for g, p in po.items() if g in pc and math.dist(p, pc[g]) > T_OPEN]
    c4_ok = not moved
    if moved: F("C4", "ERROR", f"{len(moved)} opening(s) moved > 1e-4 ft")

    # C10 shape preservation (corner-aware; merged lowers excluded as legitimately changed)
    cp = corner_participants(mo)
    o_sid = {m["sid"]: gid for gid, m in o.items()}
    trimmed_gids = {cm["gid"] for cm, _, _ in trimmed} | {cm["gid"] for cm, _, _ in grew} | merged_lowers
    cp_gids = {gid for sid, gid in o_sid.items() if sid in cp}
    thick_chg = []; height_chg = []; clip_lost = []; undiv = []; squared = []
    for gid in set(o) & set(c):
        om, cm = o[gid], c[gid]
        if gid in merged_lowers:
            continue
        if om["thickness"] and cm["thickness"] and abs(om["thickness"] - cm["thickness"]) > T_THICK:
            thick_chg.append(cm["name"])
        if om["height"] and cm["height"] and abs(om["height"] - cm["height"]) > T_HEIGHT:
            height_chg.append(cm["name"])
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
                undiv.append(f"{cm['name']} {sig_o}->{sig_c}")
    for x in thick_chg:  F("C10", "ERROR", f"thickness changed: {x}")
    for x in height_chg: F("C10", "ERROR", f"height changed: {x}")
    for x in clip_lost:  F("C10", "ERROR", f"clip dropped: {x}")
    for x in undiv:      F("C10", "ERROR", f"undivided wall changed: {x}")
    for x in squared:    F("C10", "INFO", f"corner wall squared (OK §3.3): {x}")
    c10_ok = not (thick_chg or height_chg or clip_lost or undiv)

    # C11 top bound — non-merged: own roof; merged lower: vs original UPPER roof
    top_bad = []; top_n = 0; top_max = 0.0
    for gid in set(o) & set(c):
        cm_ent = cent.get(gid)
        if cm_ent is None:
            continue
        ref_ent = oent.get(lower_to_upper[gid]) if gid in merged_lowers else oent.get(gid)
        d = _top_delta(top_signature(ref_ent), top_signature(cm_ent))
        if d is None:
            continue
        top_n += 1; top_max = max(top_max, d)
        if d > T_TOP:
            top_bad.append((c[gid]["name"], d))
    c11_ok = None if top_n == 0 else (not top_bad)
    top_max_delta = top_max if top_n else None
    for nm, d in top_bad:
        F("C11", "ERROR", f"top bound moved {d:.4f} ft: {nm}")

    # C12 vertical merge
    c12_ok = True; merge_roof_max = 0.0
    for eid, (lg, ug) in pairs.items():
        if ug in c:
            c12_ok = False; F("C12", "ERROR", f"id {eid}: upper segment still present as a wall")
        if lg not in c:
            c12_ok = False; F("C12", "ERROR", f"id {eid}: merged lower wall missing"); continue
        oh = (o[lg]["height"] or 0) + (o.get(ug, {}).get("height") or 0)
        ch = c[lg]["height"] or 0
        if oh and abs(ch - oh) > 0.05:
            c12_ok = False
            F("C12", "ERROR", f"id {eid}: merged height {ch:.3f} != lower+upper {oh:.3f} ft")
        d = _top_delta(top_signature(oent.get(ug)), top_signature(cent.get(lg)))
        if d is not None:
            merge_roof_max = max(merge_roof_max, d)
            if d > 0.01:
                c12_ok = False
                F("C12", "ERROR", f"id {eid}: grafted roof off {d:.4f} ft")
    c12_ok = c12_ok if pairs else None

    # C13 glass -> furniture
    c13_ok = True if glass_gids else None
    conv_eids = set()
    for gid in glass_gids:
        e = cent.get(gid)
        eid = cu._eid_digits(oent[gid].Name) if gid in oent else "?"
        if e is None:
            c13_ok = False; F("C13", "ERROR", f"glass {eid}: gone from W1"); continue
        if e.is_a("IfcWall") or e.is_a("IfcWallStandardCase"):
            c13_ok = False; F("C13", "ERROR", f"glass {eid}: still a wall"); continue
        if not any(e.is_a(t) for t in FURN_TYPES):
            c13_ok = False; F("C13", "ERROR", f"glass {eid}: type {e.is_a()} not furniture"); continue
        if not GLASS_RE.match(e.Name or ""):
            c13_ok = False; F("C13", "ERROR", f"glass {eid}: name {e.Name!r} != 'Shower glass: <id>'")
        if not e.ObjectPlacement or not e.Representation:
            c13_ok = False; F("C13", "ERROR", f"glass {eid}: lost placement/Body")
        conv_eids.add(eid)

    return dict(
        walls_o=len(wo_raw), walls_c=len(wc_raw), expected=expected, cats=dict(cats),
        trimmed=trimmed, extended=len(grew), ov_o=len(ov_o), ov_c=ov_c,
        zok=zok, zwarn=zwarn, openings_moved=len(moved), n_std=n_std,
        n_glass=n_glass, n_merge=n_merge, conv_eids=conv_eids,
        own_counts=own_counts, top_max_delta=top_max_delta, merge_roof_max=merge_roof_max,
        crit=dict(C1=c1_ok, C2=c2_ok, C3=c3_ok, C4=c4_ok, C5=c5_ok, C6=c6_ok, C7=c7_ok,
                  C7b=c7b_ok, C8=c8_ok, C9=c9_ok, C10=c10_ok, C11=c11_ok, C12=c12_ok, C13=c13_ok),
        findings=findings, c=c)


def mark(v, warn=False):
    if v is None: return "-"
    if v is True: return "✓"
    return "!" if warn else "✗"


def main():
    pairs = []
    for src in sorted(IFC_DIR.glob("*.ifc")):
        if src.stem.endswith(SUFFIX):
            continue
        w1 = src.with_name(f"{src.stem}{SUFFIX}{src.suffix}")
        if w1.exists():
            pairs.append((src, w1))
    results = {s.stem: inspect(s, w1) for s, w1 in pairs}

    crit_keys = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C7b", "C8", "C9", "C10", "C11", "C12", "C13"]
    print("\n" + "=" * 118)
    print("§8 ACCEPTANCE CRITERIA   ✓=pass ✗=fail !=warn -=n/a")
    print("=" * 118)
    hdr = f"{'Fixture':<26}" + "".join(f"{k:>5}" for k in crit_keys)
    print(hdr); print("-" * len(hdr))
    for name, R in results.items():
        cells = "".join(f"{mark(R['crit'][k], warn=(k=='C7')):>5}" for k in crit_keys)
        print(f"{name[:25]:<26}{cells}")

    print("\n" + "=" * 118)
    print("STEP METRICS")
    print("=" * 118)
    hdr = (f"{'Fixture':<26}{'+Z':>6}{'E/I/D':>10}{'trim':>5}{'ext':>4}{'glass':>6}{'merge':>6}"
           f"{'ovlp':>6}{'opn.mv':>7}{'std':>4}{'own(ext)':>18}{'topΔ':>8}")
    print(hdr); print("-" * len(hdr))
    for name, R in results.items():
        cc = R["cats"]
        eid = f"{cc.get('exterior',0)}/{cc.get('interior',0)}/{cc.get('design',0)}"
        owns = ",".join(map(str, sorted(R["own_counts"].values(), reverse=True))) or "-"
        td = "n/a" if R["top_max_delta"] is None else f"{R['top_max_delta']:.4f}"
        print(f"{name[:25]:<26}{R['zok']:>6}{eid:>10}{len(R['trimmed']):>5}{R['extended']:>4}"
              f"{R['n_glass']:>6}{R['n_merge']:>6}{len(R['ov_c']):>6}{R['openings_moved']:>7}"
              f"{R['n_std']:>4}{owns:>18}{td:>8}")

    print("\n" + "=" * 118)
    print("§9 EXPECTED vs ACTUAL")
    print("=" * 118)
    for name, R in results.items():
        cc = R["cats"]
        act = (R["walls_c"], cc.get("exterior", 0), cc.get("interior", 0), cc.get("design", 0))
        exp = EXPECT.get(name)
        if exp:
            e = (exp["walls"], exp["ext"], exp["intr"], exp["des"])
            okw = "OK" if e == act else "MISMATCH"
            gset = "OK" if set(exp["glass"]) == R["conv_eids"] else f"MISMATCH exp{exp['glass']} got{sorted(R['conv_eids'])}"
            mok = "OK" if exp["merges"] == R["n_merge"] else f"exp{exp['merges']} got{R['n_merge']}"
            print(f"\n{name}  walls[{okw}] exp {e[0]}({e[1]}/{e[2]}/{e[3]}) act {act[0]}({act[1]}/{act[2]}/{act[3]})"
                  f"  glass[{gset}]  merges[{mok}]")
        else:
            print(f"\n{name}  [no §9 row] act {act}")
        if R["trimmed"]:
            print(f"   trim lengths: {sorted(round(cm['length'],2) for cm,_,_ in R['trimmed'])}")

    print("\n" + "=" * 118)
    print("FINDINGS")
    print("=" * 118)
    for name, R in results.items():
        fs = R["findings"]
        ne = sum(1 for _, s, _ in fs if s == "ERROR"); nw = sum(1 for _, s, _ in fs if s == "WARNING")
        print(f"\n### {name}   ({ne} err, {nw} warn)")
        if not fs:
            print("   ✓ all criteria pass")
        for cr, sv, ms in fs:
            print(f"   [{cr} {sv}] {ms}")


if __name__ == "__main__":
    main()
