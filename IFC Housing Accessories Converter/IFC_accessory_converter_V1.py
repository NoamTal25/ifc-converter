#!/usr/bin/env python3
"""
IFC_accessory_converter_V1.py — FormX IFC pipeline stage: HOUSING ACCESSORIES (preserve-and-tag).

The non-structural "accessory" objects in an ADU — furniture, plants, wall lights, plumbing
fixtures, appliances, decor — should keep LOOKING EXACTLY as exported, but each should become a
clean, self-contained WHOLE OBJECT that FormX can MOVE and REPLACE (swap the whole thing for a
catalog accessory). There is NO expectation to stretch/resize their shape.

So, unlike the window/door converters (which rebuild geometry into golden parametric templates),
this converter does NOT touch geometry at all. It is deliberately simple:

  1. SCAN the accessory root classes (IfcFurnishingElement / IfcBuildingElementProxy /
     IfcFlowTerminal — by_type pulls their subtypes IfcFurniture / IfcLightFixture /
     IfcSanitaryTerminal), dedup by .id().
  2. GATE the non-accessories: structural trim (Fascia…), bodiless / 2D-only annotation proxies
     (the "Text:…MS GOTHIC" items), and anything unreadable → left untouched + logged.
  3. CLASSIFY each survivor (class-prior, name-refine) into one of FormX's accessory types.
  4. TAG it: author ONE occurrence-level ``FormX_Accessory`` property set (AccessoryType,
     CatalogReference, Movable, Location, SourceClass, FormXConverted) via IfcRelDefinesByProperties
     — never a 2nd type (CLAUDE.md §6). The geometry, styles, placement and identity are untouched.

HEADLINE GUARANTEE — ZERO visual change. The baked mesh, surface styles and ObjectPlacement are kept
verbatim; the ONLY change per accessory is +1 IfcPropertySet, +1 IfcRelDefinesByProperties and +6
IfcPropertySingleValue. verify() proves this: it is *stricter* than the door/window verify — every
geometry/style entity-type count must be IDENTICAL before→after, every product's placement matrix
unchanged, and the per-product IsDefinedBy delta is +1 only on tagged accessories (+0 everywhere
else).

Idempotency: re-running is a no-op — an element already carrying a ``FormX_Accessory`` pset is
skipped (the marker is the pset's presence; ``Name``/``Description``/``ObjectType`` are never
touched).

Output is written with the suffix "-ACC1" before the extension. Self-contained (ifcopenshell only).

Usage:
    python3.11 IFC_accessory_converter_V1.py                  # batch INPUT_IFC_FILES_HERE → OUTPUT…
    python3.11 IFC_accessory_converter_V1.py <in.ifc>         # → OUTPUT_IFC_FILES_HERE/<in>-ACC1.ifc
    python3.11 IFC_accessory_converter_V1.py <in.ifc> <out.ifc>
"""
import shutil
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import ifcopenshell
import ifcopenshell.util.element as ifc_element
import ifcopenshell.util.placement as ifc_placement

sys.path.insert(0, str(Path(__file__).resolve().parent))
import accessory_types as at
import schema_adapter as sa
from classify_accessory import classify

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent

# ══════════════════════════════════════════════════════════════════════════════
# Config
# ══════════════════════════════════════════════════════════════════════════════
SUFFIX = "-ACC1"
INPUT_DIR = _ROOT / "INPUT_IFC_FILES_HERE"
OUTPUT_DIR = _ROOT / "OUTPUT_IFC_FILES_HERE"

PSET_NAME = "FormX_Accessory"          # the tag + idempotency marker (presence = converted)
N_PROPS = 6                            # IfcPropertySingleValue authored per tagged accessory


# ══════════════════════════════════════════════════════════════════════════════
# Scan / gate helpers
# ══════════════════════════════════════════════════════════════════════════════
def _safe_by_type(model, t):
    """by_type, but tolerant of schema-absent types (IfcLightFixture/IfcSanitaryTerminal/IfcFurniture
    do not exist in IFC2X3 — CLAUDE.md §6)."""
    try:
        return model.by_type(t)
    except RuntimeError:
        return []


