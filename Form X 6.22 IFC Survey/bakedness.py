"""
Formal "bakedness" metric for IFC window representations.

Motivation
----------
"Baked" = the window has been reduced to frozen output geometry, losing the
parametric recipe that would let a tool answer "make this wider" or "make these
fire-rated". We quantify how much parametric intent is *recoverable* from a file
by inspecting concrete, verifiable IFC features (no guessing about provenance).

Parametric Integrity Score (PIS), 0-100  -- higher = less baked
--------------------------------------------------------------
  Geometry representation .......... max 35
      any parametric swept solid (Extruded/Revolved/SweptArea w/ profile) = 35
      CSG / boolean result only ............................................ 20
      explicit only (Brep / faceted / tessellated face sets) ............... 0
  Semantic typing (IfcWindowType/Style present) ............. max 15
  Window detailing property sets ........................... max 25
      IfcWindowLiningProperties present ................................... 12
      IfcWindowPanelProperties present .................................... 13
  Classification enums populated ........................... max 15
      PredefinedType set & != NOTDEFINED ................................... 5
      PartitioningType set & != NOTDEFINED ................................. 5
      panel OperationType set & != NOTDEFINED .............................. 5
  Property sets attached (e.g. Pset_WindowCommon) .......... max  5
  Dimensional attributes (OverallWidth & OverallHeight) .... max  5
                                                            ---------
                                                             total 100

Bakedness class (derived from PIS)
----------------------------------
  PIS >= 78 : PARAMETRIC            -- editable; constructs + parametric geometry
  50 - 77   : SEMI_PARAMETRIC       -- partial; some constructs, some baking
  20 - 49   : BAKED_WITH_METADATA   -- frozen geometry, but values survive in Psets
  < 20      : FULLY_BAKED           -- geometry only; parametric intent ~ gone

The score is computed per distinct *window type* (a "style"); where windows have
no type, they are grouped by name. We report per-style and per-file aggregates.
"""
import ifcopenshell

PARAM_SOLIDS = {
    "IfcExtrudedAreaSolid", "IfcRevolvedAreaSolid", "IfcSweptAreaSolid",
    "IfcSurfaceCurveSweptAreaSolid", "IfcFixedReferenceSweptAreaSolid",
    "IfcSweptDiskSolid",
}
CSG_SOLIDS = {
    "IfcBooleanResult", "IfcBooleanClippingResult", "IfcCsgSolid",
    "IfcCsgPrimitive3D", "IfcBlock", "IfcRectangularPyramid", "IfcSphere",
}
EXPLICIT = {
    "IfcFacetedBrep", "IfcAdvancedBrep", "IfcManifoldSolidBrep",
    "IfcTriangulatedFaceSet", "IfcPolygonalFaceSet", "IfcShellBasedSurfaceModel",
    "IfcFaceBasedSurfaceModel", "IfcClosedShell", "IfcOpenShell",
}


def _collect_geometry_types(rep, seen=None, depth=0):
    """Recursively collect geometry item class names under a representation,
    resolving mapped items to their source representation."""
    types = set()
    if rep is None or depth > 6:
        return types
    seen = seen or set()
    if rep.id() in seen:
        return types
    seen.add(rep.id())
    items = getattr(rep, "Items", None) or []
    for it in items:
        cls = it.is_a()
        if cls == "IfcMappedItem":
            src = it.MappingSource
            if src and src.MappedRepresentation:
                types |= _collect_geometry_types(src.MappedRepresentation, seen, depth + 1)
        else:
            types.add(cls)
            # one level into boolean operands to detect underlying solids
            for attr in ("FirstOperand", "SecondOperand"):
                op = getattr(it, attr, None)
                if op is not None and hasattr(op, "is_a"):
                    types.add(op.is_a())
    return types


def _window_geometry_types(win):
    types = set()
    rep = getattr(win, "Representation", None)
    if rep and getattr(rep, "Representations", None):
        for shape in rep.Representations:
            types |= _collect_geometry_types(shape)
    return types


def _geom_score(types):
    if types & PARAM_SOLIDS:
        return 35, "parametric_swept"
    if types & CSG_SOLIDS:
        return 20, "csg_boolean"
    if types & EXPLICIT:
        return 0, "explicit_baked"
    return 0, "none_or_unknown"


def _enum_ok(val):
    return val is not None and str(val).upper() not in ("NOTDEFINED", "USERDEFINED", "$", "")


