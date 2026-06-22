"""
Author parametric 'golden target' IFC4 windows, one per registry style_code.

Each file uses, deliberately, the constructs that real exports lose:
  - IfcWindowType with PredefinedType + PartitioningType
  - IfcWindowLiningProperties + one IfcWindowPanelProperties per panel
  - geometry as PARAMETERIZED swept solids (IfcRectangleHollowProfileDef /
    IfcRectangleProfileDef extruded), never baked mesh/brep
  - Pset_WindowCommon + populated OverallWidth/OverallHeight
This is the FormX output contract made concrete.
"""
import ifcopenshell
import ifcopenshell.guid as guid

# global frame parameters (mm)
LINING_DEPTH = 120.0     # frame depth into wall (Z extrusion)
LINING_THK   = 60.0      # frame face width
GLAZE_THK    = 24.0      # double-glazing unit thickness
BAR_THK      = 60.0      # mullion / transom thickness


def _pt(f, *c):   return f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(x) for x in c))
def _dir(f, *c):  return f.create_entity("IfcDirection", DirectionRatios=tuple(float(x) for x in c))

def _ax3(f, loc=(0,0,0), z=(0,0,1), x=(1,0,0)):
    return f.create_entity("IfcAxis2Placement3D", Location=_pt(f,*loc), Axis=_dir(f,*z), RefDirection=_dir(f,*x))

def _ax2(f, loc=(0,0)):
    return f.create_entity("IfcAxis2Placement2D", Location=_pt(f,*loc), RefDirection=_dir(f,1,0))

def _placement(f, rel_to=None, loc=(0,0,0)):
    return f.create_entity("IfcLocalPlacement", PlacementRelTo=rel_to, RelativePlacement=_ax3(f, loc))

def _extrude(f, profile, depth, z0=0.0):
    return f.create_entity("IfcExtrudedAreaSolid", SweptArea=profile,
                           Position=_ax3(f, loc=(0,0,z0)), ExtrudedDirection=_dir(f,0,0,1),
                           Depth=float(depth))

def _rect(f, xdim, ydim, cx=0.0, cy=0.0):
    return f.create_entity("IfcRectangleProfileDef", ProfileType="AREA",
                           Position=_ax2(f,(cx,cy)), XDim=float(xdim), YDim=float(ydim))

def _hollow(f, xdim, ydim, wall):
    return f.create_entity("IfcRectangleHollowProfileDef", ProfileType="AREA",
                           Position=_ax2(f,(0,0)), XDim=float(xdim), YDim=float(ydim),
                           WallThickness=float(wall))


def base_file():
    f = ifcopenshell.file(schema="IFC4")
    mm  = f.create_entity("IfcSIUnit", UnitType="LENGTHUNIT", Prefix="MILLI", Name="METRE")
    a2  = f.create_entity("IfcSIUnit", UnitType="AREAUNIT", Name="SQUARE_METRE")
    v3  = f.create_entity("IfcSIUnit", UnitType="VOLUMEUNIT", Name="CUBIC_METRE")
    rad = f.create_entity("IfcSIUnit", UnitType="PLANEANGLEUNIT", Name="RADIAN")
    units = f.create_entity("IfcUnitAssignment", Units=[mm,a2,v3,rad])
    ctx = f.create_entity("IfcGeometricRepresentationContext", ContextType="Model",
            CoordinateSpaceDimension=3, Precision=1e-5, WorldCoordinateSystem=_ax3(f))
    body = f.create_entity("IfcGeometricRepresentationSubContext", ContextIdentifier="Body",
            ContextType="Model", ParentContext=ctx, TargetView="MODEL_VIEW")
    person = f.create_entity("IfcPerson", FamilyName="FormX")
    org = f.create_entity("IfcOrganization", Name="FormX")
    po = f.create_entity("IfcPersonAndOrganization", ThePerson=person, TheOrganization=org)
    app = f.create_entity("IfcApplication", ApplicationDeveloper=org, Version="1.0",
            ApplicationFullName="FormX Golden Target Authoring", ApplicationIdentifier="FormX-GT")
    owner = f.create_entity("IfcOwnerHistory", OwningUser=po, OwningApplication=app,
            ChangeAction="ADDED", CreationDate=0)
    proj = f.create_entity("IfcProject", GlobalId=guid.new(), OwnerHistory=owner,
            Name="FormX Window Golden Targets", UnitsInContext=units, RepresentationContexts=[ctx])
    site = f.create_entity("IfcSite", GlobalId=guid.new(), OwnerHistory=owner, Name="Site",
            ObjectPlacement=_placement(f), CompositionType="ELEMENT")
    bldg = f.create_entity("IfcBuilding", GlobalId=guid.new(), OwnerHistory=owner, Name="Building",
            ObjectPlacement=_placement(f, site.ObjectPlacement), CompositionType="ELEMENT")
    storey = f.create_entity("IfcBuildingStorey", GlobalId=guid.new(), OwnerHistory=owner, Name="Storey",
            ObjectPlacement=_placement(f, bldg.ObjectPlacement), CompositionType="ELEMENT")
    f.create_entity("IfcRelAggregates", GlobalId=guid.new(), RelatingObject=proj, RelatedObjects=[site])
    f.create_entity("IfcRelAggregates", GlobalId=guid.new(), RelatingObject=site, RelatedObjects=[bldg])
    f.create_entity("IfcRelAggregates", GlobalId=guid.new(), RelatingObject=bldg, RelatedObjects=[storey])
    return f, owner, body, storey


