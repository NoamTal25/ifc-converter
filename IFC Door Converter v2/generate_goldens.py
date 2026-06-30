"""
Generate the 16 FormX golden-template door IFCs — one per FormX door type defined in the
IFC Standardizer Template Gallery spec (the DOORS section of the PDF).

FormX door types (from the PDF, in listed order):
  DOOR_SINGLE, DOOR_POCKET, DOOR_DOUBLE, DOOR_SHOWER, DOOR_SLIDING, DOOR_SLIDE_AND_SWING,
  DOOR_BIFOLDING_GLASS, DOOR_INTERIOR_BIFOLDING_2_PANEL, DOOR_INTERIOR_SINGLE,
  DOOR_SINGLE_FLUSH, DOOR_SLIDING_SWING_COMBO, DOOR_BIFOLDING_SWING_COMBO,
  DOOR_INTERIOR_DOUBLE, DOOR_OPENING, DOOR_BARN, DOOR_SINGLE_BARN

Each golden carries the FormX door param contract:
  - OverallWidth / OverallHeight (IfcDoor attributes; nominal defaults — injected per-instance)
  - Pset_DoorCommon: Reference, IsExternal, OverallWidth, OverallHeight, Depth,
    RoughWidth, RoughHeight
  - Pset FormX_Door_Window: HandFlipped / FacingFlipped (IfcBoolean) — the PDF's explicit Pset
  - IfcDoorLiningProperties + one IfcDoorPanelProperties per panel
  - IfcDoorType (PredefinedType=DOOR + OperationType) linked via IfcRelDefinesByType
  - Fully parametric swept geometry (no baked mesh), authored through the SHARED recipe in
    golden_door_geometry.py — so the goldens and the converter produce identical geometry.

Run with:  python3.11 generate_goldens.py
Outputs go to:  ./golden_templates/
"""

import os
import sys
import ifcopenshell
import ifcopenshell.guid as guid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import golden_door_geometry as gg
import door_types

# ---------------------------------------------------------------------------
# Global frame defaults (mm) — imported from the SHARED recipe so the goldens
# and the converter author identical geometry (the converter scales these).
# ---------------------------------------------------------------------------
LINING_DEPTH = gg.LINING_DEPTH
LINING_THK   = gg.LINING_THK
SLAB_THK     = gg.SLAB_THK
BAR_THK      = gg.BAR_THK

OUT_DIR = os.path.join(os.path.dirname(__file__), "golden_templates")


# ---------------------------------------------------------------------------
# IFC geometry / placement helpers
# ---------------------------------------------------------------------------
def _pt(f, *c):
    return f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(x) for x in c))

def _dir(f, *c):
    return f.create_entity("IfcDirection", DirectionRatios=tuple(float(x) for x in c))

def _ax3(f, loc=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0)):
    return f.create_entity("IfcAxis2Placement3D",
                           Location=_pt(f, *loc), Axis=_dir(f, *z), RefDirection=_dir(f, *x))

def _placement(f, rel_to=None, loc=(0, 0, 0)):
    return f.create_entity("IfcLocalPlacement",
                           PlacementRelTo=rel_to, RelativePlacement=_ax3(f, loc))


# ---------------------------------------------------------------------------
# Base IFC4 file scaffold (mirrors the window golden generator)
# ---------------------------------------------------------------------------
def _base_file():
    f = ifcopenshell.file(schema="IFC4")
    mm  = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE")
    a2  = f.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE")
    v3  = f.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE")
    rad = f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN")
    units = f.create_entity("IfcUnitAssignment", Units=[mm, a2, v3, rad])

    ctx = f.create_entity("IfcGeometricRepresentationContext",
                          ContextType="Model", CoordinateSpaceDimension=3,
                          Precision=1e-5, WorldCoordinateSystem=_ax3(f))
    body = f.create_entity("IfcGeometricRepresentationSubContext",
                           ContextIdentifier="Body", ContextType="Model",
                           ParentContext=ctx, TargetView="MODEL_VIEW")

    person = f.create_entity("IfcPerson", FamilyName="FormX")
    org    = f.create_entity("IfcOrganization", Name="FormX")
    po     = f.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=org)
    app    = f.create_entity("IfcApplication", ApplicationDeveloper=org, Version="2.0",
                             ApplicationFullName="FormX Door Golden Template Generator",
                             ApplicationIdentifier="FormX-GT-D2")
    owner  = f.create_entity("IfcOwnerHistory", OwningUser=po, OwningApplication=app,
                             ChangeAction="ADDED", CreationDate=0)

    proj   = f.create_entity("IfcProject", GlobalId=guid.new(), OwnerHistory=owner,
                             Name="FormX Door Golden Templates v2",
                             UnitsInContext=units, RepresentationContexts=[ctx])
    site   = f.create_entity("IfcSite", GlobalId=guid.new(), OwnerHistory=owner,
                             Name="Site", ObjectPlacement=_placement(f),
                             CompositionType="ELEMENT")
    bldg   = f.create_entity("IfcBuilding", GlobalId=guid.new(), OwnerHistory=owner,
                             Name="Building",
                             ObjectPlacement=_placement(f, site.ObjectPlacement),
                             CompositionType="ELEMENT")
    storey = f.create_entity("IfcBuildingStorey", GlobalId=guid.new(), OwnerHistory=owner,
                             Name="Storey",
                             ObjectPlacement=_placement(f, bldg.ObjectPlacement),
                             CompositionType="ELEMENT")

    f.create_entity("IfcRelAggregates", GlobalId=guid.new(),
                    RelatingObject=proj, RelatedObjects=[site])
    f.create_entity("IfcRelAggregates", GlobalId=guid.new(),
                    RelatingObject=site, RelatedObjects=[bldg])
    f.create_entity("IfcRelAggregates", GlobalId=guid.new(),
                    RelatingObject=bldg, RelatedObjects=[storey])

    return f, owner, body, storey


