"""
classify_door.py — map a baked IfcDoor to a FormX door type + build recipe.

Pure and (almost) geometry-free: it reads the family/type **Name** (Revit names look like
``Door-Interior-Double-Full Glass-Wood:84" x 96":2205482``) and resolves it to one of the 16 FormX
door types in ``door_types.py``. The converter applies the *geometric* gates (fill-ratio,
unreadable / bodiless geometry) separately; here we handle the name-driven type mapping + the
glazed/folding modifiers.

Rules implement the PDF "IFC Standardizer: Template Gallery categorizing" DOORS section, in
priority order (more specific first), tuned to the door names actually present in the ADUs:
    OPENING · POCKET · BARN(±SINGLE) · BIFOLD(_DOOR_2 / four_fold / generic) ·
    SLIDING+PLY+GEM (slide-swing combo) · SINGLE+FLUSH · INTERIOR+DOUBLE · INTERIOR+SINGLE ·
    DOUBLE+EXTERIOR · SHOWER · SLIDE+SWING · SLIDING/SLIDER · DOUBLE · → default SINGLE.

DEFERRED (PDF, needs geometry): "two doors side-by-side sharing one opening's bbox →
DOOR_BIFOLDING_SWING_COMBO" is an adjacency rule the name alone can't decide — left for a future
adjacency pass (no such pair occurs in the grounding ADUs).

Returns a recipe dict consumed by the converter:
  { gate, reason, formx_type, golden, recipe(geometry knobs), operation, user_op,
    panel_props:[(PanelPosition, PanelOperation)], folding, glazed, name }
"""
import door_types


def _norm(s):
    return (s or "").lower().replace("-", " ").replace("_", " ")


# Glazing descriptors that contain the words 'single'/'double' but mean GLAZING, not leaf count
# (e.g. "double glazed", "single pane"). Scrubbed before the leaf-count rules so they don't flip
# a door's panel count. (A real "double door … full glass" keeps its 'double' — only the glazing
# phrase is removed.)
_GLAZE_DESC = ("single glaz", "double glaz", "triple glaz",
               "single pane", "double pane", "triple pane",
               "single lite", "double lite", "single glazing", "double glazing")

# Keywords that imply an actual leaf — if present, a stray "opening" in the name is NOT a cased
# opening (e.g. "Sliding_Glass_Opening_Wall" is a sliding door, not DOOR_OPENING).
_LEAF_KW = ("swing", "flush", "sliding", "slider", "pocket", "barn", "bifold", "bi fold",
            "fold", "french", "panel", "hinged", "slide", "patio", "bypass")


def _name_blob(door, dt):
    parts = [getattr(door, "Name", None), getattr(door, "ObjectType", None),
             getattr(dt, "Name", None) if dt else None]
    return _norm(" ".join(p for p in parts if p))


def _trailing_id(name):
    """Revit names end in ':<elementid>' — keep it as a stable handle (matches v1 / windows)."""
    if name and ":" in name:
        tail = name.rsplit(":", 1)[-1].strip()
        if tail:
            return tail
    return None


def _pick_type(blob):
    """Return (formx_type, reason) for the (glazing-scrubbed) normalized name blob, in PDF priority
    order."""
    has = lambda *kw: all(k in blob for k in kw)
    any_ = lambda *kw: any(k in blob for k in kw)

    # DOOR_OPENING only when there is no leaf-implying word — a stray "opening" in a leafed door's
    # name (e.g. "Self-Opening-Automatic-Single", "Sliding_Glass_Opening") must not collapse it to a
    # leafless cased opening. "single"/"double" imply a leaf count, so they bar OPENING too.
    if "opening" in blob and not any_(*_LEAF_KW, "single", "double"):
        return "DOOR_OPENING", "name: opening"
    if "pocket" in blob:                               return "DOOR_POCKET", "name: pocket"
    if "barn" in blob:
        return ("DOOR_SINGLE_BARN", "name: barn+single") if "single" in blob \
               else ("DOOR_BARN", "name: barn")
    # bi-fold family (PDF 'BIFOLD_DOOR_2' + tuned synonyms incl. the real 'four_fold')
    if any_("bifold", "bi fold", "bifolding", "four fold", "fourfold") or ("fold" in blob):
        if any_("four fold", "fourfold") or "4 panel" in blob or "4 fold" in blob:
            return "DOOR_BIFOLDING_GLASS", "name: four-fold (4-panel bifold)"
        return "DOOR_INTERIOR_BIFOLDING_2_PANEL", "name: bifold (2-panel)"
    if has("sliding", "ply", "gem"):                   return "DOOR_SLIDING_SWING_COMBO", "name: sliding+ply+gem"
    if has("single", "flush"):                         return "DOOR_SINGLE_FLUSH", "name: single+flush"
    if has("interior", "double"):                      return "DOOR_INTERIOR_DOUBLE", "name: interior+double"
    if has("interior", "single"):                      return "DOOR_INTERIOR_SINGLE", "name: interior+single"
    if has("double", "exterior"):                      return "DOOR_DOUBLE", "name: double+exterior"
    if "shower" in blob:                               return "DOOR_SHOWER", "name: shower"
    if has("slide", "swing") or has("sliding", "swing"):
        return "DOOR_SLIDE_AND_SWING", "name: slide+swing"
    if any_("sliding", "slider", "bypass", "patio"):   return "DOOR_SLIDING", "name: sliding"
    if "double" in blob and "single" not in blob:      return "DOOR_DOUBLE", "name: double"
    return "DOOR_SINGLE", "default: single"


def classify(door, dt=None):
    """Return the build recipe for one door (see module docstring)."""
    blob = _name_blob(door, dt)
    # glazed modifier read from the RAW blob; then scrub glazing descriptors so "double glazed" /
    # "single pane" don't drive the leaf-count rules in _pick_type.
    glazed_name = ("glass" in blob) or ("glazed" in blob) or ("glazing" in blob)
    type_blob = blob
    for g in _GLAZE_DESC:
        type_blob = type_blob.replace(g, " ")
    formx_type, reason = _pick_type(type_blob)
    td = door_types.TYPES[formx_type]

    # glazed = the type's intrinsic default OR an explicit glazing keyword in the name.
    glazed = bool(td["recipe"].get("glazed")) or glazed_name
    recipe = dict(td["recipe"])            # copy so we can override glazed per instance
    recipe["glazed"] = glazed

    eid = _trailing_id(getattr(door, "Name", None))
    name = f"{formx_type}:{eid}" if eid else formx_type

    return dict(
        gate=False, reason=reason,
        formx_type=formx_type, golden=td["filename"],
        recipe=recipe, operation=td["operation"], user_op=td["user_op"],
        panel_props=td["panel_positions"], folding=bool(td["folding"]),
        glazed=glazed, name=name,
    )
