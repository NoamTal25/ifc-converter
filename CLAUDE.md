# FormX Window IFC Converter — Project Context

**Status:** v1 converter built and passing. `IFC Window Converter/IFC_window_converter_V1.py` rebuilds each `IfcWindow` into clean parametric geometry; validated on all three real FormX ADUs (IFC2X3/IFC4/IFC4X3). See Section 4 + the converter's own `algorithm.md`.
**Scope:** Windows only (first element type in a planned multi-element converter — doors etc. follow later, out of scope here).
**Environment:** ifcopenshell 0.8.5 installed under `python3.11` (run the converter with `python3.11`). The plain `python3` (3.11 framework build) does NOT have ifcopenshell — there was no pre-existing ifcopenshell env on this machine despite Gal's `.pyc` being cpython-310.

---

## 0. Update protocol — READ THIS FIRST

This file is the persistent memory for this project across Claude Code sessions and other Claude agents. There is no other source of truth for *why* things are built the way they are — the reasoning lives here, not just in code comments.

**Any Claude session working in this repo must:**
- Update the relevant section below **immediately** after writing code, changing architecture, or discovering something non-obvious — not at the end of a long session, not "later."
- Add new discoveries to **Section 6 (Findings & gotchas)** as they happen, with enough detail that a fresh session doesn't have to re-derive them by re-reading files or re-running scripts.
- Update **Section 4 (Current state of artifacts)** the moment a file is added, renamed, or restructured. Stale paths here are worse than no paths.
- Log reversed or superseded decisions in **Section 8 (Decisions log)** rather than silently deleting the old reasoning — future sessions need to know a thing was tried and why it was abandoned.
- Keep this file scannable top-to-bottom in a few minutes. Deep detail belongs in code docstrings or referenced files; this file is the map, not the territory.
- If you are an agent and you are uncertain whether a change is "meaningful enough" to log — log it. Under-updating this file is the failure mode that costs the most downstream time.

---

## 1. What FormX is, and why this problem exists

FormX builds AI-driven modular homes: automation, modular construction, and a chat-driven design tool ("make this window wider") layered on top.

That chat-driven manipulation only works if the underlying data is **semantic + parametric + relational** — the system needs to know an object *is* a window, that its width is a *live parameter*, and how it relates to its host wall. IFC (Industry Foundation Classes) is FormX's chosen format because it's open, vendor-neutral, semantically rich, and programmatically read/writable (via IfcOpenShell) — no other format hits all of those at once.

**The problem:** designs are authored in Revit, which is parametric internally, but Revit's IFC *export* commonly bakes that parametric data into frozen geometry — the recipe is lost, only the resulting shape (and sometimes scattered metadata) survives. "Make it wider" has nothing to grab onto in a baked file. This converter exists to undo that, for windows first.

No off-the-shelf tool solves this (verified by searching — see Section 6). It's bespoke because the target isn't generic "rich IFC," it's *FormX-standardized* parametric IFC.

---

## 2. The core mental model

Two axes matter and must not be conflated:

- **Window *style*** — what kind of window it is (operation × panel configuration), e.g. "double casement." This is identity.
- **IFC *bakedness*** — how much parametric intent a given *file's representation* of a window retains, independent of style. A casement window can be baked or parametric; bakedness is a property of the file, not the style.

**Two distinct difficulty modes**, depending on what survives in a given input file:
- **Promote from surviving Psets** (tractable) — geometry is baked, but parameter values (dimensions, sometimes U-values, sometimes the style hint in a Name string) survived in property sets or the type's Name. The converter's job is to read these and re-author clean parametric geometry from them.
- **Reconstruct from baked geometry** (hard inference) — nothing useful survived; would require inferring parameters by measuring a triangle mesh. Avoid this path; see Section 7.

