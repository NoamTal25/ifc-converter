# Window Style Survey — Pass 1

Two linked files that separate the **canonical taxonomy** (authored by us) from
the **survey of real files** (downloaded + classified). A guiding distinction:

- **Style** = *what kind of window it is* (its identity). The spine.
- **Bakedness** = *how usable a given file is as a reference* (a quality verdict
  on a file), NOT a property of the style itself.

The two files link on `style_code`: the registry defines the codes, the survey
classifies each found file into one of them.

--------------------------------------------------------------------------------
## FILE 1 — style_registry.csv  (the spine; authored, not harvested)

The controlled vocabulary every downstream artifact keys off. The survey
classifies *into* it; golden targets are authored one-per-code; the converter
resolves messy input → a `style_code` → that code's target IFC constructs.
Each row is one canonical window style.

### Columns

- **style_code** — The canonical, machine-stable key. Grammar:
  `WIN-<OPERATION>-<PANELCONFIG>`, e.g. `WIN-CASEMENT-DBL_V`. This is the value
  the survey, the golden targets, and the converter all share, so it must never
  drift. Handing (L/R) and shape (ROUND/ARCH) are deliberately NOT folded into
  the code — they live in separate refinement columns — so the code is always a
  clean, lookup-able key rather than an ever-branching string.

- **style_name** — Human-readable label for an audience ("Double (French)
  casement"). The plain-English counterpart to `style_code`.

- **operation** — *How (or whether) the window opens.* This is the primary
  classifying axis architects actually use. Values: FIXED, CASEMENT, AWNING,
  HOPPER, HUNG (and its SINGLEHUNG/DOUBLEHUNG sub-types), SLIDER, TILTTURN,
  PIVOTH, PIVOTV. This is the conceptual axis; `ifc_panel_operation` below is
  its IFC-enum encoding.

- **panel_config** — *Layout of the panes/sashes.* The second axis. Values:
  SINGLE (one panel), DBL_V (two panels split by a vertical mullion), DBL_H
  (two stacked, split by a horizontal transom), TRI_V (three vertical), etc.
  Conceptual counterpart to `ifc_partitioning_type`.

- **shape** — Outer silhouette of the frame. RECT (default), ROUND, ARCH, etc.
  A refinement axis: a round casement is still a casement. Drives the parametric
  *profile* of the geometry rather than the operation.

- **n_panels** — Integer count of distinct panels/sashes in the style. Sanity
  check and a hint for how many `IfcWindowPanelProperties` the target should
  carry (one per operable panel).

- **ifc_predefined_type** — The value the converter should write into
  `IfcWindowType.PredefinedType` (IFC enum `IfcWindowTypeEnum`). Almost always
  WINDOW; SKYLIGHT for roof-plane windows; LIGHTDOME for domed rooflights.
  This is part of the **output contract** — what "FormX-standard" means for
  this style at the type level.

- **ifc_partitioning_type** — Target value for `IfcWindowType.PartitioningType`
  (enum `IfcWindowTypePartitioningEnum`): SINGLE_PANEL, DOUBLE_PANEL_VERTICAL,
  DOUBLE_PANEL_HORIZONTAL, TRIPLE_PANEL_*, etc. The IFC encoding of
  `panel_config`. Output contract.

- **ifc_panel_operation** — Target value(s) for each panel's `OperationType`
  inside `IfcWindowPanelProperties` (enum `IfcWindowPanelOperationEnum`):
  e.g. FIXEDCASEMENT, SIDEHUNGLEFTHAND, TOPHUNG, BOTTOMHUNG, SLIDINGVERTICAL,
  TILTANDTURNRIGHTHAND, PIVOTHORIZONTAL. A `|` means "either hand is valid"
  (resolved by the `handing` refinement); a `,` lists per-panel operations for
  multi-panel styles (e.g. a double casement = LEFTHAND **,** RIGHTHAND).
  This is the operational heart of the output contract.

- **optional_refinements** — Documents the legal sub-variants of this base style
  (e.g. `-L / -R` for handing, `-ROUND` for shape, `->SINGLEHUNG / ->DOUBLEHUNG`
  for sub-typing a generic HUNG). Tells you how the code can be specialised
  without redesigning the scheme.

- **description** — One-line functional explanation for a human reader.

- **status** — Lifecycle flag. `seed` = part of the initial non-exhaustive set.
  Becomes `confirmed` once FormX signs off, `deprecated` if retired. Lets the
  registry grow toward exhaustive while tracking what's been ratified.

--------------------------------------------------------------------------------
## FILE 2 — window_file_survey.csv  (downloaded files, classified + scored)

One row per distinct window style found *inside* each real file. (A single file
often holds several styles; each becomes its own row.) Every row is traceable to
a real, downloadable source — nothing here is authored or synthetic.

### Identity & provenance columns

- **file_style_id** — Row primary key (F001, F002, …). Stable handle for
  referring to a specific found style.

- **file** — Local filename of the source IFC (e.g. `revit_duplex-apartment.ifc`).

- **origin_tool** — The authoring application, read from the file's
  `IfcApplication` entity (e.g. "Autodesk Revit Architecture 2011",
  "ARCHICAD-64"). Matters because *which tool exported it* is the single biggest
  predictor of how baked the result is.

- **ifc_schema** — The IFC schema version of the file (IFC2X3, IFC4,
  IFC4X3_ADD2), read from the file header. Affects which constructs are even
  available (e.g. IFC2x3 uses `IfcWindowStyle`; IFC4 uses `IfcWindowType`).

### Style-classification columns

- **style_code** — The registry `style_code` this found window was classified
  as — the foreign key linking this file back to FILE 1. `UNCLASSIFIED` when the
  operation genuinely can't be determined from the file. (FK integrity is
  verified: every non-UNCLASSIFIED code exists in the registry.)