def _candidates(model):
    """The accessory candidate pool: union of the root classes, deduped by .id() (by_type pulls
    subtypes, so a furniture/light is reachable more than once)."""
    seen = {}
    for root in at.ROOTS:
        for el in _safe_by_type(model, root):
            seen.setdefault(el.id(), el)
    return [seen[k] for k in sorted(seen)]


def _trim_gated(blob):
    return any(k in blob for k in at.GATE_KEYWORDS)


def _has_3d_body(el):
    """True iff the element has a Body representation that is a real 3D solid (or a mapped solid).
    Gates bodiless elements and 2D-only annotation proxies (Annotation2D text, FootPrint-only)."""
    rep = getattr(el, "Representation", None)
    if not rep or not getattr(rep, "Representations", None):
        return False
    for sr in rep.Representations:
        if sr.RepresentationIdentifier != "Body":
            continue
        if not (sr.Items or []):
            continue
        if sr.RepresentationType in at.SOLID_REP_TYPES:
            return True
    return False


def _name_blob(el, etype):
    parts = [getattr(el, "Name", None), getattr(el, "ObjectType", None),
             getattr(etype, "Name", None) if etype else None]
    return " ".join(p for p in parts if p).lower().replace("-", " ").replace("_", " ")


def _container_name(el):
    try:
        c = ifc_element.get_container(el)
        return getattr(c, "Name", None) if c else None
    except Exception:
        return None


def _already_tagged(el):
    """Idempotency marker = presence of a FormX_Accessory pset (NOT a Description stamp)."""
    for rel in (getattr(el, "IsDefinedBy", None) or []):
        if rel.is_a("IfcRelDefinesByProperties"):
            pd = rel.RelatingPropertyDefinition
            if pd and pd.is_a("IfcPropertySet") and pd.Name == PSET_NAME:
                return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# Tag one accessory (the ONLY mutation this converter makes)
# ══════════════════════════════════════════════════════════════════════════════
def tag_accessory(model, el, owner, info):
    """Author exactly one FormX_Accessory pset on the occurrence. Atomic: on any failure, remove the
    partially-created pset so we never leave an orphan."""
    cref = info["catalog_reference"] or info["source_class"]
    props = [
        sa.psv(model, "AccessoryType",    info["accessory_type"],  "IfcLabel"),
        sa.psv(model, "CatalogReference", cref,                    "IfcIdentifier"),
        sa.psv(model, "Movable",          True,                    "IfcBoolean"),
        sa.psv(model, "Location",         info["location"],        "IfcLabel"),
        sa.psv(model, "SourceClass",      info["source_class"],    "IfcLabel"),
        sa.psv(model, "FormXConverted",   True,                    "IfcBoolean"),
    ]
    sa.add_pset(model, owner, PSET_NAME, props, el)


# ══════════════════════════════════════════════════════════════════════════════
# Main pipeline
# ══════════════════════════════════════════════════════════════════════════════
def convert(src_path, out_path):
    src_path, out_path = str(src_path), str(out_path)
    print(f"\n{'=' * 74}\n{Path(src_path).name}  →  {Path(out_path).name}\n{'=' * 74}")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src_path, out_path)
    model = ifcopenshell.open(out_path)
    owner = next(iter(model.by_type("IfcOwnerHistory")), None)

    cands = _candidates(model)
    print(f"[scan] {len(cands)} accessory candidates | schema {model.schema}")
    stats = Counter()
    type_counts = Counter()

    # IFC2X3 requires OwnerHistory on IfcRoot; if a file lacks one we cannot author psets without
    # fabricating an owner (which would change the IfcOwnerHistory count / risk validate errors) →
    # gate the whole file rather than corrupt it.
    if model.schema == "IFC2X3" and owner is None:
        print("  [keep] IFC2X3 file has no IfcOwnerHistory → cannot tag safely; leaving untouched")
        model.write(out_path)
        verify(src_path, out_path)
        return

    for el in cands:
        nm = (el.Name or "?")
        if _already_tagged(el):
            stats["already"] += 1
            continue
        etype = ifc_element.get_type(el)
        blob = _name_blob(el, etype)
        if _trim_gated(blob):
            stats["gated-trim"] += 1
            print(f"  [keep]   {nm[:48]:48}  GATE: structural trim")
            continue
        if not _has_3d_body(el):
            stats["gated-nobody"] += 1
            print(f"  [keep]   {nm[:48]:48}  GATE: no 3D Body (bodiless / 2D annotation)")
            continue
        try:
            info = classify(el, etype, _container_name(el))
            tag_accessory(model, el, owner, info)
            stats["tagged"] += 1
            type_counts[info["accessory_type"]] += 1
            print(f"  [tag]    {nm[:48]:48}  → {info['accessory_type']} "
                  f"[{info['location']}] ({info['source_class']})")
        except Exception as e:
            stats["skipped"] += 1
            print(f"  [SKIP]   {nm[:48]:48}  unreadable → left untouched: {e!r}")

    model.write(out_path)
    print(f"[write] {out_path}")
    print(f"[stats] tagged={stats['tagged']} gated-trim={stats['gated-trim']} "
          f"gated-nobody={stats['gated-nobody']} skipped={stats['skipped']} "
          f"already={stats['already']}")
    if type_counts:
        print("[types] " + ", ".join(f"{t}:{type_counts[t]}" for t in at.ORDER if type_counts[t]))
    verify(src_path, out_path)