def psv(f, name, val, typ):
    return f.create_entity("IfcPropertySingleValue", Name=name, NominalValue=f.create_entity(typ, val))


def build(spec, path):
    f, owner, body, storey = base_file()
    W, H = spec["W"], spec["H"]
    iW, iH = W - 2*LINING_THK, H - 2*LINING_THK          # glazed opening
    split = spec["split"]

    # --- geometry: lining frame (hollow rect) + bars + panes, all parametric ---
    items = [_extrude(f, _hollow(f, W, H, LINING_THK), LINING_DEPTH)]
    zpane = (LINING_DEPTH - GLAZE_THK)/2.0
    panes = []
    if split == "V":
        pane_w = (iW - BAR_THK)/2.0
        items.append(_extrude(f, _rect(f, BAR_THK, iH), LINING_DEPTH))           # mullion
        panes = [(-(BAR_THK/2+pane_w/2), 0, pane_w, iH), (BAR_THK/2+pane_w/2, 0, pane_w, iH)]
    elif split == "H":
        pane_h = (iH - BAR_THK)/2.0
        items.append(_extrude(f, _rect(f, iW, BAR_THK), LINING_DEPTH))           # transom
        panes = [(0, BAR_THK/2+pane_h/2, iW, pane_h), (0, -(BAR_THK/2+pane_h/2), iW, pane_h)]
    else:
        panes = [(0, 0, iW, iH)]
    for cx, cy, pw, ph in panes:
        items.append(_extrude(f, _rect(f, pw, ph, cx, cy), GLAZE_THK, z0=zpane))

    shape = f.create_entity("IfcShapeRepresentation", ContextOfItems=body,
            RepresentationIdentifier="Body", RepresentationType="SweptSolid", Items=items)
    prodshape = f.create_entity("IfcProductDefinitionShape", Representations=[shape])

    # --- parametric window-detail property sets (the crown jewels) ---
    lining_kwargs = dict(GlobalId=guid.new(), OwnerHistory=owner, Name="Lining",
            LiningDepth=LINING_DEPTH, LiningThickness=LINING_THK)
    if split == "V":
        lining_kwargs.update(MullionThickness=BAR_THK, FirstMullionOffset=W/2.0)
    if split == "H":
        lining_kwargs.update(TransomThickness=BAR_THK, FirstTransomOffset=H/2.0)
    lining = f.create_entity("IfcWindowLiningProperties", **lining_kwargs)

    panel_defs = []
    for pos, op in spec["panels"]:
        panel_defs.append(f.create_entity("IfcWindowPanelProperties", GlobalId=guid.new(),
            OwnerHistory=owner, Name=f"Panel-{pos}", OperationType=op, PanelPosition=pos,
            FrameDepth=LINING_DEPTH, FrameThickness=LINING_THK))

    wtype = f.create_entity("IfcWindowType", GlobalId=guid.new(), OwnerHistory=owner,
            Name=spec["code"], Description=spec["name"], HasPropertySets=[lining]+panel_defs,
            PredefinedType=spec["predef"], PartitioningType=spec["part"])

    window = f.create_entity("IfcWindow", GlobalId=guid.new(), OwnerHistory=owner,
            Name=spec["code"], Description=spec["name"],
            ObjectPlacement=_placement(f, storey.ObjectPlacement),
            Representation=prodshape, OverallHeight=float(H), OverallWidth=float(W),
            PredefinedType=spec["predef"], PartitioningType=spec["part"])

    f.create_entity("IfcRelDefinesByType", GlobalId=guid.new(), OwnerHistory=owner,
            RelatingType=wtype, RelatedObjects=[window])

    common = f.create_entity("IfcPropertySet", GlobalId=guid.new(), OwnerHistory=owner,
            Name="Pset_WindowCommon", HasProperties=[
                psv(f, "Reference", spec["code"], "IfcIdentifier"),
                psv(f, "IsExternal", True, "IfcBoolean"),
                psv(f, "ThermalTransmittance", 1.4, "IfcThermalTransmittanceMeasure"),
                psv(f, "Infiltration", 0.3, "IfcReal"),
                psv(f, "FireRating", "", "IfcLabel"),
            ])
    f.create_entity("IfcRelDefinesByProperties", GlobalId=guid.new(), OwnerHistory=owner,
            RelatingPropertyDefinition=common, RelatedObjects=[window])

    f.create_entity("IfcRelContainedInSpatialStructure", GlobalId=guid.new(), OwnerHistory=owner,
            RelatingStructure=storey, RelatedElements=[window])

    f.write(path)
    return path