- **handing** — Inferred hinge side: L, R, or `-` (unknown/not applicable).
  A refinement, only knowable when the file declares panel operation.

- **shape** — Inferred silhouette: RECT, ROUND, … Refinement axis.

- **operation_guess / panel_config_guess** — The two classifying axes as
  inferred for *this* file (before assembly into `style_code`). Exposed
  separately so you can see each axis's call, not just the combined code.

- **classification_confidence** — How much to trust the `style_code`:
  - `high`  — the file explicitly declared panel operation via an IFC enum.
  - `medium`— inferred from the family/type **name** (e.g. "M_Casement").
  - `low`   — only a size string or nothing usable; mostly assumption.
  This is the column to filter on before trusting any classification.

- **classification_basis** — *What kind of evidence* produced the call:
  `panel_operation_enum` (strongest), `partitioning_enum`, `family_name`,
  `predefined_type`, `shape_name`, or `none`. Pairs with `confidence`.

- **classification_evidence** — The actual raw signals used, quoted from the
  file (the name string, the partitioning enum value, the panel-operation list).
  Makes every label auditable — you can see *exactly* why F010 was called a
  casement (its name was `m_casement:819mm…`) without reopening the file.

### Bakedness columns  (full rubric lives in bakedness.py)

- **parametric_integrity_score** — 0–100; **higher = less baked = more
  recoverable parametric intent.** A weighted sum over six inspectable signals:
  geometry type (parametric swept solid vs frozen mesh) is the largest weight,
  then semantic typing, lining/panel property sets, classification enums,
  general property sets, and dimensional attributes.