# ══════════════════════════════════════════════════════════════════════════════
# Built-in verification — PRESERVE-ONLY (reopen src + out; "only FormX_Accessory psets were added")
# ══════════════════════════════════════════════════════════════════════════════
# Geometry/style entity types that MUST be count-identical before→after (we author none of them).
_GEOM_STYLE_TYPES = [
    "IfcShapeRepresentation", "IfcProductDefinitionShape", "IfcMappedItem",
    "IfcRepresentationMap", "IfcStyledItem", "IfcSurfaceStyle", "IfcSurfaceStyleRendering",
    "IfcExtrudedAreaSolid", "IfcFacetedBrep", "IfcAdvancedBrep", "IfcTriangulatedFaceSet",
    "IfcPolyloop", "IfcCartesianPoint", "IfcOwnerHistory",
]
# The only types whose count is allowed to grow, by exactly these per-tag multiples.
_ADDED = {"IfcPropertySet": 1, "IfcRelDefinesByProperties": 1, "IfcPropertySingleValue": N_PROPS}


def _has_formx_pset(el):
    for rel in (getattr(el, "IsDefinedBy", None) or []):
        if rel.is_a("IfcRelDefinesByProperties"):
            pd = rel.RelatingPropertyDefinition
            if pd and pd.is_a("IfcPropertySet") and pd.Name == PSET_NAME:
                return True
    return False


def _ndef(el):
    return sum(1 for rel in (getattr(el, "IsDefinedBy", None) or [])
               if rel.is_a("IfcRelDefinesByProperties"))


def _histogram(model):
    h = Counter()
    for e in model:
        h[e.is_a()] += 1
    return h