def _psv(f, name, val, typ):
    return f.create_entity("IfcPropertySingleValue",
                           Name=name, NominalValue=f.create_entity(typ, val))


# ---------------------------------------------------------------------------
# Surface styles (opaque frame/panel + transparent glass) — IFC4 attaches directly.
# ---------------------------------------------------------------------------
def _surf(f, name, rgb, transp):
    col  = f.create_entity("IfcColourRgb", Red=rgb[0], Green=rgb[1], Blue=rgb[2])
    rend = f.create_entity("IfcSurfaceStyleRendering", SurfaceColour=col,
                           Transparency=float(transp), ReflectanceMethod="NOTDEFINED")
    return f.create_entity("IfcSurfaceStyle", Name=name, Side="BOTH", Styles=[rend])

def _style_item(f, solid, surface_style):
    f.create_entity("IfcStyledItem", Item=solid, Styles=[surface_style])

# role → which style bucket; glazed panels get glass, everything else opaque.
GLASS_RGB = (0.78, 0.87, 0.93)
FRAME_RGB = (0.55, 0.55, 0.55)
SLAB_RGB  = (0.82, 0.74, 0.62)   # warm wood-ish for opaque leaves
HW_RGB    = (0.30, 0.30, 0.32)   # dark metal for handles/track/rollers


# ---------------------------------------------------------------------------
# Geometry builder — author the body via the SHARED recipe, attach styles by role.
# ---------------------------------------------------------------------------
def _build_geometry(f, body, W, H, recipe):
    # Golden files are authored in millimetres → unit_scale = 0.001 → dims_in_units is the
    # identity (canonical mm). The converter calls dims_in_units(measured scale) instead.
    dims = gg.dims_in_units(0.001)
    items = gg.build_door_items(
        f, W, H, LINING_DEPTH, recipe=recipe, dims=dims,
        center=(0.0, 0.0, LINING_DEPTH / 2.0), depth_dir=(0, 0, 1), width_dir=(1, 0, 0))

    surf_by_bucket = {
        "glass": _surf(f, "Glass", GLASS_RGB, 0.55),
        "frame": _surf(f, "Frame", FRAME_RGB, 0.0),
        "slab":  _surf(f, "Leaf",  SLAB_RGB, 0.0),
        "metal": _surf(f, "Hardware", HW_RGB, 0.0),
    }
    glazed = bool(recipe.get("glazed", False))

    # role → colour bucket is centralised in golden_door_geometry (single source of truth).
    for solid, role in items:
        _style_item(f, solid, surf_by_bucket[gg.bucket_for(role, glazed)])

    shape = f.create_entity("IfcShapeRepresentation",
                            ContextOfItems=body, RepresentationIdentifier="Body",
                            RepresentationType="SweptSolid",
                            Items=[solid for solid, _role in items])
    return f.create_entity("IfcProductDefinitionShape", Representations=[shape])


# ---------------------------------------------------------------------------
# Property sets
# ---------------------------------------------------------------------------
def _build_psets(f, owner, door, spec):
    W, H = spec["W"], spec["H"]
    rough_margin = 2 * LINING_THK
    common = [
        _psv(f, "Reference",     spec["formx_type"], "IfcIdentifier"),
        _psv(f, "IsExternal",    spec.get("is_external", False), "IfcBoolean"),
        _psv(f, "OverallWidth",  W,                  "IfcPositiveLengthMeasure"),
        _psv(f, "OverallHeight", H,                  "IfcPositiveLengthMeasure"),
        _psv(f, "Depth",         LINING_DEPTH,       "IfcPositiveLengthMeasure"),
        _psv(f, "RoughWidth",    W + rough_margin,   "IfcPositiveLengthMeasure"),
        _psv(f, "RoughHeight",   H + rough_margin / 2.0, "IfcPositiveLengthMeasure"),
    ]
    pset_common = f.create_entity("IfcPropertySet", GlobalId=guid.new(), OwnerHistory=owner,
                                  Name="Pset_DoorCommon", HasProperties=common)
    f.create_entity("IfcRelDefinesByProperties", GlobalId=guid.new(), OwnerHistory=owner,
                    RelatingPropertyDefinition=pset_common, RelatedObjects=[door])

    # FormX_Door_Window — the PDF's explicit Pset name for the two flip booleans.
    formx = [
        _psv(f, "HandFlipped",   False, "IfcBoolean"),
        _psv(f, "FacingFlipped", False, "IfcBoolean"),
    ]
    pset_formx = f.create_entity("IfcPropertySet", GlobalId=guid.new(), OwnerHistory=owner,
                                 Name="FormX_Door_Window", HasProperties=formx)
    f.create_entity("IfcRelDefinesByProperties", GlobalId=guid.new(), OwnerHistory=owner,
                    RelatingPropertyDefinition=pset_formx, RelatedObjects=[door])


