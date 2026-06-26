"""
schema_adapter.py — the ONE place that knows how IFC2X3 / IFC4 / IFC4X3 differ.

The geometry recipe (``golden_geometry.py``) is schema-agnostic; everything that genuinely
diverges between schema versions is funnelled through here, so a per-schema problem is a
localized fix (and, per the project note, a candidate for a divergent standardized procedure
per IFC type later). Known divergences handled:

  * Surface styles — IFC2X3 wraps ``IfcSurfaceStyle`` inside ``IfcPresentationStyleAssignment``;
    IFC4 / IFC4X3 attach the style directly.
  * Window semantics — ``IfcWindow.PredefinedType`` / ``PartitioningType`` exist only in
    IFC4+; IFC2X3 ``IfcWindow`` has neither.
  * Window *type* entity — IFC4+ uses ``IfcWindowType`` (PredefinedType + PartitioningType);
    IFC2X3 uses ``IfcWindowStyle`` (ConstructionType + OperationType + Sizeable).

Every author-side helper is defensive: a schema quirk degrades to "skip this enrichment and
log", never "abort the conversion" — the geometry swap + Pset are the must-haves.
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


# ── window occurrence semantics (PredefinedType / PartitioningType) ───────────────
def set_window_semantics(win, predef, part_type):
    """Set PredefinedType + PartitioningType where the schema/entity supports them.
    IFC2X3 IfcWindow has neither attribute — silently no-ops there."""
    for attr, val in (("PredefinedType", predef), ("PartitioningType", part_type)):
        if hasattr(win, attr) and val is not None:
            try:
                setattr(win, attr, val)
            except Exception:
                pass   # schema/enum mismatch → leave unset rather than fail the conversion


# ── window *type* / *style* entity + relating ─────────────────────────────────────
def make_window_type(f, owner, name, description, predef, part_type, prop_sets, win):
    """Author the type/style entity that matches the schema and link `win` to it.

    IFC4/4X3 → IfcWindowType (PredefinedType + PartitioningType).
    IFC2X3   → IfcWindowStyle (OperationType carries the partitioning; no window/skylight split).
    Returns the type entity, or None if the schema rejects it (logged by caller)."""
    schema = f.schema
    try:
        if schema == "IFC2X3":
            wtype = f.create_entity(
                "IfcWindowStyle", GlobalId=_guid(), OwnerHistory=owner,
                Name=name, Description=description, HasPropertySets=prop_sets,
                ConstructionType="NOTDEFINED",
                OperationType=(part_type or "NOTDEFINED"),
                ParameterTakesPrecedence=True, Sizeable=True)
        else:
            wtype = f.create_entity(
                "IfcWindowType", GlobalId=_guid(), OwnerHistory=owner,
                Name=name, Description=description, HasPropertySets=prop_sets,
                PredefinedType=(predef or "WINDOW"),
                PartitioningType=(part_type or "NOTDEFINED"))
        f.create_entity("IfcRelDefinesByType", GlobalId=_guid(), OwnerHistory=owner,
                        RelatingType=wtype, RelatedObjects=[win])
        return wtype
    except Exception:
        return None


def make_lining_props(f, owner):
    """IfcWindowLiningProperties — authored VALUE-LESS (entity + Name only, no dimensional fields),
    matching the flush FormX-native reference (HUDSON_ADU). The FormX dimension contract rides on
    Pset_WindowCommon + IfcWindow.OverallWidth/Height; the frame is real geometry (4 solid bars),
    so nothing parametric is lost. (CLAUDE.md §6.)"""
    try:
        return f.create_entity("IfcWindowLiningProperties", GlobalId=_guid(),
                               OwnerHistory=owner, Name="Lining")
    except Exception:
        return None


def make_panel_props(f, owner, panels):
    """One IfcWindowPanelProperties per (panel_position, operation_type), VALUE-LESS — only the
    OperationType + PanelPosition enums (the meaningful FormX semantics); no FrameDepth/FrameThickness
    (the flush native reference leaves them null)."""
    out = []
    for pos, op in panels:
        try:
            out.append(f.create_entity("IfcWindowPanelProperties", GlobalId=_guid(),
                                       OwnerHistory=owner, Name=f"Panel-{pos}",
                                       OperationType=op, PanelPosition=pos))
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
    """Author a fallback (glass_styles, frame_styles) pair for windows that carried none.
    Returned values are ready to drop into IfcStyledItem.Styles (schema-correct wrapping)."""
    def surf(name, rgb, transp):
        col = f.create_entity("IfcColourRgb", Red=rgb[0], Green=rgb[1], Blue=rgb[2])
        rend = f.create_entity("IfcSurfaceStyleRendering", SurfaceColour=col,
                               Transparency=float(transp), ReflectanceMethod="NOTDEFINED")
        ss = f.create_entity("IfcSurfaceStyle", Name=name, Side="BOTH", Styles=[rend])
        return wrap_surface_style(f, ss)
    return surf("Glass", (0.78, 0.87, 0.93), 0.55), surf("Frame", (0.55, 0.55, 0.55), 0.0)


def iter_surface_styles(styles):
    """Yield IfcSurfaceStyle from an IfcStyledItem.Styles list, handling the IFC2X3
    IfcPresentationStyleAssignment wrapper and the direct IFC4/4X3 select."""
    for s in styles or []:
        if s.is_a("IfcPresentationStyleAssignment"):
            yield from (sub for sub in (s.Styles or []) if sub.is_a("IfcSurfaceStyle"))
        elif s.is_a("IfcSurfaceStyle"):
            yield s
