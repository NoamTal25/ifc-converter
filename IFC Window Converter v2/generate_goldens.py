"""
Generate 7 FormX golden-template window IFCs — one per FormX window type defined in
the IFC Standardizer Template Gallery spec.

FormX window types (from PDF):
  SINGLE_PANEL_WINDOW × 5 panel subtypes: FIXED, CASEMENT, AWNING, SLIDER, DOUBLE_HUNG
  DOUBLE_HORIZONTAL_WINDOW — two panels side-by-side (vertical mullion)
  DOUBLE_VERTICAL_WINDOW   — two panels stacked (horizontal transom)

Each golden carries:
  - OverallWidth / OverallHeight (nominal defaults — injected per-instance during conversion)
  - Depth (lining depth)
  - RoughWidth / RoughHeight (rough-opening, stored in Pset_WindowCommon)
  - HandFlipped / FacingFlipped (IfcBoolean, Pset_WindowCommon)
  - SplitWidth (DOUBLE_HORIZONTAL) or SplitHeight (DOUBLE_VERTICAL)
  - IfcWindowLiningProperties + IfcWindowPanelProperties per panel
  - Fully parametric swept geometry (no baked mesh)

Run with:  python3.11 generate_goldens.py
Outputs go to:  ./golden_templates/
"""

import os
import sys
import ifcopenshell
import ifcopenshell.guid as guid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import golden_geometry as gg

# ---------------------------------------------------------------------------
# Global frame defaults (mm) — imported from the SHARED recipe so the goldens
# and the converter author identical geometry (the converter scales these).
# ---------------------------------------------------------------------------
LINING_DEPTH = gg.LINING_DEPTH   # frame depth into wall (extrusion depth)
LINING_THK   = gg.LINING_THK     # frame face width
GLAZE_THK    = gg.GLAZE_THK      # glazing unit thickness
BAR_THK      = gg.BAR_THK        # mullion / transom thickness

# Nominal default window dimensions (mm). The converter replaces these.
NOM_W_SINGLE  =  900.0
NOM_H_SINGLE  = 1200.0
NOM_W_DBL_H   = 1800.0   # DOUBLE_HORIZONTAL: wide
NOM_H_DBL_H   = 1200.0
NOM_W_DBL_V   =  900.0
NOM_H_DBL_V   = 2400.0   # DOUBLE_VERTICAL: tall

OUT_DIR = os.path.join(os.path.dirname(__file__), "golden_templates")


# ---------------------------------------------------------------------------
# IFC geometry helpers
# ---------------------------------------------------------------------------
def _pt(f, *c):
    return f.create_entity("IfcCartesianPoint", Coordinates=tuple(float(x) for x in c))

def _dir(f, *c):
    return f.create_entity("IfcDirection", DirectionRatios=tuple(float(x) for x in c))

def _ax3(f, loc=(0, 0, 0), z=(0, 0, 1), x=(1, 0, 0)):
    return f.create_entity("IfcAxis2Placement3D",
                           Location=_pt(f, *loc),
                           Axis=_dir(f, *z),
                           RefDirection=_dir(f, *x))

def _placement(f, rel_to=None, loc=(0, 0, 0)):
    return f.create_entity("IfcLocalPlacement",
                           PlacementRelTo=rel_to,
                           RelativePlacement=_ax3(f, loc))


# ---------------------------------------------------------------------------
# Base IFC4 file scaffold
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
                             ApplicationFullName="FormX Golden Template Generator",
                             ApplicationIdentifier="FormX-GT2")
    owner  = f.create_entity("IfcOwnerHistory", OwningUser=po, OwningApplication=app,
                             ChangeAction="ADDED", CreationDate=0)

    proj   = f.create_entity("IfcProject", GlobalId=guid.new(), OwnerHistory=owner,
                             Name="FormX Window Golden Templates v2",
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
                           Name=name,
                           NominalValue=f.create_entity(typ, val))


# ---------------------------------------------------------------------------
# Geometry builder
# ---------------------------------------------------------------------------
def _build_geometry(f, body, W, H, split):
    """Author the window body via the SHARED recipe (golden_geometry.build_window_items),
    so the goldens and the converter are guaranteed to produce identical geometry.

    Axis-aligned for the standalone golden: depth along +Z (the frame spans 0..LINING_DEPTH),
    width along +X, height along +Y. split: None → single pane · 'V' → vertical mullion ·
    'H' → horizontal transom."""
    items = gg.build_window_items(
        f, W, H, LINING_DEPTH,
        frame_thk=LINING_THK, glaze_thk=GLAZE_THK, bar_thk=BAR_THK, split=split,
        center=(0.0, 0.0, LINING_DEPTH / 2.0), depth_dir=(0, 0, 1), width_dir=(1, 0, 0))
    shape = f.create_entity("IfcShapeRepresentation",
                            ContextOfItems=body,
                            RepresentationIdentifier="Body",
                            RepresentationType="SweptSolid",
                            Items=[solid for solid, _role in items])
    return f.create_entity("IfcProductDefinitionShape", Representations=[shape])


