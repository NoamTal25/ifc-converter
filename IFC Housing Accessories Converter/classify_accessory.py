"""
classify_accessory.py — map a baked accessory object to a FormX accessory type + the derived
FormX_Accessory fields. Pure and geometry-free.

Strategy (mirrors the door converter's "class drives, name refines"): the IFC **class** is the
strong prior; the **Name** only refines WITHIN a class. This is what defuses the headline trap —
``Vanity Counter Top w Square Sink Hole`` is an ``IfcFurnishingElement`` whose Name contains "sink",
but class-prior keeps it in the furniture branch (→ TABLE via "counter top"), never SANITARY_FIXTURE.

The converter applies the *gates* (structural-trim keywords, bodiless/2D-only, unreadable)
separately, in the main loop — this module is a pure mapping and never gates.

Returns: ``{accessory_type, catalog_reference, location, source_class}``.
"""
import accessory_types as at


def _norm(s):
    return (s or "").lower().replace("-", " ").replace("_", " ")


def _name_blob(el, etype):
    parts = [getattr(el, "Name", None), getattr(el, "ObjectType", None),
             getattr(etype, "Name", None) if etype else None]
    return _norm(" ".join(p for p in parts if p))


def _first_match(blob, rules):
    """First (type, keywords) rule whose any keyword is a substring of blob, else None."""
    for typ, kws in rules:
        if any(k in blob for k in kws):
            return typ
    return None


def _catalog_reference(el):
    """The Revit family/type Name with the trailing ':<elementid>' stripped (the swap key FormX uses
    to find a catalog match). Revit names look like ``Family:Type:elementid`` with a NUMERIC id, so we
    only strip a trailing all-digits segment — never a meaningful ':'-bearing type token. Falls back
    to the raw Name, then ''."""
    nm = (getattr(el, "Name", None) or "").strip()
    if not nm:
        return ""
    if ":" in nm:
        head, tail = nm.rsplit(":", 1)
        if tail.strip().isdigit() and head.strip():
            return head.strip()
    return nm


def _location(blob, container_name):
    """'Outdoor' if the containing storey is GRADE/Site or the name says outdoor/exterior, else
    'Indoor'."""
    cn = (container_name or "").lower()
    if "grade" in cn or "site" in cn or "outdoor" in blob or "exterior" in blob:
        return "Outdoor"
    return "Indoor"


def classify(el, etype=None, container_name=None):
    ifc = el.is_a()
    blob = _name_blob(el, etype)

    # ── CLASS-PRIOR (strong) ─────────────────────────────────────────────────────────
    if ifc == "IfcLightFixture":
        t = "LIGHTING"
    elif ifc == "IfcSanitaryTerminal":
        t = "SANITARY_FIXTURE"
    elif ifc == "IfcFlowTerminal":
        # a bare flow terminal is a plumbing fixture unless the name says light or appliance
        if any(k in blob for k in at.LIGHTING_KW):
            t = "LIGHTING"
        elif any(k in blob for k in at.APPLIANCE_KW):
            t = "APPLIANCE"
        else:
            t = "SANITARY_FIXTURE"
    elif ifc in at.FURNITURE_CLASSES:
        t = _first_match(blob, at.FURNITURE_RULES) or "GENERIC"
    elif ifc == "IfcBuildingElementProxy":
        t = _first_match(blob, at.PROXY_RULES) or "GENERIC"
    else:
        t = "GENERIC"

    return dict(
        accessory_type=t,
        catalog_reference=_catalog_reference(el),
        location=_location(blob, container_name),
        source_class=ifc,
    )
