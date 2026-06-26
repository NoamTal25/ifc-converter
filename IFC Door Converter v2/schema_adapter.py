"""
schema_adapter.py — the ONE place that knows how IFC2X3 / IFC4 / IFC4X3 differ (door edition).

Mirror of the window converter's schema_adapter. The geometry recipe
(``golden_door_geometry.py``) is schema-agnostic; everything that genuinely diverges between
schema versions is funnelled through here, so a per-schema problem is a localized fix. Known
divergences handled for doors:

  * Surface styles — IFC2X3 wraps ``IfcSurfaceStyle`` inside ``IfcPresentationStyleAssignment``;
    IFC4 / IFC4X3 attach the style directly.
  * Door semantics — ``IfcDoor.PredefinedType`` / ``OperationType`` / ``UserDefinedOperationType``
    exist only in IFC4+; IFC2X3 ``IfcDoor`` has none of them (the operation lives on the style).
  * Door *type* entity — IFC4+ uses ``IfcDoorType`` (PredefinedType + OperationType); IFC2X3 uses
    ``IfcDoorStyle`` (OperationType + ConstructionType + Sizeable). The converter does NOT mint a
    type (the doors are already Revit-typed; IfcRelDefinesByType is [0:1]); ``make_door_type`` is
    provided for completeness/parity and is intentionally unused by the converter.

Every author-side helper is defensive: a schema quirk degrades to "skip this enrichment and log",
never "abort the conversion" — the geometry swap + property sets are the must-haves.
"""


# ── value / property-set authoring (value types are schema-stable) ───────────────
def psv(f, name, value, typ):
    """IfcPropertySingleValue with a typed nominal value."""
    return f.create_entity("IfcPropertySingleValue", Name=name,
                           NominalValue=f.create_entity(typ, value))


def add_pset(f, owner, name, props, obj):
    """Create an IfcPropertySet `name` with `props` and attach it to `obj`."""
    pset = f.create_entity("IfcPropertySet", GlobalId=_guid(), OwnerHistory=owner,
                           Name=name, HasProperties=props)
    relate_propertyset(f, owner, pset, obj)
    return pset


def relate_propertyset(f, owner, propdef, obj):
    """Attach an existing IfcPropertySetDefinition (Pset, lining/panel props, …) to an OCCURRENCE
    via IfcRelDefinesByProperties. Occurrence-level property definitions are many-per-element, so
    this never collides with an existing type relationship (IfcRelDefinesByType is [0:1])."""
    f.create_entity("IfcRelDefinesByProperties", GlobalId=_guid(), OwnerHistory=owner,
                    RelatingPropertyDefinition=propdef, RelatedObjects=[obj])


def _guid():
    import ifcopenshell.guid as guid
    return guid.new()


# ── door occurrence semantics (PredefinedType / OperationType) ────────────────────
def set_door_semantics(door, predef, operation, user_op=None, existing_op=None):
    """Set PredefinedType + OperationType (+ UserDefinedOperationType) where the schema/entity
    supports them. IFC2X3 IfcDoor has none of these attributes — silently no-ops there.

    PRESERVE harvested handedness: Revit doors often carry a meaningful occurrence OperationType
    (e.g. SINGLE_SWING_RIGHT, DOUBLE_DOOR_SINGLE_SWING_OPPOSITE_RIGHT). We must NOT clobber it with
    the class-canonical value (which would flip RIGHT→LEFT / drop the OPPOSITE suffix and leave the
    occurrence disagreeing with its preserved IfcDoorType). So: keep ``existing_op`` when it is
    meaningful (not None / NOTDEFINED); fall back to the class-canonical ``operation`` only to fill a
    NOTDEFINED gap (the sliding / pocket doors Revit leaves undefined)."""
    op, udo = operation, user_op
    if existing_op not in (None, "NOTDEFINED"):
        op, udo = existing_op, None      # honour the source's real handedness; not our USERDEFINED
    triples = [("PredefinedType", predef), ("OperationType", op)]
    if op == "USERDEFINED" and udo is not None:
        triples.append(("UserDefinedOperationType", udo))
    for attr, val in triples:
        if hasattr(door, attr) and val is not None:
            try:
                setattr(door, attr, val)
            except Exception:
                pass   # schema/enum mismatch → leave unset rather than fail the conversion


