"""
classify_window.py — map a baked IfcWindow to a FormX window type + build recipe.

Pure and geometry-free: it reads only the family/type **Name** (Revit names look like
``Window-Casement-Double:60" x 60":1624599``). The converter applies the *geometric* gates
(fill-ratio < 0.95, unreadable / GeometricSet geometry) separately; here we only handle the
name-driven gates (trapezoid-by-name, skylight) and the type/panel mapping.

FormX window taxonomy (PDF "IFC Standardizer: Template Gallery categorizing"):
  SINGLE_PANEL_WINDOW × {FIXED, CASEMENT, AWNING, SLIDER, DOUBLE_HUNG}
  DOUBLE_HORIZONTAL_WINDOW   (two panels side-by-side, vertical mullion)
  DOUBLE_VERTICAL_WINDOW     (two panels stacked, horizontal transom)
  TRAPEZOID_WINDOW           (gated — no parametric template yet)

Returns a recipe dict consumed by the converter:
  { gate, reason, formx_type, predef, split, part_type, golden,
    panels:[(PanelPosition, IfcWindowPanelOperationEnum)], pset_panel:{...}, name }
"""

# FormX panel label → IfcWindowPanelOperationEnum (schema-stable in IFC2X3/IFC4/IFC4X3)
PANEL_OP = {
    "WINDOW_PANEL_FIXED":       "FIXEDCASEMENT",
    "WINDOW_PANEL_CASEMENT":    "SIDEHUNGRIGHTHAND",
    "WINDOW_PANEL_AWNING":      "TOPHUNG",
    "WINDOW_PANEL_SLIDER":      "SLIDINGHORIZONTAL",
    "WINDOW_PANEL_DOUBLE_HUNG": "SLIDINGVERTICAL",
}

# operation keyword found in the name → FormX panel label
_OP_KEYWORD = [
    ("casement", "WINDOW_PANEL_CASEMENT"),
    ("awning",   "WINDOW_PANEL_AWNING"),
    ("slider",   "WINDOW_PANEL_SLIDER"),
    ("sliding",  "WINDOW_PANEL_SLIDER"),
    ("fixed",    "WINDOW_PANEL_FIXED"),
]


def _name_blob(win, wt):
    return " ".join(s for s in (getattr(win, "Name", None),
                                getattr(wt, "Name", None)) if s).lower()


def _trailing_id(name):
    """Revit names end in ':<elementid>' — keep it as a stable handle (matches v1)."""
    if name and ":" in name:
        tail = name.rsplit(":", 1)[-1].strip()
        if tail:
            return tail
    return None


def _panel_from_keyword(blob):
    for kw, label in _OP_KEYWORD:
        if kw in blob:
            return label
    return "WINDOW_PANEL_FIXED"      # confirmed default when no operation keyword matches


def _named(formx_type, win):
    eid = _trailing_id(getattr(win, "Name", None))
    return f"{formx_type}:{eid}" if eid else formx_type


def classify(win, wt=None):
    """Return the build recipe for one window (see module docstring)."""
    blob = _name_blob(win, wt)

    # 1) name-driven gates ----------------------------------------------------------
    if "trapezoid" in blob:
        return dict(gate=True, reason="trapezoid (no parametric template yet)")
    if "skylight" in blob:
        return dict(gate=True, reason="skylight (not a FormX window type)")

    # 2) any "…hung…" → SINGLE_PANEL_WINDOW with a DOUBLE_HUNG panel (two stacked sashes).
    #    FormX has only one hung panel type (DOUBLE_HUNG), so single/double-hung both map here.
    #    Checked before the generic 'double' rule so a double-hung isn't read as a 2-leaf window.
    if "hung" in blob:
        return dict(gate=False, reason="double-hung (single panel, stacked sashes)",
                    formx_type="SINGLE_PANEL_WINDOW", predef="WINDOW",
                    split="H", part_type="DOUBLE_PANEL_HORIZONTAL",
                    golden="SINGLE-DOUBLEHUNG.ifc",
                    panels=[("TOP", PANEL_OP["WINDOW_PANEL_DOUBLE_HUNG"]),
                            ("BOTTOM", PANEL_OP["WINDOW_PANEL_DOUBLE_HUNG"])],
                    pset_panel={"PanelType": "WINDOW_PANEL_DOUBLE_HUNG"},
                    name=_named("SINGLE_PANEL_WINDOW", win))

    # 3) compound DOUBLE windows (single IfcWindow named '…-Double') -----------------
    if "double" in blob and "single" not in blob:
        panel = _panel_from_keyword(blob)
        op = PANEL_OP[panel]
        if "vertical" in blob or "stacked" in blob:        # stacked: horizontal transom
            return dict(gate=False, reason=f"double-vertical ({panel})",
                        formx_type="DOUBLE_VERTICAL_WINDOW", predef="WINDOW",
                        split="H", part_type="DOUBLE_PANEL_HORIZONTAL",
                        golden="DOUBLE-VERTICAL.ifc",
                        panels=[("TOP", op), ("BOTTOM", op)],
                        pset_panel={"PanelTypeTop": panel, "PanelTypeBottom": panel},
                        name=_named("DOUBLE_VERTICAL_WINDOW", win))
        return dict(gate=False, reason=f"double-horizontal ({panel})",   # side-by-side: vertical mullion
                    formx_type="DOUBLE_HORIZONTAL_WINDOW", predef="WINDOW",
                    split="V", part_type="DOUBLE_PANEL_VERTICAL",
                    golden="DOUBLE-HORIZONTAL.ifc",
                    panels=[("LEFT", op), ("RIGHT", op)],
                    pset_panel={"PanelTypeLeft": panel, "PanelTypeRight": panel},
                    name=_named("DOUBLE_HORIZONTAL_WINDOW", win))

    # 4) single panel — operation from keyword, else FIXED ---------------------------
    panel = _panel_from_keyword(blob)
    golden = {
        "WINDOW_PANEL_FIXED":    "SINGLE-FIXED.ifc",
        "WINDOW_PANEL_CASEMENT": "SINGLE-CASEMENT.ifc",
        "WINDOW_PANEL_AWNING":   "SINGLE-AWNING.ifc",
        "WINDOW_PANEL_SLIDER":   "SINGLE-SLIDER.ifc",
    }[panel]
    return dict(gate=False, reason=f"single ({panel})",
                formx_type="SINGLE_PANEL_WINDOW", predef="WINDOW",
                split=None, part_type="SINGLE_PANEL", golden=golden,
                panels=[("MIDDLE", PANEL_OP[panel])],
                pset_panel={"PanelType": panel},
                name=_named("SINGLE_PANEL_WINDOW", win))
