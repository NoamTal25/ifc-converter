"""
accessory_types.py — the SINGLE source of truth for the FormX accessory vocabulary + the
allow-list / gate / classification keyword maps.

This is the ``door_types.py`` analog for the accessories converter, but much lighter: there is NO
geometry to author (the converter is PRESERVE-AND-TAG — it keeps the baked mesh verbatim and only
stamps a ``FormX_Accessory`` property set). So an entry carries only a human ``description`` + an
``is_external`` default; there are no geometry knobs.

Both ``classify_accessory.py`` and the tester import this table, so the catalog + the keyword rules
live in exactly ONE editable place ("come back and update accessories later" = edit here).

Sections:
  * ``TYPES``        — the ~10 fine-grained accessory types + a GENERIC fallback.
  * ``ROOTS``        — the IFC root classes to scan. ``by_type(root)`` already returns subtypes
                       (``IfcFurniture`` ⊂ ``IfcFurnishingElement``; ``IfcLightFixture`` /
                       ``IfcSanitaryTerminal`` ⊂ ``IfcFlowTerminal``), so the converter dedups by
                       ``.id()``. Some subtypes are absent in IFC2X3 → the scan is try/except.
  * ``GATE_KEYWORDS`` — structural trim (fascia/soffit/…) that Revit sometimes exports as a proxy
                       with a real Body but which is NOT a movable accessory → gated.
  * ``SOLID_REP_TYPES`` — Body representation types that count as a real 3D object (anything else /
                       2D-only, e.g. ``Annotation2D`` text, is gated as bodiless).
  * keyword rule lists — CLASS-prior is applied in the classifier; these refine WITHIN a class.
"""


# ── accessory type vocabulary ───────────────────────────────────────────────────────
def _t(description, is_external=False):
    return dict(description=description, is_external=is_external)


TYPES = {
    "PLANT":             _t("Plant / planter (indoor or outdoor greenery)"),
    "SEATING":           _t("Seating — chair, sofa, stool, bench, ottoman"),
    "TABLE":             _t("Table / desk / counter / work surface"),
    "STORAGE":           _t("Cabinet / casework / shelving / wardrobe / vanity"),
    "BED":               _t("Bed / mattress"),
    "APPLIANCE":         _t("Appliance — range, fridge, washer/dryer, range hood, TV"),
    "SANITARY_FIXTURE":  _t("Plumbing fixture — toilet, sink, lavatory, shower"),
    "LIGHTING":          _t("Light fixture — wall light, lamp, sconce, pendant"),
    "DECOR":             _t("Decor — mirror, picture, art, niche, clock"),
    "OUTDOOR_FURNITURE": _t("Outdoor furniture", is_external=True),
    "GENERIC":           _t("Generic movable accessory (unclassified)"),
}

# Iteration order for reports / docs.
ORDER = ["PLANT", "SEATING", "TABLE", "STORAGE", "BED", "APPLIANCE", "SANITARY_FIXTURE",
         "LIGHTING", "DECOR", "OUTDOOR_FURNITURE", "GENERIC"]


# ── allow-list roots (by_type pulls subtypes; dedup by .id() in the scanner) ──────────
ROOTS = ["IfcFurnishingElement",      # + IfcFurniture (IFC4/4X3)
         "IfcBuildingElementProxy",
         "IfcFlowTerminal"]           # + IfcLightFixture / IfcSanitaryTerminal (IFC4+)

# leaf classes treated as furniture for the class-prior branch.
FURNITURE_CLASSES = ("IfcFurnishingElement", "IfcFurniture", "IfcSystemFurnitureElement")


# ── gates ────────────────────────────────────────────────────────────────────────────
# Structural trim Revit sometimes exports as IfcBuildingElementProxy with a real Body — NOT a
# movable accessory. Gated (left untouched) regardless of having geometry.
GATE_KEYWORDS = ("fascia", "soffit", "trim", "gutter", "flashing", "molding", "moulding",
                 "cornice", "coping", "baseboard", "skirting", "fillet strip")

# Body RepresentationType values that count as a real 3D solid. Anything else (or no Body shaperep
# at all — e.g. the "Text:…MS GOTHIC" annotation proxies, RepresentationType=Annotation2D) → gated.
SOLID_REP_TYPES = ("SweptSolid", "Brep", "AdvancedBrep", "CSG", "SolidModel", "AdvancedSweptSolid",
                   "MappedRepresentation", "Tessellation", "SurfaceModel", "Clipping",
                   "BooleanResult", "GeometricSet")


# ── classification keyword maps (refine WITHIN a class; FIRST match wins) ──────────────
# Appliance keywords — also used to split a bare IfcFlowTerminal into APPLIANCE vs SANITARY_FIXTURE
# (e.g. "Stacked Washer and Dryer" is a flow terminal but an appliance).
APPLIANCE_KW = ("refrigerator", "fridge", "range hood", "range", "oven", "cooktop", "stove",
                "dishwasher", "washer", "dryer", "microwave", "hood", "appliance")

LIGHTING_KW = ("wall light", "light", "lamp", "sconce", "luminaire", "chandelier", "pendant")

# Furniture sub-classification (IfcFurnishingElement / IfcFurniture). ORDER IS LOAD-BEARING:
# BED must come AFTER seating + table, else "Bedside Table" → BED (want TABLE) and the SOLLERÖN
# "daybed" sofa → BED (want SEATING). Only the genuine "Murphy bed" then lands on BED.
FURNITURE_RULES = [
    ("OUTDOOR_FURNITURE", ("outdoor",)),
    ("SEATING",   ("sofa", "couch", "chair", "seat", "stool", "bench", "ottoman", "recliner")),
    ("TABLE",     ("counter top", "countertop", "worktop", "table", "desk", "nightstand", "bedside")),
    ("BED",       ("murphy", "headboard", "mattress", "bed")),
    ("STORAGE",   ("cabinet", "casework", "wardrobe", "bookcase", "shelf", "dresser", "drawer",
                   "closet", "cupboard", "storage", "vanity")),
    ("APPLIANCE", APPLIANCE_KW),
    ("DECOR",     ("flat screen", "tv", "television", "mirror", "picture", "frame", "art", "rug",
                   "vase", "clock", "lamp", "decor")),
]

# Proxy sub-classification (IfcBuildingElementProxy) — the heterogeneous mixed bag, full name-driven.
PROXY_RULES = [
    ("PLANT",             ("plant",)),
    ("OUTDOOR_FURNITURE", ("outdoor",)),
    ("APPLIANCE",         APPLIANCE_KW),
    ("SANITARY_FIXTURE",  ("toilet", "wc", "sink", "lavatory", "basin", "shower", "faucet",
                           "tap", "bathtub", "tub", "bidet", "urinal")),
    ("LIGHTING",          LIGHTING_KW),
    ("DECOR",             ("mirror", "picture", "frame", "niche", "nich", "art", "decor", "clock",
                           "vase", "flat screen", "tv", "television")),
    ("SEATING",           ("sofa", "couch", "chair", "seat", "stool", "bench")),
    ("TABLE",             ("table", "desk", "counter")),
    ("STORAGE",           ("cabinet", "casework", "wardrobe", "bookcase", "shelf", "storage")),
    ("BED",               ("mattress", "bed")),
]
