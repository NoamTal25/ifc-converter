"""
schema_adapter.py — occurrence-level property-set authoring (accessories edition).

A deliberate SUBSET of the door/window converters' schema_adapter: this converter authors NO
geometry, NO surface styles, and NO element type — it only adds a ``FormX_Accessory`` property set at
the OCCURRENCE level. So the only helpers needed are the four schema-stable property-set authoring
functions, copied verbatim from ``IFC Door Converter v2/schema_adapter.py`` (lines 24–48).

Why occurrence-level (never a 2nd type): an occurrence has at most one type
(``IfcRelDefinesByType`` is [0:1]); Revit accessories are already typed. Property *definitions*
attach via ``IfcRelDefinesByProperties``, which is many-per-element, so this never collides with the
existing type relationship (CLAUDE.md §6).

Value types (``IfcLabel`` / ``IfcIdentifier`` / ``IfcBoolean``) exist in IFC2X3, IFC4 and IFC4X3, so
no per-schema branching is required here.
"""


def psv(f, name, value, typ):
    """IfcPropertySingleValue with a typed nominal value."""
    return f.create_entity("IfcPropertySingleValue", Name=name,
                           NominalValue=f.create_entity(typ, value))


def add_pset(f, owner, name, props, obj):
    """Create an IfcPropertySet `name` with `props` and attach it to `obj`. Returns the pset."""
    pset = f.create_entity("IfcPropertySet", GlobalId=_guid(), OwnerHistory=owner,
                           Name=name, HasProperties=props)
    relate_propertyset(f, owner, pset, obj)
    return pset


def relate_propertyset(f, owner, propdef, obj):
    """Attach an existing IfcPropertySetDefinition to an OCCURRENCE via IfcRelDefinesByProperties.
    Occurrence-level definitions are many-per-element, so this never collides with the [0:1] type
    relationship. RelatedObjects is always [obj] (one element per relationship)."""
    f.create_entity("IfcRelDefinesByProperties", GlobalId=_guid(), OwnerHistory=owner,
                    RelatingPropertyDefinition=propdef, RelatedObjects=[obj])


def _guid():
    import ifcopenshell.guid as guid
    return guid.new()