def _type_of(win):
    for rel in getattr(win, "IsTypedBy", None) or getattr(win, "IsDefinedBy", None) or []:
        if rel.is_a("IfcRelDefinesByType"):
            return rel.RelatingType
    # IFC2x3 path
    for rel in getattr(win, "IsDefinedBy", None) or []:
        if rel.is_a("IfcRelDefinesByType"):
            return rel.RelatingType
    return None


def _psets_of(win):
    names = []
    for rel in getattr(win, "IsDefinedBy", None) or []:
        if rel.is_a("IfcRelDefinesByProperties"):
            pdef = rel.RelatingPropertyDefinition
            if pdef and pdef.is_a("IfcPropertySet"):
                names.append(pdef.Name)
    return names


def score_window(win, wtype):
    geom_types = _window_geometry_types(win)
    if wtype is not None:
        # window may carry no geometry of its own; pull from the type's maps
        for rm in getattr(wtype, "RepresentationMaps", None) or []:
            if rm.MappedRepresentation:
                geom_types |= _collect_geometry_types(rm.MappedRepresentation)

    g_pts, g_label = _geom_score(geom_types)

    has_type = wtype is not None
    type_pts = 15 if has_type else 0

    lining = panel = False
    panel_op = None
    pset_defs = []
    if wtype is not None:
        pset_defs = getattr(wtype, "HasPropertySets", None) or []
    for pd in pset_defs:
        if pd.is_a("IfcWindowLiningProperties"):
            lining = True
        if pd.is_a("IfcWindowPanelProperties"):
            panel = True
            panel_op = getattr(pd, "OperationType", None)
    detail_pts = (12 if lining else 0) + (13 if panel else 0)

    predef = getattr(win, "PredefinedType", None) or getattr(wtype, "PredefinedType", None)
    partition = getattr(wtype, "PartitioningType", None) if wtype else None
    # IFC2x3 fallbacks
    if partition is None and wtype is not None:
        partition = getattr(wtype, "OperationType", None)
    enum_pts = (5 if _enum_ok(predef) else 0) + (5 if _enum_ok(partition) else 0) + \
               (5 if _enum_ok(panel_op) else 0)

    psets = _psets_of(win)
    pset_pts = 5 if psets else 0

    ow = getattr(win, "OverallWidth", None)
    oh = getattr(win, "OverallHeight", None)
    dim_pts = 5 if (ow and oh) else (2 if (ow or oh) else 0)

    has_geometry = bool(geom_types)
    pis = g_pts + type_pts + detail_pts + enum_pts + pset_pts + dim_pts
    if not has_geometry and getattr(win, "Representation", None) is None:
        klass = "STUB_NO_GEOMETRY"   # bakedness N/A: window carries no shape at all
    elif pis >= 78:
        klass = "PARAMETRIC"
    elif pis >= 50:
        klass = "SEMI_PARAMETRIC"
    elif pis >= 20:
        klass = "BAKED_WITH_METADATA"
    else:
        klass = "FULLY_BAKED"

    return {
        "pis": pis, "bakedness": klass, "geometry": g_label,
        "geom_types": ",".join(sorted(geom_types)) or "-",
        "has_window_type": has_type, "has_lining_props": lining,
        "has_panel_props": panel, "has_geometry": has_geometry, "predefined_type": str(predef) if predef else "-",
        "partitioning": str(partition) if partition else "-",
        "panel_operation": str(panel_op) if panel_op else "-",
        "psets": ";".join(psets) or "-",
        "overall_w": ow, "overall_h": oh,
    }


def survey_file(path):
    m = ifcopenshell.open(path)
    schema = m.schema
    wins = m.by_type("IfcWindow")
    # group windows by their type (= "style"); fall back to Name
    groups = {}
    for w in wins:
        wt = _type_of(w)
        key = ("type", wt.id()) if wt else ("name", getattr(w, "Name", None) or "unnamed")
        groups.setdefault(key, {"type": wt, "windows": []})["windows"].append(w)

    rows = []
    for (kind, kid), g in groups.items():
        rep = g["windows"][0]
        sc = score_window(rep, g["type"])
        sc["style_key"] = f"{kind}:{kid}"
        sc["style_name"] = (g["type"].Name if g["type"] else getattr(rep, "Name", None)) or "unnamed"
        sc["count"] = len(g["windows"])
        rows.append(sc)
    return schema, len(wins), rows