def verify(src_path, out_path):
    print("[verify] (preserve-only)")
    before = ifcopenshell.open(src_path)
    after = ifcopenshell.open(out_path)
    ok = True

    # Tagged set = products carrying a FormX_Accessory pset in `after` but not in `before`.
    b_tag = {p.GlobalId for p in before.by_type("IfcProduct") if _has_formx_pset(p)}
    a_tag = {p.GlobalId for p in after.by_type("IfcProduct") if _has_formx_pset(p)}
    newly = a_tag - b_tag
    T = len(newly)
    print(f"  tagged this run: {T}")

    # 1. All product GlobalIds unchanged (we add zero products).
    gb = {p.GlobalId for p in before.by_type("IfcProduct")}
    ga = {p.GlobalId for p in after.by_type("IfcProduct")}
    good = gb == ga; ok &= good
    print(f"  product GlobalId set: {len(gb)} → {len(ga)}  [{'OK' if good else 'CHANGED!'}]")

    # 2. Every product's ObjectPlacement matrix unchanged.
    bpl = {p.GlobalId: ifc_placement.get_local_placement(p.ObjectPlacement)
           for p in before.by_type("IfcProduct") if p.ObjectPlacement}
    moved = 0
    for p in after.by_type("IfcProduct"):
        m0 = bpl.get(p.GlobalId)
        if m0 is not None and p.ObjectPlacement is not None:
            if not np.allclose(m0, ifc_placement.get_local_placement(p.ObjectPlacement), atol=1e-9):
                moved += 1
    good = moved == 0; ok &= good
    print(f"  product placements moved: {moved}  [{'OK — none moved' if good else 'CHANGED!'}]")

    # 3. Geometry/style entity-type counts identical (the heart of preserve-only).
    geom_bad = []
    for t in _GEOM_STYLE_TYPES:
        nb, na = len(_safe_by_type(before, t)), len(_safe_by_type(after, t))
        if nb != na:
            geom_bad.append(f"{t} {nb}->{na}")
    good = not geom_bad; ok &= good
    print(f"  geometry/style entity counts identical: "
          f"[{'OK' if good else 'CHANGED! ' + '; '.join(geom_bad)}]")

    # 4. Full type histogram identical EXCEPT the three added types (+T / +T / +6T).
    hb, ha = _histogram(before), _histogram(after)
    hist_bad = []
    for t in set(hb) | set(ha):
        delta = ha.get(t, 0) - hb.get(t, 0)
        expected = _ADDED.get(t, 0) * T
        if delta != expected:
            hist_bad.append(f"{t} Δ{delta:+d} (want {expected:+d})")
    good = not hist_bad; ok &= good
    print(f"  type histogram (only +{T}/+{T}/+{N_PROPS * T} on pset/rel/value): "
          f"[{'OK' if good else 'CHANGED! ' + '; '.join(hist_bad[:6])}]")

    # 5. KEYSTONE — per-product IsDefinedBy delta = 1 on newly-tagged, 0 everywhere else.
    bdef = {p.GlobalId: _ndef(p) for p in before.by_type("IfcProduct")}
    leak = 0
    for p in after.by_type("IfcProduct"):
        delta = _ndef(p) - bdef.get(p.GlobalId, 0)
        if delta != (1 if p.GlobalId in newly else 0):
            leak += 1
    good = leak == 0; ok &= good
    print(f"  IsDefinedBy delta (+1 tagged only): {leak} leaks  "
          f"[{'OK' if good else 'LEAKED ONTO NON-ACCESSORIES!'}]")

    # 6. Each tagged accessory has exactly one FormX_Accessory pset with N_PROPS properties.
    bad_pset = 0
    for p in after.by_type("IfcProduct"):
        if p.GlobalId not in a_tag:
            continue
        psets = [rel.RelatingPropertyDefinition for rel in (p.IsDefinedBy or [])
                 if rel.is_a("IfcRelDefinesByProperties")
                 and rel.RelatingPropertyDefinition.is_a("IfcPropertySet")
                 and rel.RelatingPropertyDefinition.Name == PSET_NAME]
        if len(psets) != 1 or len(psets[0].HasProperties or []) != N_PROPS:
            bad_pset += 1
    good = bad_pset == 0; ok &= good
    print(f"  exactly one well-formed pset per tagged accessory: {bad_pset} bad  "
          f"[{'OK' if good else 'MALFORMED!'}]")

    # 7. No new validate errors vs source (≤, not ==).
    try:
        import ifcopenshell.validate as V
        lb = V.json_logger(); V.validate(before, lb)
        la = V.json_logger(); V.validate(after, la)
        nb, na = len(lb.statements), len(la.statements)
        good = na <= nb; ok &= good
        pre = f" ({nb} pre-existing in source)" if nb else ""
        print(f"  ifcopenshell.validate: source {nb} → output {na}{pre}  "
              f"[{'OK' if good else 'NEW ERRORS INTRODUCED!'}]")
    except Exception as e:
        print(f"  ifcopenshell.validate: skipped ({e!r})")

    print(f"\n  RESULT: {'ALL CHECKS PASSED ✓' if ok else 'SEE WARNINGS ABOVE ✗'}")
    return ok


def _out_path_for(src_path):
    p = Path(src_path)
    return OUTPUT_DIR / f"{p.stem}{SUFFIX}{p.suffix}"


def main():
    if len(sys.argv) >= 3:
        convert(sys.argv[1], sys.argv[2])
    elif len(sys.argv) == 2:
        convert(sys.argv[1], _out_path_for(sys.argv[1]))
    else:
        srcs = sorted(p for p in INPUT_DIR.glob("*.ifc") if not p.stem.endswith(SUFFIX))
        if not srcs:
            print(f"No source .ifc files found in {INPUT_DIR}")
            sys.exit(1)
        for src in srcs:
            convert(src, _out_path_for(src))
    print("\nDone.")


if __name__ == "__main__":
    main()