# ---------------------------------------------------------------------------
# Lining + panel properties
# ---------------------------------------------------------------------------
def _build_door_props(f, owner, spec):
    """Author the lining + per-panel property ENTITIES value-LESS (no dimensional fields), matching
    the flush FormX-native reference. Gaudi renders an IfcDoor parametrically from these *values* —
    a valued lining/panel set makes it draw its own inset panel (the "gap"); a value-less set lets
    it render the flush Body mesh. The geometry (build_door_items) is unchanged. (CLAUDE.md §6.)"""
    lining = f.create_entity("IfcDoorLiningProperties",
                             GlobalId=guid.new(), OwnerHistory=owner, Name="Lining")
    n = spec["recipe"].get("panels", 1)
    panels = []
    positions = spec.get("panel_positions") or _default_positions(n)
    for pos, op in positions:
        panels.append(f.create_entity("IfcDoorPanelProperties",
                                      GlobalId=guid.new(), OwnerHistory=owner,
                                      Name=f"Panel-{pos}",
                                      PanelOperation=op, PanelPosition=pos))
    return lining, panels


def _default_positions(n):
    """Default (PanelPosition, PanelOperation) pairs for n panels."""
    if n <= 0:
        return []
    if n == 1:
        return [("RIGHT", "SWINGING")]
    if n == 2:
        return [("LEFT", "SWINGING"), ("RIGHT", "SWINGING")]
    return [("LEFT", "SWINGING")] + [("MIDDLE", "SWINGING")] * (n - 2) + [("RIGHT", "SWINGING")]


# ---------------------------------------------------------------------------
# Build one golden IFC
# ---------------------------------------------------------------------------
def build_golden(spec, out_dir):
    f, owner, body, storey = _base_file()
    W, H = spec["W"], spec["H"]

    prod_shape = _build_geometry(f, body, W, H, spec["recipe"])
    lining, panels = _build_door_props(f, owner, spec)

    # USERDEFINED operation (the slide+swing combos, whose mixed panels match no single enum)
    # must carry a UserDefinedOperationType label per the schema.
    udo = spec.get("user_op") if spec["operation"] == "USERDEFINED" else None

    dtype = f.create_entity("IfcDoorType",
                            GlobalId=guid.new(), OwnerHistory=owner,
                            Name=spec["formx_type"], Description=spec["description"],
                            HasPropertySets=([lining] + panels) or None,
                            PredefinedType="DOOR",
                            OperationType=spec["operation"],
                            UserDefinedOperationType=udo)

    door = f.create_entity("IfcDoor",
                           GlobalId=guid.new(), OwnerHistory=owner,
                           Name=spec["formx_type"], Description=spec["description"],
                           ObjectPlacement=_placement(f, storey.ObjectPlacement),
                           Representation=prod_shape,
                           OverallHeight=float(H), OverallWidth=float(W),
                           PredefinedType="DOOR",
                           OperationType=spec["operation"],
                           UserDefinedOperationType=udo)

    f.create_entity("IfcRelDefinesByType", GlobalId=guid.new(), OwnerHistory=owner,
                    RelatingType=dtype, RelatedObjects=[door])
    f.create_entity("IfcRelContainedInSpatialStructure", GlobalId=guid.new(),
                    OwnerHistory=owner, RelatingStructure=storey, RelatedElements=[door])

    _build_psets(f, owner, door, spec)

    path = os.path.join(out_dir, spec["filename"])
    f.write(path)
    n = spec["recipe"].get("panels", 1)
    print(f"  wrote {spec['filename']:<38}  ({W:.0f} × {H:.0f} mm, {n} panel(s))")
    return path


# ---------------------------------------------------------------------------
# Golden specs — built from the single source of truth (door_types.TYPES),
# so the catalog is edited in exactly ONE place. build_golden() consumes this
# flat shape (formx_type / filename / W / H / recipe / operation / …).
# ---------------------------------------------------------------------------
def _spec_from_type(formx_type):
    td = door_types.TYPES[formx_type]
    return dict(formx_type=formx_type, filename=td["filename"], description=td["description"],
                W=td["nom_w"], H=td["nom_h"], recipe=td["recipe"], operation=td["operation"],
                is_external=td["is_external"], panel_positions=td["panel_positions"],
                user_op=td["user_op"])


GOLDEN_SPECS = [_spec_from_type(t) for t in door_types.ORDER]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Generating {len(GOLDEN_SPECS)} golden door template IFCs → {OUT_DIR}/\n")
    for spec in GOLDEN_SPECS:
        build_golden(spec, OUT_DIR)
    print(f"\nDone. Open the .ifc files in your viewer to inspect geometry.")