**Open architectural question, unresolved:** does FormX receive only post-export IFC, or does it (or could it) access source `.rvt` files directly? If `.rvt` access exists, extracting parameters directly (e.g. via Revit API, or non-Revit `.rvt` readers like Speckle's importer) and authoring IFC from that rich source bypasses the baking problem entirely — a fundamentally easier problem than reconstructing from already-baked IFC. **This has not been confirmed with the CTO and should be treated as a blocking question for scoping, not a settled assumption.**

---

## 3. Project goal: this converter, specifically

Build a Python script (IfcOpenShell-based) that:

1. Takes a real-world, possibly-baked IFC file containing windows (Revit/ArchiCAD/etc. export).
2. For each `IfcWindow`, classifies it into a canonical `style_code` (Section 5's registry).
3. Looks up the matching golden-target template for that style.
4. Re-authors a parametric window using the golden target's structure, with the *input's real surviving values* (dimensions, any Psets that survived) substituted in.
5. Outputs an IFC file where windows conform to FormX's parametric standard, validated against the same closed-loop checks used for the golden targets (Section 4).

This is **not** mesh-reconstruction. The strategy is promotion onto known templates, justified empirically in Section 6.

---

## 4. Current state of artifacts (what already exists)

Built and validated in the research phase + the v1 converter build. **Exact paths (these folders contain spaces — the converter imports the .py modules by inserting their dirs on `sys.path`):**

| File | What it is | Status |
|---|---|---|
| `Form X 6.22 IFC Survey/style_registry.csv` | Canonical style taxonomy: 13 rows (12 concrete + 1 abstract). `style_code` is the FK every other artifact keys off. Columns include target IFC enums (`ifc_predefined_type`, `ifc_partitioning_type`, `ifc_panel_operation`). | Stable, `status=seed` — extensible, not exhaustive |
| `Form X 6.22 IFC Survey/window_file_survey.csv` | Survey rows from real downloaded IFC files. Each row: `style_code` classification (confidence + basis + evidence), bakedness score/class, provenance. | Done; see Section 6 |
| `FormX 6.22 IFC Generated/*.ifc` (12 `WIN-*.ifc`) + `golden_targets.csv` | 12 authored, parametric IFC4 window files — one per concrete registry style. **Phase-2 / deferred output spec, NOT used by v1** (see Section 7). | Validated (see below) |
| `Form X 6.22 IFC Survey/bakedness.py` | Parametric Integrity Score (PIS, 0–100) scorer. Classes: `PARAMETRIC` (≥78) / `SEMI_PARAMETRIC` (50–77) / `BAKED_WITH_METADATA` (20–49) / `FULLY_BAKED` (<20) / `STUB_NO_GEOMETRY`. `score_window(win, wtype)`. | Stable; **reused by converter** as the report/gate |
| `Form X 6.22 IFC Survey/classify.py` | Style classifier: panel-operation enums first (high conf), family-name regex fallback (medium), else low/`UNCLASSIFIED`. English + German. `classify(win, wt)`. | **Reused by converter for Name + skylight only** (its weakness is cosmetic there) — see Section 6 |
| `FormX 6.22 IFC Generated/author_goldens.py` | IfcOpenShell engine that builds the golden targets. `SPECS` (13 entries incl. abstract `WIN-HUNG-DBL_H`, which has NO `.ifc`), `build(spec, path)`. | Stable; **not imported by v1** (v1 authors its own arbitrary-axis geometry) |
| **`IFC Window Converter/IFC_window_converter_V1.py`** | **The v1 converter.** Rebuilds each `IfcWindow` into a clean parametric hollow-frame + pane sized from its own measured geometry; preserves GlobalId/placement/relationships; sets canonical Name + `PredefinedType`. Batch `INPUT_IFC_FILES_HERE/` → `OUTPUT_IFC_FILES_HERE/` with `-WIN1` suffix; also single-file args. Run with `python3.11`. | **Built, all 3 ADUs pass `RESULT: ALL CHECKS PASSED`** |
| **`IFC Window Converter/IFC window converter algorithm.md`** | Living spec for the converter (Gal's doc structure). | Current |
| **`IFC Window Converter/test_window_converter.py`** + **`WINDOW_CONVERTER_TESTING_AGENT.md`** | **Automated manipulability tester** (replaces the manual Blender loop). Runs the converter on every fixture in throwaway temps, re-derives invariants independently (does NOT call the converter's `verify()`), and **actually manipulates each rebuilt window** (parametric width/height resize, move, rotate) — asserting frame border stays constant, window moves rigidly, validates clean. Analytic/kernel-free geometry (the geom tessellation kernel returns nondeterministic empty meshes on freshly-authored solids). Negative-control/teeth: same test on the baked originals MUST fail + pinned `BASELINE_REBUILT` counts (a no-op trips it). Run `python3.11 test_window_converter.py`. | **All 6 fixtures pass (730 checks); teeth verified** |

**Test inputs:** real FormX ADUs live in `INPUT_IFC_FILES_HERE/` — both the converter's batch input and the tester's fixture corpus (currently `LEXFORD_OFFICE-C1`, `SAN_JUAN_CYPRESS…-W1-L1`, `Sunflower_A`, `Turnberry…-C1`). The earlier `FormX Designs IFC/` reference folder (HUDSON / LEXFORD / Thomas) was **removed as redundant** — its validation history still stands (see below), and LEXFORD survives in INPUT. `Gal_Similar_Project_Refrences/` (walls cleanup / levels organizer / floors definer) is the **design template** for the converter (CLI shape, built-in `verify()`, algorithm.md structure, "only-touch-your-element" discipline).

**Golden target validation status (multi-source, not just self-checks):**
- All 12 score `PIS=100 / PARAMETRIC` on `bakedness.py` (self-consistency — see caveat in Section 6).
- All 12 pass `ifcopenshell.validate` (real IFC4 schema/WHERE-rule validation — independent of our own scoring code).
- All 12 round-trip through `classify.py` to their own correct `style_code`.
- All 12 confirmed by a human to open and function correctly in OpenIFCViewer (independent, real-world tool check).
- **Known gap:** golden targets use plain `IfcWindow` + a `'Body'` SweptSolid representation, not the spec-preferred `IfcWindowStandardCase` + `'Profile'` representation for windows with applied shape parameters. Judged non-critical: all parametric data lives on `IfcWindowType` either way and is fully readable regardless of which wrapper entity holds the geometry; most real-world exporters also use plain `IfcWindow`, so this arguably makes the targets *more* representative of real output, not less. Only matters for strict IFC4 *Reference View* certification. Revisit if that becomes a requirement.

---

## 5. The style registry (summary — full detail in style_registry.csv)

`style_code` grammar: `WIN-<OPERATION>-<PANELCONFIG>`. Handing (`-L`/`-R`) and shape (`ROUND`/`ARCH`) are optional refinement columns, never baked into the code.

12 concrete styles: `WIN-FIXED-SINGLE`, `WIN-CASEMENT-SINGLE`, `WIN-CASEMENT-DBL_V`, `WIN-AWNING-SINGLE`, `WIN-HOPPER-SINGLE`, `WIN-SINGLEHUNG-DBL_H`, `WIN-DOUBLEHUNG-DBL_H`, `WIN-SLIDER-DBL_V`, `WIN-TILTTURN-SINGLE`, `WIN-PIVOTH-SINGLE`, `WIN-PIVOTV-SINGLE`, `WIN-SKYLIGHT-SINGLE`.

1 abstract style: `WIN-HUNG-DBL_H` — discovered during authoring that "generic hung" can't be built without committing to single- vs double-hung (one vs two operable sashes). Exists only as a classifier fallback parent, never a golden target.

Cross-checked against the real-world NAFS/AAMA fenestration standard (the North American Fenestration Standard, AAMA/WDMA/CSA 101/I.S.2/A440) — our operation axis converges well with NAFS's official operator-type codes. Known gap vs. NAFS: we don't yet have **jalousie/louvre** as a style. Candidate addition, not yet done.

---

## 6. Findings & gotchas (read before writing converter logic)

These are empirical results from the survey, not assumptions — re-derive from `window_file_survey.csv` if in doubt.

**— Findings from the v1 converter build (2026-06-22): —**

- **Gal's functioning tools define the real FormX recipe, and it's leaner than the golden spec.** Grepping what each of Gal's three tools *authors* (not reads): walls cleanup (1512 lines, in production) creates **zero `Pset_*` and zero `IfcWallType`**; it rebuilds baked/Brep wall bodies into clean swept extrusions, renames by class, preserves openings. Levels organizer authors nothing but placements; floors definer only sets standard `PredefinedType` + cleans existing types. **None author bespoke property sets or operation-enum type apparatus.** The consistent recipe is: *clean regenerable geometry + canonical Name + standard `PredefinedType` + intact relationships, and nothing more.* v1 follows exactly this. The golden targets' richer apparatus (IfcWindowType + lining/panel/operation enums + Pset_WindowCommon) is **more than the working system is shown to need, and was authored by Claude, not FormX** — so it's deferred (Section 7), not built into v1.

- **Real FormX ADU window geometry** (observed across the real FormX ADUs; those reference files have since moved/been removed, but the findings stand — current corpus is `INPUT_IFC_FILES_HERE/`). Schema varies per file (saw **IFC2X3, IFC4, IFC4X3** across the ADUs); units are **feet** (`unit_scale 0.3048`), not mm. Each window's body is a **mapped representation**: `IfcWindow.Representation → IfcShapeRepresentation(MappedRepresentation) → IfcMappedItem → IfcRepresentationMap → real geometry`. The map is often **shared across multiple windows** (so never edit it in place). Underlying geometry is a mix of `IfcExtrudedAreaSolid` bundles (PIS 72–77), `IfcFacetedBrep`, and `IfcAdvancedBrep` (PIS 37–42). The mapping transform was identity on these files (geometry sits directly under `ObjectPlacement`).

- **The local-frame wrinkle is solved empirically, not analytically.** A window's width/height/depth map onto whatever local axes its placement + exporter chose (some ADU windows are rotated 90°/180°; skylights lie flat with depth along Z). **Don't reverse-engineer the convention** — measure the window's local-frame bbox via the IfcOpenShell kernel (`use-world-coords=False`), take the **thinnest axis as depth**, and author a symmetric frame+pane filling that exact box. Measured extents matched `OverallWidth/OverallHeight` exactly. This makes the rebuild orientation-agnostic and keeps the window in its opening (verify world-bbox drift was 0 mm on swept windows, ≤7 mm on Brep bevels). **Kernel gotcha:** `ifcopenshell.geom` returns vertices in **metres regardless of file units** — divide by `calculate_unit_scale` to get file units.

- **Removing a mapped representation safely.** When swapping a window's body, the old per-window `IfcShapeRepresentation` is often also referenced by an `IfcPresentationLayerAssignment`. Removing just the `IfcMappedItem` leaves the shaperep with empty `Items` → a schema error. Fix: **de-reference the shaperep from any layer assignment first**, then remove items + shaperep + product-def-shape; **never remove the shared `IfcRepresentationMap`**. (This bit during the build — see `_cleanup_old_rep`.)

- **`classify.py`'s weakness is neutralized in v1.** Because v1 uses `style_code` only for the Name label + skylight detection (not geometry), a wrong style guess is cosmetic. One real miss in the test data: "Skylight-Top-Hung" classified as `WIN-HUNG-DBL_H` (matched "hung"), so the converter adds a **direct `"skylight"` name override** to force `PredefinedType=SKYLIGHT` (which IS semantic). Classifier returns `confidence=medium` from family names on all ADU windows.

- **`validate` gate must compare to source, not require zero.** Real source files carry pre-existing `ifcopenshell.validate` errors (HUDSON: 2 "Attribute not optional"). The converter's gate is *output errors ≤ source errors* (introduce none), not *== 0*.

- **Non-rectangular windows must NOT be rebuilt — the rectangular template flattens them (v1.1 fix).** The neutral frame+pane is authored from the window's *bounding box*, so a trapezoid/triangle/gable/arch would become a rectangle (caught in Blender: LEXFORD's `…Direct_Glaze_Trapezoid…` window, fill ratio 0.60, was flattened). Fix: a **face-fill gate** — convex-hull area of the face silhouette ÷ bounding-rect area; below `FILL_MIN` (0.95) the window is non-rectangular, so **leave its original geometry untouched and flag `[keep]`** (correct-but-original > clean-but-wrong). All FormX ADU rectangles score 1.00; only the trapezoid trips it. Faithful clean rebuild of odd shapes (extrude the real silhouette) is Phase-2. *Reminder for the future parametric layer: object-level Blender scaling is NOT parametric — it rigidly stretches the mesh and doesn't touch OverallWidth.*

- **Rebuilding a window's representation drops its `IfcStyledItem` styles → windows render solid gray (v1.1 fix).** Glass transparency + frame colour live on `IfcStyledItem → IfcSurfaceStyle` attached to each *representation item*, not on the window element. Fresh items have none, so Blender/Bonsai (and other viewers) default to gray — caught in Blender testing. Fix: **harvest the original window's styles (bucket by `IfcSurfaceStyleRendering.Transparency`: most-transparent = glass, opaque = frame) and re-attach to the new pane/frame**, reusing the existing `IfcSurfaceStyle` entities verbatim (identical look; schema-correct since IFC2X3 wraps in `IfcPresentationStyleAssignment` while IFC4/4X3 is direct). Real FormX windows carried "Glass" (transp 0.75) + "Aluminum" (opaque). `verify()` now asserts every rebuilt window's items are styled.

**— Survey findings (research phase): —**

- **Operational style is almost never machine-declared.** Of 208 surveyed windows, only 25 (12%) were classifiable at all; 183 (88%) came back `UNCLASSIFIED`. Only the ArchiCAD file reliably populated panel-operation enums. Revit encodes style in the **family/type Name string** ("M_Fixed", "M_Casement", or German "DK-Fix" = tilt-turn, "3 tlg" = 3-panel), not in structured IFC fields. **Converter implication: do not trust `PartitioningType`/panel operation enums alone to know what kind of window something is. Name-string parsing (multilingual) is load-bearing, not a fallback.**

- **"Revit bakes everything" is too crude — it's schema-version-dependent.** 2011-era Revit IFC2x3 exports (Duplex, Office_A) kept genuine swept-solid geometry (`IfcExtrudedAreaSolid`) plus `IfcWindowStyle`/lining properties — scored `SEMI_PARAMETRIC`. But modern Revit 2019–2022 **IFC4** exports (`FM_ARC_DigitalHub`, `ISSUE_159`) use explicit tessellated/brep geometry (`IfcAdvancedBrep`) — scored `BAKED_WITH_METADATA`. **If FormX ingests current Revit output, expect the baked case, not the lucky 2011 case.**

- **Worked example of what survives a modern-Revit-baked window** (file: a Revit 2019 IFC4 export; window Name `"FE 3 tlg - DK-Fix im Rahmen-DK-2:6000 x 1500:2529359"`):
  - Survived: `OverallWidth=6.0`, `OverallHeight=2.35` (note: this file's project units are **meters**, not millimeters — see unit gotcha below); some Pset scalars (`ThermalTransmittance=0.8`, `SolarHeatGainTransmittance=0.41`, `IsExternal=False`); the Name string itself, which encodes family + style hint + size + instance ID.
  - Lost: `PartitioningType=NOTDEFINED`; no `IfcWindowPanelProperties` at all (operation type gone from structured data — only recoverable from "DK-Fix" in the Name); `IfcWindowLiningProperties` entity existed but every numeric field on it was `None`.
  - Geometry: `IfcAdvancedBrep`, 14 explicit faces — a frozen shell, width/height only implicit in face coordinates.
  - **This is the central justification for the promote-not-reconstruct strategy**: nearly everything needed (dimensions + a style hint) survived outside the geometry; the brep itself should be discarded and regenerated from the golden template, not reverse-engineered.

- **Unit-handling gotcha (real, found via the example above):** survey files are not guaranteed to use millimeters. The golden targets use `MILLI METRE` throughout (see `author_goldens.py:base_file()`); real files may declare meters or other length units via `IfcUnitAssignment`. **The converter must read each input file's units and convert, never assume mm.** Not yet implemented anywhere.

- **`classify.py` is acknowledged-weak and will need real work.** Current logic is regex over English + German family names plus a few enum checks. It has already needed two correctness fixes during the golden-target build (sliders/hung defaulting to single-panel when undeclared — physically impossible, fixed to default to multi-panel; skylight not being checked before panel-operation logic, fixed to check `PredefinedType=SKYLIGHT` first). **Treat the classifier as a first draft, not ground truth** — expect to extend its name-pattern library significantly once real FormX input samples are available, and possibly add other languages.

- **Self-validation circularity, acknowledged.** The golden targets' "100/100 PARAMETRIC, round-trips correctly" results come from scoring our own output with our own rubric (`bakedness.py`/`classify.py`) — useful for catching internal bugs (and it did), but it is not independent proof of correctness. The schema validation (`ifcopenshell.validate`) and the human OpenIFCViewer check are the only checks so far that didn't come from our own code. Keep seeking independent checks as the converter is built — don't rely solely on the same scorer that defines the target.

- **No off-the-shelf converter exists for this problem** (verified by web search) — reconstructing parametric intent from already-baked geometry is described in the wild as largely unimplemented (e.g. GeometryGym's reverse-engineering attempts). This validates building in-house, but also means there's no reference implementation to check our approach against.

- **Generic/template window libraries (BIM&CO, NBS Source, Modlar, Parallax, etc.) are not a shortcut.** They distribute Revit/ArchiCAD-native families (not IFC), and exporting them to IFC would just reproduce the same baking problem one step earlier. Don't waste time chasing these as a source of pre-validated parametric IFC.

---

## 7. Converter pipeline — v1 AS BUILT + Phase-2 parking lot

**v1 is built and passing** (`IFC Window Converter/IFC_window_converter_V1.py`; full detail in that folder's `algorithm.md`). It deliberately follows the **proven Gal recipe** (Section 6), NOT the golden-spec. Per `IfcWindow`:

```
0. Skip if Description == "FormX-WIN1 parametric window"  (idempotent re-runs)
1. Measure local-frame bbox via ifcopenshell.geom (use-world-coords=False), ÷ unit_scale
   → file units. Thinnest axis = through-wall depth. (Empirical; orientation-agnostic.)
2. classify.classify(win, wt) → style_code, used ONLY for the canonical Name label +
   PredefinedType. Direct "skylight" name override → PredefinedType=SKYLIGHT.
3. Author 2 clean swept solids filling that exact box: IfcRectangleHollowProfileDef lining
   frame (full depth) + IfcRectangleProfileDef glazed pane (inset, thin, centred), extruded
   along the depth axis via an IfcExtrudedAreaSolid whose placement maps +Z onto that axis.
4. Point win.Representation at the new IfcProductDefinitionShape (reuse existing 'Body'
   context). Remove old per-window rep entities (de-reference layer assignments first);
   NEVER remove the shared IfcRepresentationMap. Preserve GlobalId + ObjectPlacement; set
   canonical Name + Description marker + PredefinedType. Opening/fill/void/containment
   chain is never touched.
5. verify(): non-window counts unchanged, window GlobalIds preserved, openings unmoved,
   per-window world-bbox drift ≤ 20 mm, no NEW ifcopenshell.validate errors vs source.
```

**Phase-2 parking lot (DEFERRED — do not forget, do not build until triggered):**
the golden-spec enrichment — `IfcWindowType` + `IfcWindowLiningProperties` +
`IfcWindowPanelProperties` (operation enums) + `Pset_WindowCommon` (carrying forward real
surviving U-values/IsExternal) + per-style **panel topology** (V/H mullions, multi-pane) +
the full 12-style golden vocabulary in `FormX 6.22 IFC Generated/`. **Promotion trigger:**
(a) FormX/CTO confirms the chat-manipulation actually consumes operation-type/panel
semantics, OR (b) a viewer/FormX "look right" test shows panel subdivisions (mullions)
matter visually. Until then this is unverified complexity beyond the proven recipe.
`WIN-HUNG-DBL_H` is abstract (a `SPECS` entry with no golden `.ifc`) — handle then.
- **Faithful rebuild of non-rectangular windows** (trapezoid/triangle/gable/arch): extrude the
  real face silhouette as an `IfcArbitraryClosedProfileDef` + inset pane, instead of the
  rectangular template. Until then they are preserved un-rebuilt (Section 6 fill-gate).

Still genuinely open: windows where the kernel can't read geometry are flagged `[SKIP]` and
left untouched (no dims to measure) — acceptable for v1, revisit if real inputs hit it.

---

## 8. Decisions log

| Decision | Rationale | Status |
|---|---|---|
| Target IFC4 (not IFC2x3, not IFC4.3, not IFC5) | IFC4 is the current ISO standard for practical purposes (IFC4.3 ADD2 is the latest, but IFC4 has the broadest tool support); IFC5 is alpha/not production-ready | Active |
| `style_code` = operation × panel-config, handing/shape as separate refinement columns | Keeps the FK clean; avoids combinatorial explosion of codes | Active |
| Golden targets are *authored* (IfcOpenShell), not sourced online | No real downloadable file came back as a clean parametric reference except one ArchiCAD casement; generic template libraries are Revit-native, not IFC | Active |
| `WIN-HUNG-DBL_H` demoted to abstract, excluded from concrete golden-target set | Can't be built without committing to single/double-hung; only useful as classifier fallback | Active |
| Plain `IfcWindow`/`'Body'` representation kept over `IfcWindowStandardCase`/`'Profile'` | Non-critical per spec (data is on the type regardless); more representative of real-world exporter output | Active, revisit only if formal IFC4 Reference View certification becomes a requirement |
| Promote-from-surviving-data strategy chosen over geometric reconstruction | Empirically justified by the worked example in Section 6 — dimensions/style hints usually survive even when geometry is baked | Active |
| **v1 follows the proven Gal recipe (clean geometry + Name + PredefinedType + relations), NOT the golden-spec** | Gal's functioning tools author no Psets/types; the golden apparatus is more than the working system needs and was Claude-authored, not FormX-confirmed (Section 6). Lower risk, more likely to drop into FormX. | **Active (v1)** |
| **Golden-spec richness (IfcWindowType + lining/panel/operation enums + Pset + per-style topology) deferred to Phase 2, not deleted** | Value is conditional on FormX consuming operation semantics — unverified. Parked in Section 7 with an explicit promotion trigger. | **Active — revisit on trigger** |
| **v1 rebuilds geometry from the window's OWN measured local bbox, one neutral frame+pane template** | Orientation-agnostic, keeps the window in its opening, makes it manipulatable. Per-style panel fidelity is the deferred layer. | **Active (v1)** |
| **Non-rectangular windows (fill-ratio < 0.95) are preserved un-rebuilt, not flattened** | The rectangular template would turn a trapezoid/triangle/arch into a rectangle; preserving original geometry is correct-but-baked. Faithful clean rebuild is Phase-2. | **Active (v1.1)** |
| **Surface styles (glass/frame) carried forward onto rebuilt geometry** | New items have no styling → viewers render gray; harvest + re-attach original `IfcSurfaceStyle`s. | **Active (v1.1)** |
| **Preserve original `GlobalId`; rebuild in place; only the window's representation/Name/PredefinedType change** | Keeps the opening/fill/void/containment chain valid; "change as little as possible." (Resolved the Section-7-draft open question.) | **Active (v1)** |
| **I/O: batch `INPUT_IFC_FILES_HERE/` → `OUTPUT_IFC_FILES_HERE/`, `-WIN1` suffix; single-file args too** | Honors the repo's existing folders + Gal's CLI shape. (`-W1` is taken by walls cleanup.) | **Active (v1)** |
| **Target schema is whatever the input is (IFC2X3/IFC4/IFC4X3), not forced to IFC4** | Converter edits in place; real ADUs vary by schema. Supersedes the "Target IFC4" row above *for the converter's I/O* (golden authoring still emits IFC4). | **Active (v1)** |
| **Done = "open + look right" in FormX/viewer (user's manual test); script self-validates structurally** | No FormX-side acceptance spec exists yet; structural `verify()` is the internal gate, user's viewer check is ground truth. | Active |

---

## 9. Glossary

- **Bakedness** — how much parametric intent a file's window *representation* retains; scored 0–100 by `bakedness.py` (PIS). Property of a file, not a style.
- **Golden target** — an authored, validated, fully parametric reference IFC file for one canonical style. The converter's output spec.
- **Style code** — canonical identity key (`WIN-<OPERATION>-<PANELCONFIG>`) for a window's operational type, independent of bakedness.
- **Promote vs. reconstruct** — promote: recover parameters from surviving Psets/Name onto a known template (tractable). Reconstruct: infer parameters from raw baked geometry alone (hard, avoided here).
- **PIS** — Parametric Integrity Score, the 0–100 output of `bakedness.py`.