- **bakedness_class** — The score bucketed into a label:
  - `PARAMETRIC` (≥78) — editable; parametric geometry + the right constructs.
  - `SEMI_PARAMETRIC` (50–77) — partial; some constructs survive, some baking.
  - `BAKED_WITH_METADATA` (20–49) — geometry frozen, but parameter *values*
    survive in property sets (so the converter can *promote*, not reconstruct).
  - `FULLY_BAKED` (<20) — geometry only; parametric intent effectively gone.
  - `STUB_NO_GEOMETRY` — the window carries no shape at all (bakedness N/A;
    it's an empty placeholder, not a baked solid — a distinct edge case).

- **geometry** — How the window's shape is represented, the dominant bakedness
  signal: `parametric_swept` (extruded/revolved solid — regenerable),
  `csg_boolean` (constructive solid — semi), `explicit_baked` (B-rep / triangle
  mesh — frozen), or `none_or_unknown`.

- **has_lining_props / has_panel_props** — Booleans: does the window carry an
  `IfcWindowLiningProperties` / `IfcWindowPanelProperties` set? These hold the
  genuinely parametric window detail (lining depth/thickness, mullion/transom
  offsets, per-panel operation). Their presence is what separates a rich export
  from a dumb one, independent of geometry.

### Fitness verdict

- **golden_target_verdict** — Fuses bakedness + classification into a single
  call on whether this file can serve as a golden target for its style:
  - `candidate` — not baked AND confidently classified. Usable as-is.
  - `candidate_review` — not baked, but medium-confidence style → human-confirm.
  - `needs_style_review` — clean enough, but style is low-confidence/unconfirmed.
  - `specimen_only` — too baked (or a stub) to be a target; it documents the
    *input* problem the converter must solve, rather than the desired output.

### Source / context columns

- **source_style_name** — The raw name the file gave this window/type
  ("M_Fixed:4835mm x 2420mm", "Rundfenster 13"). Preserved verbatim so you can
  see what real-world labels look like (usually size strings, not style names).

- **window_count** — How many window instances in the file share this style.
  A frequency signal — which styles are common in real models.

- **source** — Provenance tag: `downloaded` (a real file from the internet) vs
  `authored` (would be a file we generated). All pass-1 rows are `downloaded`.

- **source_url** — The repository the file was obtained from (verifiable).

- **provenance_note** — Short description of what the file is and where it
  originated (e.g. "Duplex Apartment, common openBIM test model").

- **license_note** — Licensing status / what still needs verifying before the
  file is used beyond internal R&D. Flagged per row rather than assumed clear.

--------------------------------------------------------------------------------
--------------------------------------------------------------------------------
## FILE 3 — golden_targets/ + golden_targets.csv  (authored; the OUTPUT spec)

12 parametric IFC4 windows, one per *concrete* registry style, authored with
IfcOpenShell (author_goldens.py). These ARE the FormX output contract made real:
each uses IfcWindowType + IfcWindowLiningProperties + one IfcWindowPanelProperties
per panel, geometry as PARAMETERIZED swept solids (rectangle-hollow frame profile
+ extruded panes/mullions, never baked mesh), Pset_WindowCommon, and populated
OverallWidth/Height. origin_tool = "FormX Golden Target Authoring".

Every target was validated three ways (see golden_targets.csv):
  - bakedness: all score PIS=100 / PARAMETRIC on the same metric used for the survey
  - renders:   geometry tessellates (opens cleanly in an IFC viewer)
  - roundtrip: re-classifying our own output returns the correct style_code

Authoring finding: `WIN-HUNG-DBL_H` was dropped from the concrete set and marked
`status=abstract` in the registry. A "generic hung" window can't be built without
committing to single- vs double-hung (one vs two operable sashes), so it exists
only as a classification fallback parent, not a buildable target. (13 registry
styles -> 12 concrete golden targets + 1 abstract.)

## Key findings (pass 2 — 17 files, 208 style rows)

1. **Operational style is essentially absent from real IFC.** Across 17 diverse
   buildings, 183 of 208 window styles (87%) could NOT be classified
   by operation, and 185/208 (88%) are low-confidence. Only ArchiCAD
   reliably declared panel-operation enums. CONCLUSION: a style taxonomy cannot
   be harvested from the wild — it must be authored (FILE 1), and the converter
   cannot rely on IFC enums to recognise window kind; it needs name heuristics /
   human-in-the-loop.

2. **Modern Revit IFC4 exports are BAKED — the earlier optimism was schema-bound.**
   The 2011 IFC2x3 Revit files kept swept solids (SEMI_PARAMETRIC). But the
   modern IFC4 Revit files added in this pass (FM_ARC_DigitalHub / Revit 2019,
   ISSUE_159 / Revit 2022) are BAKED_WITH_METADATA with explicit (tessellated)
   geometry. So if FormX ingests CURRENT Revit output, expect baked geometry:
   the converter's job is "promote from surviving Psets", not "swept already
   there". Parameter VALUES still survive in property sets (hence _WITH_METADATA,
   not FULLY_BAKED) — so promotion, not triangle-reconstruction.

3. **Every clean golden-target candidate is ArchiCAD; none are Revit.** All 9
   `candidate` rows come from ArchiCAD (which writes operation enums). Revit
   files are `specimen_only` / `needs_style_review` — specimens of the input
   problem, not targets.

4. **17 real buildings cover only 6 of 13 styles — and 'cover' is generous.**
   Only 25 of 208 windows (12%) were classifiable at all; among those, 6 distinct
   styles appeared: fixed, single & double casement, tilt-turn, skylight, and a
   single name-inferred slider. The 7 uncovered styles (awning, hopper,
   single/double/generic hung, both pivots) have ZERO IFC panel-operation-enum
   occurrences across all 17 files — flatly absent at the declared level, not
   merely unclassified. "6 of 13" therefore measures what is legibly DECLARED,
   not what physically exists; up to 7 styles could be hiding among the 183
   unclassified windows and be invisible. Either way: these 7 must be authored.

## Caveats
- Provenance: open test fixtures (buildingSMART, ThatOpen/web-ifc). Immediate
  source verifiable; per-file original author/license needs confirmation before
  use beyond internal R&D (flagged per row).
- Confidence skew is real signal, not noise: most rows are low-confidence because
  the files genuinely don't declare style. Treat UNCLASSIFIED/low rows as
  "input specimens", not taxonomy.
- Largest files (49-73 MB) were fully processed; grouping by window TYPE keeps
  even a 1199-window file to a handful of style rows.
