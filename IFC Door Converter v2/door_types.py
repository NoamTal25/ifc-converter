"""
door_types.py — the SINGLE source of truth for FormX's 16 door types.

Both ``generate_goldens.py`` (which writes the reviewable golden template IFCs) and
``classify_door.py`` (which maps a baked IfcDoor's Name to one of these types) import this table,
so the type catalog lives in exactly ONE editable place. To add / retune a door type, edit the
entry here and re-run ``generate_goldens.py`` — nothing is duplicated downstream.

Each entry carries:
  filename        : golden template file name
  description     : human label (Description on the golden IfcDoor / IfcDoorType)
  nom_w, nom_h    : nominal dimensions in mm (the converter replaces these per instance)
  recipe          : the GEOMETRY knobs consumed by golden_door_geometry.build_door_items
                    (panels / arrangement / glazed / handle / head_rail / barn_track)
  operation       : IfcDoorTypeOperationEnum value (or "USERDEFINED")
  user_op         : UserDefinedOperationType label when operation == "USERDEFINED", else None
  panel_positions : [(IfcDoorPanelPositionEnum, IfcDoorPanelOperationEnum), …] — one per panel
  folding         : True for bi-fold / folding-combo types — these export partly folded, so the
                    converter clamps the measured through-wall depth (the §6 folding-depth gotcha)
  is_external     : Pset_DoorCommon.IsExternal default

NOTE (viewer-review items recorded for the architectural review, not yet changed):
  * DOOR_BARN is modelled as TWO leaves on one track (vs DOOR_SINGLE_BARN = one leaf); confirm the
    1-leaf-vs-2-leaf interpretation of the PDF "BARN" / "BARN+SINGLE" split.
  * DOOR_POCKET's pull is proud of both faces; a real pocket pull is recessed.
  * bi-fold / combo panels are flat & coplanar (decision #3 — not articulated).
"""

# canonical nominal sizes (mm)
_W_SINGLE = 900.0
_W_DOUBLE = 1800.0
_W_BIFOLD = 2400.0   # 4 leaves
_W_BARN   = 1000.0
_H        = 2100.0


def _t(filename, description, recipe, operation, panel_positions, *,
       nom_w=_W_SINGLE, nom_h=_H, user_op=None, folding=False, is_external=False):
    return dict(filename=filename, description=description, nom_w=nom_w, nom_h=nom_h,
                recipe=recipe, operation=operation, user_op=user_op,
                panel_positions=panel_positions, folding=folding, is_external=is_external)