# ── door *type* / *style* entity (parity helper — NOT used by the converter) ──────
def make_door_type(f, owner, name, description, operation, user_op, prop_sets, door):
    """Author the type/style entity that matches the schema and link `door` to it. Provided for
    completeness/parity with the goldens; the converter does NOT call this (it would create a 2nd
    IfcRelDefinesByType, which is [0:1]-invalid on already-typed Revit doors). Returns the entity
    or None."""
    try:
        if f.schema == "IFC2X3":
            dtype = f.create_entity(
                "IfcDoorStyle", GlobalId=_guid(), OwnerHistory=owner,
                Name=name, Description=description, HasPropertySets=prop_sets or None,
                OperationType=(operation if operation != "USERDEFINED" else "USERDEFINED"),
                ConstructionType="NOTDEFINED",
                ParameterTakesPrecedence=True, Sizeable=True)
        else:
            dtype = f.create_entity(
                "IfcDoorType", GlobalId=_guid(), OwnerHistory=owner,
                Name=name, Description=description, HasPropertySets=prop_sets or None,
                PredefinedType="DOOR", OperationType=(operation or "NOTDEFINED"),
                UserDefinedOperationType=(user_op if operation == "USERDEFINED" else None))
        f.create_entity("IfcRelDefinesByType", GlobalId=_guid(), OwnerHistory=owner,
                        RelatingType=dtype, RelatedObjects=[door])
        return dtype
    except Exception:
        return None


def make_lining_props(f, owner, *, lining_depth, lining_thk, has_divider, bar_thk):
    """IfcDoorLiningProperties — the parametric lining detail (schema-stable attrs).
    A divider between leaves is recorded as TransomThickness (the nearest lining attribute)."""
    kw = dict(GlobalId=_guid(), OwnerHistory=owner, Name="Lining",
              LiningDepth=float(lining_depth), LiningThickness=float(lining_thk),
              ThresholdDepth=0.0, ThresholdThickness=0.0)
    if has_divider:
        kw["TransomThickness"] = float(bar_thk)
    try:
        return f.create_entity("IfcDoorLiningProperties", **kw)
    except Exception:
        return None


def make_panel_props(f, owner, panels, panel_depth):
    """One IfcDoorPanelProperties per (PanelPosition, PanelOperation)."""
    out = []
    for pos, op in panels:
        try:
            out.append(f.create_entity("IfcDoorPanelProperties", GlobalId=_guid(),
                                       OwnerHistory=owner, Name=f"Panel-{pos}",
                                       PanelOperation=op, PanelPosition=pos,
                                       PanelDepth=float(panel_depth)))
        except Exception:
            pass
    return out


# ── surface styles ────────────────────────────────────────────────────────────────
def wrap_surface_style(f, surface_style):
    """Wrap an IfcSurfaceStyle for an IfcStyledItem.Styles list, per schema."""
    if f.schema == "IFC2X3":
        return [f.create_entity("IfcPresentationStyleAssignment", Styles=[surface_style])]
    return [surface_style]


def build_default_styles(f):
    """Author a fallback (glass_styles, opaque_styles) pair for doors that carried none.
    Returned values are ready to drop into IfcStyledItem.Styles (schema-correct wrapping)."""
    def surf(name, rgb, transp):
        col = f.create_entity("IfcColourRgb", Red=rgb[0], Green=rgb[1], Blue=rgb[2])
        rend = f.create_entity("IfcSurfaceStyleRendering", SurfaceColour=col,
                               Transparency=float(transp), ReflectanceMethod="NOTDEFINED")
        ss = f.create_entity("IfcSurfaceStyle", Name=name, Side="BOTH", Styles=[rend])
        return wrap_surface_style(f, ss)
    return surf("Glass", (0.78, 0.87, 0.93), 0.55), surf("Door", (0.82, 0.74, 0.62), 0.0)


def iter_surface_styles(styles):
    """Yield IfcSurfaceStyle from an IfcStyledItem.Styles list, handling the IFC2X3
    IfcPresentationStyleAssignment wrapper and the direct IFC4/4X3 select."""
    for s in styles or []:
        if s.is_a("IfcPresentationStyleAssignment"):
            yield from (sub for sub in (s.Styles or []) if sub.is_a("IfcSurfaceStyle"))
        elif s.is_a("IfcSurfaceStyle"):
            yield s