# ---------------------------------------------------------------------------
# Property set builder
# ---------------------------------------------------------------------------
def _build_psets(f, owner, window, spec):
    """Attach Pset_WindowCommon with FormX-spec properties."""
    W, H = spec["W"], spec["H"]
    rough_margin = 2 * LINING_THK   # simple rough-opening = window + 2 × lining

    props = [
        _psv(f, "Reference",      spec["formx_type"],    "IfcIdentifier"),
        _psv(f, "IsExternal",     True,                  "IfcBoolean"),
        _psv(f, "OverallWidth",   W,                     "IfcPositiveLengthMeasure"),
        _psv(f, "OverallHeight",  H,                     "IfcPositiveLengthMeasure"),
        _psv(f, "Depth",          LINING_DEPTH,          "IfcPositiveLengthMeasure"),
        _psv(f, "RoughWidth",     W + rough_margin,      "IfcPositiveLengthMeasure"),
        _psv(f, "RoughHeight",    H + rough_margin,      "IfcPositiveLengthMeasure"),
        _psv(f, "HandFlipped",    False,                 "IfcBoolean"),
        _psv(f, "FacingFlipped",  False,                 "IfcBoolean"),
        _psv(f, "PanelType",      spec["panel_type"],    "IfcLabel"),
    ]

    # DOUBLE_ types expose split position + per-panel types
    if spec.get("split") == "V":
        split_w = (spec["W"] - 2 * LINING_THK - BAR_THK) / 2.0
        props += [
            _psv(f, "SplitWidth",      split_w,               "IfcPositiveLengthMeasure"),
            _psv(f, "PanelTypeLeft",   spec["panel_type"],    "IfcLabel"),
            _psv(f, "PanelTypeRight",  spec["panel_type"],    "IfcLabel"),
        ]
    elif spec.get("split") == "H":
        split_h = (spec["H"] - 2 * LINING_THK - BAR_THK) / 2.0
        props += [
            _psv(f, "SplitHeight",     split_h,               "IfcPositiveLengthMeasure"),
            _psv(f, "PanelTypeTop",    spec["panel_type"],    "IfcLabel"),
            _psv(f, "PanelTypeBottom", spec["panel_type"],    "IfcLabel"),
        ]

    pset = f.create_entity("IfcPropertySet", GlobalId=guid.new(), OwnerHistory=owner,
                           Name="Pset_WindowCommon", HasProperties=props)
    f.create_entity("IfcRelDefinesByProperties", GlobalId=guid.new(), OwnerHistory=owner,
                    RelatingPropertyDefinition=pset, RelatedObjects=[window])


# ---------------------------------------------------------------------------
# Lining + panel properties
# ---------------------------------------------------------------------------
def _build_window_props(f, owner, spec):
    """Author the lining + per-panel property ENTITIES value-LESS (no dimensional fields), matching
    the flush FormX-native reference. Gaudi renders an IfcWindow parametrically from these *values*
    — a valued lining/panel set makes it draw its own inset pane (the "gap"); a value-less set lets
    it render the flush Body mesh. The geometry (build_window_items) is unchanged. (CLAUDE.md §6.)"""
    lining = f.create_entity("IfcWindowLiningProperties",
                             GlobalId=guid.new(), OwnerHistory=owner, Name="Lining")
    panels = []
    for pos, op_type in spec["ifc_panels"]:
        panels.append(f.create_entity("IfcWindowPanelProperties",
                                      GlobalId=guid.new(), OwnerHistory=owner,
                                      Name=f"Panel-{pos}",
                                      OperationType=op_type,
                                      PanelPosition=pos))
    return lining, panels


# ---------------------------------------------------------------------------
# Build one golden IFC
# ---------------------------------------------------------------------------
def build_golden(spec, out_dir):
    f, owner, body, storey = _base_file()
    W, H = spec["W"], spec["H"]

    prod_shape = _build_geometry(f, body, W, H, spec.get("split"))
    lining, panels = _build_window_props(f, owner, spec)

    wtype = f.create_entity("IfcWindowType",
                            GlobalId=guid.new(), OwnerHistory=owner,
                            Name=spec["formx_type"],
                            Description=spec["description"],
                            HasPropertySets=[lining] + panels,
                            PredefinedType=spec["predef"],
                            PartitioningType=spec["part_type"])

    window = f.create_entity("IfcWindow",
                             GlobalId=guid.new(), OwnerHistory=owner,
                             Name=spec["formx_type"],
                             Description=spec["description"],
                             ObjectPlacement=_placement(f, storey.ObjectPlacement),
                             Representation=prod_shape,
                             OverallHeight=float(H),
                             OverallWidth=float(W),
                             PredefinedType=spec["predef"],
                             PartitioningType=spec["part_type"])

    f.create_entity("IfcRelDefinesByType", GlobalId=guid.new(), OwnerHistory=owner,
                    RelatingType=wtype, RelatedObjects=[window])
    f.create_entity("IfcRelContainedInSpatialStructure", GlobalId=guid.new(),
                    OwnerHistory=owner, RelatingStructure=storey,
                    RelatedElements=[window])

    _build_psets(f, owner, window, spec)

    path = os.path.join(out_dir, spec["filename"])
    f.write(path)
    print(f"  wrote {spec['filename']}  ({W:.0f} × {H:.0f} mm)")
    return path


