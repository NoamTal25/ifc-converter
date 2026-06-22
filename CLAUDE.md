# FormX Window IFC Converter — Project Context

**Status:** Pre-implementation. Research, taxonomy, and golden targets are done. No converter code exists yet.
**Scope:** Windows only (first element type in a planned multi-element converter — doors etc. follow later, out of scope here).

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

All built and validated in the research phase preceding this converter. Paths below assume these sit in the project root or are copied in — **update these paths if your repo layout differs.**

| File | What it is | Status |
|---|---|---|
| `style_registry.csv` | Canonical style taxonomy: 13 rows (12 concrete + 1 abstract). `style_code` is the FK every other artifact keys off. Columns include target IFC enums (`ifc_predefined_type`, `ifc_partitioning_type`, `ifc_panel_operation`) — the **output contract**. | Stable, `status=seed` — extensible, not exhaustive |
| `window_file_survey.csv` | 208 window-style rows from 17 real downloaded IFC files (Revit 2011–2022, ArchiCAD, Rhino, IfcOpenShell-authored; IFC2x3 and IFC4). Each row: `style_code` classification (with confidence + basis + evidence), bakedness score/class, provenance. | Done; see Section 6 for headline findings |
| `golden_targets/*.ifc` + `golden_targets.csv` | 12 authored, parametric IFC4 window files — one per concrete registry style. **These are the converter's output spec.** | Validated (see below) |
| `bakedness.py` | The Parametric Integrity Score (PIS, 0–100) scorer. Classes: `PARAMETRIC` (≥78) / `SEMI_PARAMETRIC` (50–77) / `BAKED_WITH_METADATA` (20–49) / `FULLY_BAKED` (<20) / `STUB_NO_GEOMETRY`. | Stable |
| `classify.py` | Style classifier: tries panel-operation enums first (high confidence), falls back to family-name regex (medium), else low/`UNCLASSIFIED`. Handles English + German terms. | Working but acknowledged-weak — see Section 6 |
| `author_goldens.py` | IfcOpenShell engine that builds the golden targets (parametric swept geometry + lining/panel properties + Psets). | Stable; spec for `SPECS` list is in-file |

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

## 7. Proposed converter pipeline (DRAFT — not yet implemented, not yet agreed)

This is a starting sketch for the first implementation pass, not a locked spec. Update this section as soon as real design decisions are made or this diverges from what's actually built.

```
for each IfcWindow in input file:
    1. Extract survivors:
       - OverallWidth, OverallHeight (convert using file's actual IfcUnitAssignment)
       - Name string of the IfcWindow and its IfcWindowType
       - Any populated values in attached property sets (Pset_WindowCommon, etc.)
       - Any populated PartitioningType / panel OperationType enums (rare but check first — high confidence when present)

    2. Classify -> style_code (extend classify.py; enum check first, name-parse fallback,
       multilingual; flag low-confidence results rather than silently guessing)

    3. Look up the golden target for that style_code (golden_targets/<style_code>.ifc)

    4. Re-author a new window:
       - Start from the golden target's structure (IfcWindowType, lining/panel properties, swept geometry)
       - Substitute real OverallWidth/OverallHeight
       - Carry forward any real survived Pset values (don't discard real U-values etc. in favor of placeholders)
       - Regenerate geometry parametrically at the real dimensions (don't reuse golden's placeholder dims)

    5. Replace the baked window in the spatial/relational structure
       (IfcRelFillsElement -> IfcOpeningElement -> IfcRelVoidsElement -> IfcWall chain
        should be preserved; only the window's own representation + type changes)

    6. Validate the output window the same way golden targets were validated:
       bakedness.py score should be PARAMETRIC, classify.py round-trip should match
       the assigned style_code, ifcopenshell.validate should be clean.

    7. Flag (don't silently drop) any UNCLASSIFIED or low-confidence windows for
       human review rather than guessing a style.
```

Unresolved before this can be finalized: how to handle windows where *no* dimensions survived; whether to preserve the original `GlobalId` or mint new ones; batch vs. one-file-at-a-time CLI design; what "FormX-standard" output should be named/organized as on disk.

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

---

## 9. Glossary

- **Bakedness** — how much parametric intent a file's window *representation* retains; scored 0–100 by `bakedness.py` (PIS). Property of a file, not a style.
- **Golden target** — an authored, validated, fully parametric reference IFC file for one canonical style. The converter's output spec.
- **Style code** — canonical identity key (`WIN-<OPERATION>-<PANELCONFIG>`) for a window's operational type, independent of bakedness.
- **Promote vs. reconstruct** — promote: recover parameters from surviving Psets/Name onto a known template (tractable). Reconstruct: infer parameters from raw baked geometry alone (hard, avoided here).
- **PIS** — Parametric Integrity Score, the 0–100 output of `bakedness.py`.