TYPES = {
    # ── single-leaf swing slabs ────────────────────────────────────────────────
    "DOOR_SINGLE": _t(
        "DOOR_SINGLE.ifc", "Single swing door — one hinged leaf",
        dict(panels=1, arrangement="side_by_side", glazed=False, handle="lever"),
        "SINGLE_SWING_LEFT", [("RIGHT", "SWINGING")], is_external=True),
    "DOOR_INTERIOR_SINGLE": _t(
        "DOOR_INTERIOR_SINGLE.ifc", "Interior single swing door — one hinged leaf",
        dict(panels=1, arrangement="side_by_side", glazed=False, handle="lever"),
        "SINGLE_SWING_LEFT", [("RIGHT", "SWINGING")]),
    "DOOR_SINGLE_FLUSH": _t(
        "DOOR_SINGLE_FLUSH.ifc", "Single flush door — one plain hinged leaf",
        dict(panels=1, arrangement="side_by_side", glazed=False, handle="lever"),
        "SINGLE_SWING_LEFT", [("RIGHT", "SWINGING")]),

    # ── double-leaf swing ──────────────────────────────────────────────────────
    "DOOR_DOUBLE": _t(
        "DOOR_DOUBLE.ifc", "Double exterior door — two framed hinged leaves",
        dict(panels=2, arrangement="side_by_side", glazed=False, handle="lever", leaf_frame=True),
        "DOUBLE_DOOR_SINGLE_SWING", [("LEFT", "SWINGING"), ("RIGHT", "SWINGING")],
        nom_w=_W_DOUBLE, is_external=True),
    "DOOR_INTERIOR_DOUBLE": _t(
        "DOOR_INTERIOR_DOUBLE.ifc", "Interior double door — two framed full-glass leaves",
        dict(panels=2, arrangement="side_by_side", glazed=True, handle="lever", leaf_frame=True),
        "DOUBLE_DOOR_SINGLE_SWING", [("LEFT", "SWINGING"), ("RIGHT", "SWINGING")],
        nom_w=_W_DOUBLE),

    # ── sliding / pocket ───────────────────────────────────────────────────────
    "DOOR_POCKET": _t(
        "DOOR_POCKET.ifc", "Pocket door — single leaf retracted into an in-wall pocket",
        dict(panels=1, arrangement="side_by_side", glazed=False, handle="pull", pocket=True),
        "SLIDING_TO_LEFT", [("RIGHT", "SLIDING")], nom_w=_W_DOUBLE),
    "DOOR_SLIDING": _t(
        "DOOR_SLIDING.ifc", "Sliding door — fixed panel + sliding panel (offset, partially open)",
        dict(panels=2, arrangement="side_by_side", glazed=True, handle="none", sliding=True),
        "SLIDING_TO_LEFT", [("LEFT", "SLIDING"), ("RIGHT", "SLIDING")],
        nom_w=_W_DOUBLE),

    # ── barn (exposed track) ───────────────────────────────────────────────────
    "DOOR_BARN": _t(
        "DOOR_BARN.ifc", "Barn door — two leaves on an exposed overhead track",
        dict(panels=2, arrangement="side_by_side", glazed=False, handle="pull", barn_track=True),
        "DOUBLE_DOOR_SLIDING", [("LEFT", "SLIDING"), ("RIGHT", "SLIDING")],
        nom_w=_W_DOUBLE, is_external=True),
    "DOOR_SINGLE_BARN": _t(
        "DOOR_SINGLE_BARN.ifc", "Single barn door — one leaf on an exposed overhead track",
        dict(panels=1, arrangement="side_by_side", glazed=False, handle="pull", barn_track=True),
        "SLIDING_TO_LEFT", [("RIGHT", "SLIDING")], nom_w=_W_BARN, is_external=True),

    # ── shower (glazed) ────────────────────────────────────────────────────────
    "DOOR_SHOWER": _t(
        "DOOR_SHOWER.ifc", "Shower door — single glazed leaf in a slim frame",
        dict(panels=1, arrangement="side_by_side", glazed=True, handle="pull"),
        "SINGLE_SWING_LEFT", [("RIGHT", "SWINGING")]),

    # ── bifolding ──────────────────────────────────────────────────────────────
    "DOOR_BIFOLDING_GLASS": _t(
        "DOOR_BIFOLDING_GLASS.ifc", "Bifolding glass door — four glazed leaves (flat, first pass)",
        dict(panels=4, arrangement="side_by_side", glazed=True, handle="pull"),
        "DOUBLE_DOOR_FOLDING",
        [("LEFT", "FOLDING"), ("MIDDLE", "FOLDING"), ("MIDDLE", "FOLDING"), ("RIGHT", "FOLDING")],
        nom_w=_W_BIFOLD, folding=True, is_external=True),
    "DOOR_INTERIOR_BIFOLDING_2_PANEL": _t(
        "DOOR_INTERIOR_BIFOLDING_2_PANEL.ifc",
        "Interior bifolding door — two folding leaves (flat, first pass)",
        dict(panels=2, arrangement="side_by_side", glazed=False, handle="pull"),
        "DOUBLE_DOOR_FOLDING", [("LEFT", "FOLDING"), ("RIGHT", "FOLDING")], folding=True),

    # ── combos (split leaf, first pass) ────────────────────────────────────────
    "DOOR_SLIDE_AND_SWING": _t(
        "DOOR_SLIDE_AND_SWING.ifc",
        "Slide-and-swing door — one sliding + one swing leaf (split, first pass)",
        dict(panels=2, arrangement="side_by_side", glazed=False, handle="lever"),
        "USERDEFINED", [("LEFT", "SLIDING"), ("RIGHT", "SWINGING")],
        nom_w=_W_DOUBLE, user_op="SLIDE_AND_SWING", is_external=True),
    "DOOR_SLIDING_SWING_COMBO": _t(
        "DOOR_SLIDING_SWING_COMBO.ifc",
        "Sliding-swing combo door — one sliding + one swing leaf (split, first pass)",
        dict(panels=2, arrangement="side_by_side", glazed=False, handle="lever"),
        "USERDEFINED", [("LEFT", "SLIDING"), ("RIGHT", "SWINGING")],
        nom_w=_W_DOUBLE, user_op="SLIDING_SWING_COMBO", is_external=True),
    "DOOR_BIFOLDING_SWING_COMBO": _t(
        "DOOR_BIFOLDING_SWING_COMBO.ifc",
        "Bifolding-swing combo — two folding + one swing leaf (flat, first pass)",
        dict(panels=3, arrangement="side_by_side", glazed=False, handle="pull"),
        "DOUBLE_DOOR_FOLDING",
        [("LEFT", "FOLDING"), ("MIDDLE", "FOLDING"), ("RIGHT", "SWINGING")],
        nom_w=_W_BIFOLD, folding=True, is_external=True),

    # ── leafless cased opening ─────────────────────────────────────────────────
    "DOOR_OPENING": _t(
        "DOOR_OPENING.ifc", "Cased opening — lining frame only, no leaf",
        dict(panels=0, arrangement="side_by_side", glazed=False, handle="none"),
        "NOTDEFINED", []),
}

# PDF listing order — generate_goldens iterates this so the catalog reads in spec order.
ORDER = [
    "DOOR_SINGLE", "DOOR_POCKET", "DOOR_DOUBLE", "DOOR_SHOWER", "DOOR_SLIDING",
    "DOOR_SLIDE_AND_SWING", "DOOR_BIFOLDING_GLASS", "DOOR_INTERIOR_BIFOLDING_2_PANEL",
    "DOOR_INTERIOR_SINGLE", "DOOR_SINGLE_FLUSH", "DOOR_SLIDING_SWING_COMBO",
    "DOOR_BIFOLDING_SWING_COMBO", "DOOR_INTERIOR_DOUBLE", "DOOR_OPENING",
    "DOOR_BARN", "DOOR_SINGLE_BARN",
]