# ---------------------------------------------------------------------------
# Golden specs — one per FormX window type
# ---------------------------------------------------------------------------
GOLDEN_SPECS = [
    # ── Single-panel types ──────────────────────────────────────────────────
    dict(
        formx_type  = "SINGLE_PANEL_WINDOW",
        panel_type  = "WINDOW_PANEL_FIXED",
        description = "Fixed (picture) window — single non-operable pane",
        predef      = "WINDOW",
        part_type   = "SINGLE_PANEL",
        W           = NOM_W_SINGLE,
        H           = NOM_H_SINGLE,
        split       = None,
        ifc_panels  = [("MIDDLE", "FIXEDCASEMENT")],
        filename    = "SINGLE-FIXED.ifc",
    ),
    dict(
        formx_type  = "SINGLE_PANEL_WINDOW",
        panel_type  = "WINDOW_PANEL_CASEMENT",
        description = "Casement window — single side-hung operable sash",
        predef      = "WINDOW",
        part_type   = "SINGLE_PANEL",
        W           = NOM_W_SINGLE,
        H           = NOM_H_SINGLE,
        split       = None,
        ifc_panels  = [("MIDDLE", "SIDEHUNGRIGHTHAND")],
        filename    = "SINGLE-CASEMENT.ifc",
    ),
    dict(
        formx_type  = "SINGLE_PANEL_WINDOW",
        panel_type  = "WINDOW_PANEL_AWNING",
        description = "Awning window — top-hung, opens outward at bottom",
        predef      = "WINDOW",
        part_type   = "SINGLE_PANEL",
        W           = NOM_W_SINGLE,
        H           = NOM_H_SINGLE,
        split       = None,
        ifc_panels  = [("MIDDLE", "TOPHUNG")],
        filename    = "SINGLE-AWNING.ifc",
    ),
    dict(
        formx_type  = "SINGLE_PANEL_WINDOW",
        panel_type  = "WINDOW_PANEL_SLIDER",
        description = "Slider window — single sash slides horizontally",
        predef      = "WINDOW",
        part_type   = "SINGLE_PANEL",
        W           = NOM_W_SINGLE,
        H           = NOM_H_SINGLE,
        split       = None,
        ifc_panels  = [("MIDDLE", "SLIDINGHORIZONTAL")],
        filename    = "SINGLE-SLIDER.ifc",
    ),
    dict(
        formx_type  = "SINGLE_PANEL_WINDOW",
        panel_type  = "WINDOW_PANEL_DOUBLE_HUNG",
        description = "Double-hung window — two sashes slide vertically",
        predef      = "WINDOW",
        part_type   = "DOUBLE_PANEL_HORIZONTAL",
        W           = NOM_W_SINGLE,
        H           = NOM_H_SINGLE,
        split       = "H",   # horizontal transom divides top/bottom sashes
        ifc_panels  = [("TOP", "SLIDINGVERTICAL"), ("BOTTOM", "SLIDINGVERTICAL")],
        filename    = "SINGLE-DOUBLEHUNG.ifc",
    ),

    # ── Compound types ───────────────────────────────────────────────────────
    dict(
        formx_type  = "DOUBLE_HORIZONTAL_WINDOW",
        panel_type  = "WINDOW_PANEL_FIXED",   # default; per-instance panel types vary
        description = "Double horizontal window — two panels side-by-side (vertical mullion)",
        predef      = "WINDOW",
        part_type   = "DOUBLE_PANEL_VERTICAL",
        W           = NOM_W_DBL_H,
        H           = NOM_H_DBL_H,
        split       = "V",   # vertical mullion
        ifc_panels  = [("LEFT", "FIXEDCASEMENT"), ("RIGHT", "FIXEDCASEMENT")],
        filename    = "DOUBLE-HORIZONTAL.ifc",
    ),
    dict(
        formx_type  = "DOUBLE_VERTICAL_WINDOW",
        panel_type  = "WINDOW_PANEL_FIXED",   # default; per-instance panel types vary
        description = "Double vertical window — two panels stacked (horizontal transom)",
        predef      = "WINDOW",
        part_type   = "DOUBLE_PANEL_HORIZONTAL",
        W           = NOM_W_DBL_V,
        H           = NOM_H_DBL_V,
        split       = "H",   # horizontal transom
        ifc_panels  = [("TOP", "FIXEDCASEMENT"), ("BOTTOM", "FIXEDCASEMENT")],
        filename    = "DOUBLE-VERTICAL.ifc",
    ),
]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Generating {len(GOLDEN_SPECS)} golden template IFCs → {OUT_DIR}/\n")
    for spec in GOLDEN_SPECS:
        build_golden(spec, OUT_DIR)
    print(f"\nDone. Open the .ifc files in your viewer to inspect geometry.")