SPECS = [
 dict(code="WIN-FIXED-SINGLE", name="Fixed (picture) window", predef="WINDOW",
      part="SINGLE_PANEL", W=1200, H=1500, split=None, panels=[("MIDDLE","FIXEDCASEMENT")]),
 dict(code="WIN-CASEMENT-SINGLE", name="Single casement (right-hung)", predef="WINDOW",
      part="SINGLE_PANEL", W=700, H=1400, split=None, panels=[("MIDDLE","SIDEHUNGRIGHTHAND")]),
 dict(code="WIN-CASEMENT-DBL_V", name="Double (French) casement", predef="WINDOW",
      part="DOUBLE_PANEL_VERTICAL", W=1400, H=1400, split="V",
      panels=[("LEFT","SIDEHUNGLEFTHAND"),("RIGHT","SIDEHUNGRIGHTHAND")]),
 dict(code="WIN-AWNING-SINGLE", name="Awning window", predef="WINDOW",
      part="SINGLE_PANEL", W=1000, H=700, split=None, panels=[("MIDDLE","TOPHUNG")]),
 dict(code="WIN-HOPPER-SINGLE", name="Hopper window", predef="WINDOW",
      part="SINGLE_PANEL", W=1000, H=700, split=None, panels=[("MIDDLE","BOTTOMHUNG")]),
 dict(code="WIN-HUNG-DBL_H", name="Hung window (sub-type undetermined)", predef="WINDOW",
      part="DOUBLE_PANEL_HORIZONTAL", W=900, H=1600, split="H",
      panels=[("TOP","SLIDINGVERTICAL"),("BOTTOM","SLIDINGVERTICAL")]),
 dict(code="WIN-SINGLEHUNG-DBL_H", name="Single-hung", predef="WINDOW",
      part="DOUBLE_PANEL_HORIZONTAL", W=900, H=1600, split="H",
      panels=[("TOP","FIXEDCASEMENT"),("BOTTOM","SLIDINGVERTICAL")]),
 dict(code="WIN-DOUBLEHUNG-DBL_H", name="Double-hung", predef="WINDOW",
      part="DOUBLE_PANEL_HORIZONTAL", W=900, H=1600, split="H",
      panels=[("TOP","SLIDINGVERTICAL"),("BOTTOM","SLIDINGVERTICAL")]),
 dict(code="WIN-SLIDER-DBL_V", name="Horizontal slider, 2-panel", predef="WINDOW",
      part="DOUBLE_PANEL_VERTICAL", W=1800, H=1200, split="V",
      panels=[("LEFT","SLIDINGHORIZONTAL"),("RIGHT","SLIDINGHORIZONTAL")]),
 dict(code="WIN-TILTTURN-SINGLE", name="Tilt-and-turn (right)", predef="WINDOW",
      part="SINGLE_PANEL", W=800, H=1400, split=None, panels=[("MIDDLE","TILTANDTURNRIGHTHAND")]),
 dict(code="WIN-PIVOTH-SINGLE", name="Horizontal pivot", predef="WINDOW",
      part="SINGLE_PANEL", W=1200, H=1200, split=None, panels=[("MIDDLE","PIVOTHORIZONTAL")]),
 dict(code="WIN-PIVOTV-SINGLE", name="Vertical pivot", predef="WINDOW",
      part="SINGLE_PANEL", W=1200, H=1200, split=None, panels=[("MIDDLE","PIVOTVERTICAL")]),
 dict(code="WIN-SKYLIGHT-SINGLE", name="Skylight / roof window", predef="SKYLIGHT",
      part="SINGLE_PANEL", W=1000, H=1000, split=None, panels=[("MIDDLE","FIXEDCASEMENT")]),
]
