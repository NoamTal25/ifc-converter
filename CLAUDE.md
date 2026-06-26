# FormX IFC Converter — Project Context

**What this is.** A growing set of per-component IFC "converters" that rebuild Revit-exported
building elements into clean, **parametric, manipulable** geometry for FormX — *one converter
per element type*, designed to compose into a single "fix any building IFC" pipeline. The
**window converter is the proven reference implementation**; the **door converter is the second**
(`reference/IFC Door Converter v1/`, converter v2.1 built — now **reference v1**, pending a
golden-template v2). This file is the generalized playbook **and** the
project memory.

**Status.** Window converter v1: built, **self-contained** (depends on ifcopenshell only),
validated on every real ADU (IFC2X3 / IFC4 / IFC4X3), shipped with an automated manipulability
tester (teeth-verified). Door converter v2.1: **built + self-contained**, algorithm spec written,
validated on every real ADU (all 11 doors rebuilt across IFC2X3 / IFC4 / IFC4X3, `verify()` ALL
CHECKS PASSED, no new validate errors), shipped with an automated manipulability tester
(4/4 fixtures, 272 checks, teeth-verified). Gal's three sibling tools (walls/levels/floors) are
the original design template — see §4.

**⚠️ Direction pivot (2026-06-25) — READ §1/§2/§7 before building.** The project is moving to a
**golden-template-swap** methodology, **project-wide**: instead of measuring a baked element and
rebuilding a neutral parametric template in code, the converter **classifies** each element into one
of **FormX's defined types**, loads that type's **golden template IFC**, **injects the instance's
parameters** (dimensions, …), and **swaps it in**. The existing **window v1** and **door v2.1**
converters are the **prior measure-and-rebuild approach** — kept as **reference, not deleted** (they
approximate rather than matching FormX's catalog). The next window converter is built **fresh**
under this new method (separate chat).

**Environment.** ifcopenshell 0.8.5 under **`python3.11`** — run *everything* with `python3.11`.
Plain `python3` does NOT have ifcopenshell on this machine.

---

## 0. Update protocol — READ THIS FIRST

This file is the persistent memory across Claude Code sessions. It is the only source of truth
for *why* things are built the way they are. **Any session must:**
- Update the relevant section **immediately** after writing code, changing architecture, or
  discovering something non-obvious — not "later."
- Add new cross-cutting discoveries to **§6 (Findings & gotchas)**; update **§4 (Artifacts)**
  the moment a file is added/renamed/moved (stale paths here are worse than none); log reversed
  decisions in **§7** rather than deleting them.
- Keep it scannable in a few minutes — this file is the map, not the territory. When in doubt
  whether a change is worth logging, log it.

---

## 1. What FormX is, and why these converters exist

FormX builds AI-driven modular homes with a chat-driven design tool ("make this window wider").
That manipulation only works if the data is **semantic + parametric + relational** — the system
must know an object *is* a window, that its width is a *live parameter*, and how it relates to
its host wall. IFC is the chosen format (open, vendor-neutral, semantically rich, read/writable
via IfcOpenShell).

**The problem:** designs are authored in Revit (parametric internally), but Revit's IFC *export*
bakes that into **frozen geometry** — the recipe is lost; only the shape (and scattered metadata)
survives. "Make it wider" has nothing to grab. **Each converter undoes this for one element
type**, rebuilding baked geometry into clean parametric form. No off-the-shelf tool does this.

**Go-forward strategy (project-wide, set 2026-06-25) — classify → instantiate FormX's golden
template → inject params.** FormX maintains a defined catalog of element types (windows first), each
with a **golden template IFC** that is already clean + parametric. The converter, per element:
**(1)** classifies the baked element into one of those FormX types by its **naming standard**;
**(2)** loads that type's **golden template IFC**; **(3)** injects the instance's specific parameters
(dimensions — possibly more); **(4)** **swaps** the baked element for the parameterized template,
preserving `GlobalId` / `ObjectPlacement` / relationships. Geometry now comes from **FormX's
templates**, not from code-authored shapes — so the output *matches FormX's catalog* instead of
approximating it. Classification is now **structural** (it picks the template), not cosmetic.

**v1 strategy (window v1 / door v2.1 — reference) — promote/measure-and-rebuild, never reverse-
engineer a mesh.** Measure the element's real extents, then author a clean neutral parametric
template *in code* that occupies the same space. Same *promote-not-reconstruct* spirit; what changes
go-forward is the **geometry source** (FormX golden templates vs. code-authored). Kept as reference —
its preserve-identity / copy-in-place / styles / verify+teeth machinery carries straight over.

**Open question — partially resolved (2026-06-25):** the team has now informed the design — FormX
*does* have an element-type taxonomy + golden template IFCs the converter instantiates (this fired
the §7 golden-spec promotion trigger). Still open: whether FormX could read source `.rvt` directly
(would bypass the baking problem entirely) — treat as a scoping question, not a settled assumption.

---

## 2. THE CONVERTER RECIPE

> **Two recipes now.** §2a is the **go-forward golden-template-swap** recipe (project-wide, the new
> method). §2b is the **v1 measure-and-rebuild** recipe (window v1 / door v2.1 — reference). Both
> share the same preserve-identity / copy-in-place / verify+teeth backbone; they differ in where the
> geometry comes from.

### 2a. Go-forward recipe — classify → instantiate golden template → inject params

For each element of your type, in a copied model:
1. **Classify** the baked element into one of **FormX's defined types** by its naming standard
   (this is now *structural* — it selects the template; a wrong class = wrong geometry).
2. **Load that type's golden template IFC** (FormX-provided; already clean + parametric).
3. **Inject the instance's parameters** — dimensions (from the baked element's measured extents),
   and whatever else the template exposes — into the template.
4. **Swap** the baked element's representation for the parameterized template, **preserving
   `GlobalId` / `ObjectPlacement` / the opening→fill→void→host chain / spatial containment** (§2b
   points 1, 6 carry over verbatim) + surface styles, canonical Name + `PredefinedType`, idempotency
   marker.
5. **Gate** elements that don't classify / can't be templated — leave untouched + flag (point 9).
6. **Ship `verify()` + a teeth'd tester + a testing-agent `.md`** (point 10), adapted to assert the
   element now matches its golden template.

*(Open design qs for the new window converter, to settle in its own chat: where template IFCs live;
the exact naming-standard → type map; full param set beyond dimensions; how a template is
instantiated — geom copy + scale vs. parametric fill. The archived goldens in §8 are the starting
reference.)*

### 2b. v1 recipe — measure-and-rebuild (reference)

This is the distilled pattern, proven by the window converter + Gal's three production tools.
A converter for any element type does this and **nothing more** (the only per-element part is the
geometry authoring + that element's specific relationships):

1. **Only touch your element type; preserve everything else exactly.** Counts of every other
   element/relationship type, all GlobalIds, all `ObjectPlacement`s, and every relationship edge
   are identical before→after. "Change as little as possible to make it work."
2. **Edit a copy in place.** Copy `src`→`out`, work on the copy, never modify the original.
   Batch `INPUT_IFC_FILES_HERE/` → `OUTPUT_IFC_FILES_HERE/` with a stage suffix; also accept
   explicit single-file in/out args.
3. **Be self-contained** — depend on ifcopenshell only, no project-internal imports. (Any style
   labelling is an inline keyword scan; it's cosmetic.)
4. **Measure the element's own geometry, don't assume conventions.** Read its local-frame bbox
   from the kernel; for in-wall elements the **thinnest axis = through-wall depth**. Orientation-
   agnostic → the rebuild lands exactly where the original was.
5. **Author clean parametric swept geometry** (e.g. `IfcRectangleHollowProfileDef` /
   `IfcRectangleProfileDef` extruded) so dimensions become drivable parameters → manipulable.
6. **Preserve identity + relationships.** Keep `GlobalId` + `ObjectPlacement`; give the element a
   fresh per-instance representation; never touch the `opening→fill→void→host` chain or spatial
   containment.
7. **Carry forward surface styles** (color/transparency) onto the new geometry, or viewers render
   it gray.
8. **Canonical Name + standard `PredefinedType`.** Stamp a `Description` marker for idempotency.
9. **Gate edge cases — preserve, don't corrupt.** If the neutral template can't faithfully
   represent an element (odd shape, unreadable geometry), leave the original untouched and flag it.
10. **Ship a built-in `verify()`** (re-open src+out; assert the only-your-element invariants;
    introduce no new `ifcopenshell.validate` errors vs source) **and a separate automated tester
    with teeth** (a negative control that must fail) + a testing-agent `.md`.

The window converter is the canonical implementation of all ten; read it before writing a new one.

---

## 3. Building the next converter (step-by-step)

> **Reference (v1 measure-and-rebuild procedure).** This was the playbook for the window/door
> converters. Go-forward converters follow the **§2a golden-template-swap** recipe instead; the
> scanning/mapping (steps 1, 3) + I/O + verify/test discipline still apply.

General procedure for any component:

1. **Scan the real inputs** for your element type, its geometry forms (mapped? brep? swept?), and
   its relationships. A tiny `by_type` script over `INPUT_IFC_FILES_HERE/*.ifc` (use schema-safe
   `try/except` per §6) tells you counts, schemas, units.
2. **Create `IFC <Component> Converter/` and copy the window converter's spine** — the §2 recipe
   is mostly element-agnostic; reuse the measure→rebuild→preserve→style→verify scaffolding.
3. **Map the element:** its IFC type(s) (incl. `…StandardCase` variants), the relationships to
   preserve, its geometry variety, and the edge cases to gate.
4. **Implement** the per-element geometry authoring + relationship preservation.
5. **Pick a free stage suffix** (walls `-W1`, levels `-L1`, floors `-F1`, windows `-WIN1`; doors
   `-D1` is free). Suffixes compose along the pipeline.
6. **Write the docs + tests**, mirroring the window converter: `IFC <component> algorithm.md`,
   `test_<component>_converter.py`, `<COMPONENT>_CONVERTER_TESTING_AGENT.md`.
7. **Validate** on `INPUT_IFC_FILES_HERE/` and confirm the tester's teeth (a no-op must fail).

**Combining into one tool (later):** the unified "fix any building" converter is an **orchestrator**
that runs the per-element converters in dependency order (host elements like walls before the
windows/doors that fill their openings), under the shared §2 contract. Modularity is already proven
to compose (Gal's `-W1-L1-F1` + our `-WIN1` chain). Full component taxonomy / registry: see the
plan file (not yet in-repo).

---

## 4. Current artifacts (the working set)

**Repo layout (reorganized 2026-06-26):** active converters + the IO folders live at the repo
root; superseded/reference material lives under `reference/`. See `README.md` for the full tree.

```
ifc-converter/
├── IFC Window Converter v2/     ACTIVE — window converter (golden-template-swap, go-forward §2a)
├── IFC Door Converter v2/       ACTIVE — door converter (golden-template-swap, go-forward §2a)
├── INPUT_IFC_FILES_HERE/        batch input + tester fixture corpus
├── OUTPUT_IFC_FILES_HERE/       converter outputs (gitignored)
└── reference/
    ├── IFC Window Converter v1/        superseded by v2 (measure-and-rebuild)
    ├── IFC Door Converter v1/          door v2.1 (measure-and-rebuild) — superseded by door v2
    ├── Gal_Similar_Project_Refrences/  Gal's 3 production tools (design template)
    └── Old Context/                    research archive + golden-spec prototype (§8)
```

| Path | What it is | Status |
|---|---|---|
| `reference/IFC Window Converter v1/IFC_window_converter_V1.py` | **Window v1 (measure-and-rebuild) — superseded by v2, kept as reference (moved under `reference/` 2026-06-26).** Rebuilds each `IfcWindow` into a clean parametric hollow-frame + pane from its own measured bbox; preserves GlobalId/placement/relationships + surface styles; canonical Name + `PredefinedType`; gates non-rectangular + unreadable windows. **Self-contained (ifcopenshell only).** Batch INPUT→OUTPUT `-WIN1`, also single-file args. *(Paths fixed for the deeper location: `_ROOT = _HERE.parent.parent`.)* | Reference; all fixtures pass |
| `reference/IFC Window Converter v1/IFC window converter algorithm.md` | v1 living spec (Gal's doc structure). | Reference |
| `reference/IFC Window Converter v1/test_window_converter.py` + `WINDOW_CONVERTER_TESTING_AGENT.md` | v1 **automated manipulability tester** + its subagent spec. Runs the converter on every `INPUT/` fixture in throwaway temps, re-derives invariants independently, manipulates each rebuilt window (resize/move/rotate), kernel-free, teeth + pinned `BASELINE_REBUILT`. Run `python3.11 "reference/IFC Window Converter v1/test_window_converter.py"`. | Reference; 4/4 pass; teeth verified |
| `reference/IFC Door Converter v1/IFC_door_converter_V1.py` | **Door converter v1 (v2.1 algorithm, measure-and-rebuild) — moved under `reference/` 2026-06-26, pending a golden-template v2. Paths fixed for the deeper location (`_ROOT = _HERE.parent.parent`).** Modular: `_classify`→`_rebuild_plan` makes a recipe `{panels, arrangement, folding, hardware}`; `_classify`→`_rebuild_plan` makes a recipe `{panels, arrangement, folding, hardware}`; `_assemble` composes a parts library (inline lining/pane/divider builders + `_build_handles`/`_door_depth`) into a **single frame layer** — outer lining + panes + dividers — plus a canonical **handle**, per class (French→lining+2 panes+mullion+2 levers, four-fold→4 panes+3 mullions, single-flush→lining+slab+lever, sliding→flush-pull, overhead→stacked+no handle). **Glazed doors measure their member layout 1-1** (border + pane widths + mullion from the transparent sub-solids, `_measure_layout`); opaque fall back to even-tiling. Panel count from the real `OperationType` enum (Name fallback); folding-depth clamp. Faithful (overall dims + glazed layout + colors/props measured/preserved) not pixel-identical. **Swaps only the Body, leaves `FootPrint` untouched** (match by `.id()`); gates non-rectangular + unreadable. **Self-contained.** Batch INPUT→OUTPUT `-D1`. | Reference; all 4 fixtures (11 doors) pass `verify()` |
| `reference/IFC Door Converter v1/IFC door converter algorithm.md` | Door v1 living spec (mirrors the window algorithm doc); Step 2 holds the comprehensive door taxonomy. | Reference |
| `reference/IFC Door Converter v1/test_door_converter.py` + `DOOR_CONVERTER_TESTING_AGENT.md` | Door v1 **automated manipulability tester** + its subagent spec. Mirrors the window tester: runs the converter on every `INPUT/` fixture in throwaway temps, re-derives invariants independently, manipulates each rebuilt door (resize/move/rotate), kernel-free, teeth + pinned `BASELINE_REBUILT`. Run `python3.11 "reference/IFC Door Converter v1/test_door_converter.py"`. | Reference; 4/4 pass; teeth verified |
| `IFC Window Converter v2/` | **The golden-template-swap window converter (the go-forward §2a method, built 2026-06-25).** Classify→author golden→inject params→swap. Modules: `generate_goldens.py`→`golden_templates/*.ifc` (7 reviewable FormX golden templates), `golden_geometry.py` (the SHARED parametric recipe used by both the goldens and the converter — so output == golden, scaled), `classify_window.py` (Name→recipe, PDF taxonomy), `schema_adapter.py` (the per-IFC2X3/4/4X3 quirk locus), `IFC_window_converter_V2.py` (main, suffix `-WIN2`, swaps Body only, preserves FootPrint, authors `Pset_WindowCommon` + lining/panel props at the **occurrence** level — no 2nd `IfcWindowType`), `test_window_converter_v2.py` + `WINDOW_CONVERTER_V2_TESTING_AGENT.md`, `IFC window converter v2 algorithm.md`. **Self-contained.** | Built; `verify()` ALL PASS on all 4 ADUs (rebuilt 6/5/4/8; trapezoid+skylight+bodiless gated), 0 new validate errors, tester 4/4 (394 checks) teeth-verified. **Awaiting user viewer review of goldens + outputs.** |
| `IFC Door Converter v2/` | **The golden-template-swap DOOR converter (go-forward §2a, built 2026-06-26).** Mirrors window v2 module-for-module. Modules: `door_types.py` (the **single source of truth** — 16 FormX door types; both the generator and classifier import it, so the catalog is edited in one place), `generate_goldens.py`→`golden_templates/*.ifc` (**16** reviewable goldens), `golden_door_geometry.py` (the SHARED recipe — `build_door_items` + `dims_in_units(scale)` so output == golden scaled, scale-correct in mm/ft), `classify_door.py` (Name→type per the PDF rules, tuned to real names), `schema_adapter.py` (IfcDoorType/IfcDoorStyle + styles + occurrence psets — never a 2nd type), `IFC_door_converter_V2.py` (main, suffix `-D2`, swaps Body only, preserves FootPrint + real Revit handedness, harvests the baked door's own colours, **folding-depth clamp**, authors `Pset_DoorCommon` + `FormX_Door_Window` + lining/panel props at the **occurrence** level), `test_door_converter_v2.py` (7 layers incl. a classification-multiset teeth layer) + `DOOR_CONVERTER_V2_TESTING_AGENT.md`, `IFC door converter v2 algorithm.md`. **Self-contained.** | Built; goldens 16/16 validate clean; `verify()` ALL PASS on all 4 ADUs (all 11 doors rebuilt 2/1/3/5), 0 new validate errors, tester 4/4 (276 checks) teeth-verified. **Awaiting FormX-architecture viewer review of goldens + outputs.** |
| `reference/Gal_Similar_Project_Refrences/` | Gal's three production tools (walls cleanup / levels organizer / floors definer) + their algorithm.md & testing docs. The **design template** (CLI shape, built-in `verify()`, "only-touch-your-element" discipline, testing methodology). *(moved under `reference/` 2026-06-26)* | Reference |
| `INPUT_IFC_FILES_HERE/` | Real FormX ADUs — the converter's batch input **and** the tester's fixture corpus: `LEXFORD_OFFICE-C1` (IFC2X3), `SAN_JUAN_CYPRESS…-W1-L1` (IFC4X3, already through walls+levels), `Sunflower_A` (IFC2X3), `Turnberry…-C1` (IFC4). | Active |
| `OUTPUT_IFC_FILES_HERE/` | Converter outputs (`-WIN1`/`-WIN2`/`-D1`/`-D2` etc.), gitignored. | — |
| `reference/Old Context/` | Pre-converter research + the golden-spec prototype (12 authored golden window IFCs + `author_goldens.py` + style taxonomy). **Now the prototype reference for the golden-template-swap pivot** — see §8. *(moved under `reference/` 2026-06-26; §8 path mentions of `Old Context/` now resolve to `reference/Old Context/`.)* | Reference (promoted 2026-06-25) |

---

## 5. Door converter — v1 (measure-and-rebuild, reference)

> **SUPERSEDED by door v2 (§5b, golden-template-swap, built 2026-06-26).** This `reference/IFC Door
> Converter v1/` section documents the **measure-and-rebuild v1** door converter, kept as working
> reference. Run from its path: `python3.11 "reference/IFC Door Converter v1/test_door_converter.py"`.

Confirmed by the grounding scan: **11 `IfcDoor` across the 4 ADUs** (no `IfcDoorStandardCase`),
all in feet, schemas IFC2X3/IFC4/IFC4X3. Each door **fills an `IfcOpeningElement`** voided into a
wall (`IfcRelFillsElement → IfcOpeningElement → IfcRelVoidsElement → IfcWall`), contained in an
`IfcBuildingStorey` — the same chain as windows, with the same three Body geometry kinds
(mapped FacetedBrep / ExtrudedAreaSolid / AdvancedBrep). So the window spine transferred directly.

**The door converter is v2.1: MODULAR + classification-driven, single frame layer.** `_classify` →
`_rebuild_plan` returns a recipe `{panels, arrangement, folding, hardware}`; an **assembler**
(`_assemble`) composes a small **parts library** (inline lining/pane/divider builders +
`_build_handles`, `_door_depth`) per the recipe, each part a `(solid, role)` styled role-keyed.
Adding a door class = a new recipe over the existing modules, not a new monolith. Deltas vs the
window converter:
- **Class drives the build — one frame layer:** outer lining + panes + dividers (NO nested
  per-leaf sub-frames). A flush single → lining + 1 pane + lever; French/double → lining + 2 panes
  + central mullion + 2 levers; four-fold → lining + 4 panes + 3 mullions + flush-pull;
  overhead/sectional → lining + stacked sections + rails + no handle. `side-by-side` divides width,
  `stacked` divides height. Each pane is bounded by lining + dividers, so it still reads as a framed
  leaf. **(Fixed the viewer regressions the user caught: French doors flattened → now panel
  topology; missing handles → canonical handle; skinny/then-doubled frame → measured single layer.)**
- **Glazed doors measure their member layout 1-1** (`_measure_layout`): from the transparent (glass)
  sub-solids, the real border = (door face − glazed opening)/2 → lining `WallThickness` *directly*
  (single layer, NO halving — the old lining+stile doubling is gone), and pane widths + mullion gaps
  come from the measured glazed extents (honours uneven splits). LEXFORD French → ~6.4″ border.
  Opaque doors have no glass to decompose → even-tiled at the default border.
- **Canonical handle, not preserved hardware.** A per-class detail audit (small-in-both-face-dims =
  hardware) showed originals carry handles/hinges + per-leaf stiles; we author a reusable handle
  (`lever` / `flush_pull` / `none` by family, side from handedness), proud of BOTH faces,
  placement-anchored so it moves with the door and never stretches. No baked hardware preserved.
- **Panel count from the real `OperationType` enum when present** (machine-declared on
  `IfcDoorStyle`/`IfcDoorType`, e.g. `DOUBLE_DOOR_SINGLE_SWING_OPPOSITE_*`), else the Name. Still
  not *authored* back (handedness/`IfcDoorLiningProperties`/`IfcDoorPanelProperties` = deferred
  golden-spec, promote if FormX consumes named door params).
- **Faithful, not pixel-identical (Path A).** Overall W/H/D + glazed member layout measured, colors
  harvested, properties preserved; part shapes are clean canonical rectangles. **Folding-door depth
  clamp:** folded leaves project (bbox depth ~1.55 ft on the four-fold) → `_door_depth` clamps so we
  don't build a 1.5-ft-thick door.
- **Axis roles empirical:** thinnest = depth; of the two face axes the more-vertical (max world |Z|)
  = height. **Classifier layers:** A (`PredefinedType`, 5 `IfcDoorTypeEnum`) × B (operation family,
  23 ops → 12 families) × C (glazed) are orthogonal facets; D (the recipe) is derived from them and
  drives geometry. Full taxonomy + per-class recipes in the algorithm doc §2/§3.
- **Swaps only the `Body` shaperep in place**, preserving `FootPrint`. *(Gotcha: match by `.id()`,
  not `is` — see §6.)* Suffix `-D1`; marker `Description == "FormX-D1 parametric door"`.

Result: all 11 doors rebuilt with class-correct topology + measured glazed layout + handles (0 kept,
0 skipped), `verify()` ALL CHECKS PASSED on all 4 fixtures (drift measured on the **face plane** so a
proud handle / depth-clamp doesn't trip it), no new validate errors, idempotent. The automated
tester (`test_door_converter.py`, 4/4 fixtures, teeth-verified, single hollow lining + measured
panes/handle fills) + testing-agent doc are shipped. **Open next:** new modules for untested classes (revolving
= radial, boom/turnstile = bar, trapdoor = horizontal, swing+fixed = unequal split); the deferred
pset golden-spec; a unified pipeline orchestrator per §3.

---

## 5b. Door converter v2 — built (golden-template-swap, 2026-06-26)

The second converter under the **go-forward §2a method** (door v1 kept as reference), built fresh to
mirror window v2 (§5a) module-for-module. `IFC Door Converter v2/`. Pipeline: **classify (Name) →
author the FormX golden via the shared recipe → inject measured dims + harvested colours → swap the
Body**.

- **FormX taxonomy = the PDF DOORS section** — **16 door types**, defined once in **`door_types.py`**
  (the single source of truth both `generate_goldens.py` and `classify_door.py` import — so "come
  back and update the doors later" = edit one table + re-run). Geometry archetypes: single-swing,
  double-swing, sliding, pocket, barn (track + rollers), shower (glazed), bifold (multi-panel),
  slide/swing combos, and DOOR_OPENING (cased opening, lining only).
- **Shared recipe `golden_door_geometry.py`** authors BOTH the 16 goldens and the converted doors →
  output == golden, scaled. Lining = `IfcRectangleHollowProfileDef` (`WallThickness` = drivable
  border); panels/mullions/rails/track/rollers/handles = `IfcRectangleProfileDef`. **Scale-correct:**
  all linear dims come from `dims_in_units(scale)` (canonical mm `CANON` → file units) — NO hard-coded
  mm in the build path; clamps keep features valid on narrow/odd doors. *(Both bugs the §7-trigger
  review caught — mm-globals-at-feet-scale would've made 32-ft handles; loose clamps gave negative
  panes on small subdivided doors — are fixed here.)*
- **Classification (`classify_door.py`)** = PDF `IfcRoot.Name` rules in priority order, tuned to the
  real names (OPENING · POCKET · BARN±SINGLE · bifold incl. real `four_fold` · SLIDING+PLY+GEM ·
  SINGLE+FLUSH · INTERIOR+DOUBLE · INTERIOR+SINGLE · DOUBLE+EXTERIOR · SHOWER · SLIDE+SWING · SLIDING ·
  → default SINGLE). Glazed from type default OR name "glass". **Deferred:** the side-by-side adjacency
  rule (→ DOOR_BIFOLDING_SWING_COMBO) — no such pair in the ADUs.
- **Faithful to the baked door:** measured W·H·D + harvested surface styles (the converted door
  "takes on the baked door's colours/dimensions", per the user). **Folding-depth clamp** (`MAX_FOLD_DEPTH_M`)
  for bi-fold/combo (the four-fold exports partly folded → bbox depth lies). Canonical handles authored
  (lever/pull); hardware is geometry, not a FormX param.
- **Preserve real handedness:** the occurrence `OperationType` keeps a meaningful Revit value
  (`SINGLE_SWING_RIGHT`, `…OPPOSITE_RIGHT`, …); the class-canonical op only fills a `NOTDEFINED` gap —
  so the occurrence never contradicts its preserved `IfcDoorType`. **Classifier hardened** against
  substring footguns (`opening` gated by leaf keywords; glazing descriptors like "double glazed"
  scrubbed before leaf-count rules). All-glass donor → synthesize opaque so the frame isn't see-through.
- **Occurrence-level apparatus, never a 2nd `IfcDoorType`** (§6): `Pset_DoorCommon` (Overall/Rough
  W·H, Depth) + `FormX_Door_Window` (HandFlipped/FacingFlipped) + `IfcDoorLiningProperties` + per-panel
  `IfcDoorPanelProperties` (lining/panel props authored **value-less** — entity + enums only — matching
  the flush FormX-native reference; dims ride on `Pset_DoorCommon` + `IfcDoor.OverallWidth/Height`, §6).
  **Swaps Body only** (match by `.id()`), preserves FootPrint/identity/relationships. Suffix `-D2`;
  marker `"FormX-D2 parametric door"`.
- **Lining = 4 solid `IfcRectangleProfileDef` bars, NOT a hollow profile** (§6): Gaudi mis-renders
  `IfcRectangleHollowProfileDef`, leaving a pane↔frame "space"; four plain bars render flush everywhere.
- **Per-schema quirks in `schema_adapter.py`** (IfcDoorType IFC4/4X3 vs IfcDoorStyle IFC2X3, style
  wrapping, semantics availability). USERDEFINED + `UserDefinedOperationType` for the slide/swing combos.

Result: goldens 16/16 author clean (**0 validate errors each**, full param set). Converter: **all 11
doors rebuilt** across the 4 ADUs (2/1/3/5), `verify()` ALL CHECKS PASSED, **0 new validate errors**,
0.0 mm face-bbox drift, identity/placement/FootPrint preserved, idempotent. Tester
(`test_door_converter_v2.py`) **4/4 fixtures, 276 checks, teeth-verified** — incl. a **layer G** that
pins the rebuilt FormX-type multiset (forcing every door to one type FAILS it, where it slipped the
count-only checks). **Gaudi pane↔frame "space" RESOLVED (2026-06-26, §6):** root cause was the
`IfcRectangleHollowProfileDef` lining — replaced by 4 solid bars; user confirmed `-D2` renders flush
in Gaudi. **Open:** FormX-architecture viewer review of the 16 goldens + `-D2` outputs (the agreed
ground truth); refine the first-pass simplifications (flat bifold/combo panels, barn 1-vs-2-leaf,
recessed pocket pull — see algorithm §4); HandFlipped/FacingFlipped derivation; adjacency-merge;
pipeline orchestrator.

---

## 5a. Window converter v2 — built (golden-template-swap, 2026-06-25)

The first converter built under the **go-forward §2a method** (window v1 is kept as reference).
`IFC Window Converter v2/`. Pipeline: **classify (Name) → author the FormX golden template (shared
recipe) → inject measured params → swap the Body**.

- **FormX taxonomy = the PDF** (*IFC Standardizer: Template Gallery categorizing*):
  `SINGLE_PANEL_WINDOW` × {FIXED, CASEMENT, AWNING, SLIDER, DOUBLE_HUNG} +
  `DOUBLE_HORIZONTAL_WINDOW` (vertical mullion, L/R) + `DOUBLE_VERTICAL_WINDOW` (horizontal transom,
  T/B) + `TRAPEZOID_WINDOW` (gated, no template yet). Classification is **name-keyword** driven and
  *structural* (it picks the golden → the geometry).
- **Grounding reality vs the PDF:** no opening hosts >1 window in any ADU → the PDF's "merge two
  adjacent windows into a DOUBLE" never fires; the DOUBLE windows are *single* `IfcWindow`s named
  `…-Double` → name-driven, adjacency-merge **deferred**. `DOUBLE_HUNG` is a SINGLE panel subtype
  (stacked sashes, horizontal transom), not a compound window. Skylight + trapezoid + bodiless
  `GeometricSet` → **gated** (preserved, flagged). No-operation-keyword → default **FIXED**.
- **Shared geometry recipe (`golden_geometry.py`)** is used by BOTH `generate_goldens.py` (writes
  the 7 reviewable `golden_templates/*.ifc`) and the converter → a converted window is *provably
  identical to its golden, scaled*. Frame = **4 solid `IfcRectangleProfileDef` bars** (head + sill
  full-width, two jambs spanning the inner height) — NOT a hollow profile, which Gaudi mis-renders
  (§6); panes/mullion/transom = `IfcRectangleProfileDef`; extruded along the measured depth axis.
  Same axis-role rule as the door converter (thinnest = depth; more-vertical face axis = height).
- **No second `IfcWindowType`.** These windows are already Revit-typed and `IfcRelDefinesByType` is
  `[0:1]` — minting a new type was the one bug found (a duplicate type per window failed `validate`
  in IFC4/4X3 only). Fix: author `Pset_WindowCommon` + `IfcWindowLiningProperties` +
  `IfcWindowPanelProperties` at the **occurrence** level via `IfcRelDefinesByProperties`
  (many-per-element). The lining/panel props are authored **value-less** (matching the flush
  FormX-native reference, §6); `Pset_WindowCommon` carries the PDF param contract (Overall/Rough W·H,
  Depth, PanelType(s), Split, HandFlipped/FacingFlipped=False default).
- **Per-schema quirks are centralized in `schema_adapter.py`** (the flagged locus, per the user's
  "modular + could have a divergent procedure per IFC type" steer): style wrapping, PredefinedType
  availability, `IfcWindowType` vs `IfcWindowStyle`. Every author helper degrades (skip+log).
- **Swaps the Body only** (match by `.id()`, §6), preserves `FootPrint`/identity/relationships +
  styles harvested from the baked window. Suffix `-WIN2`; marker `"FormX-WIN2 parametric window"`.

Result: `verify()` ALL CHECKS PASSED on all 4 ADUs — rebuilt **6/5/4/8** (LEXFORD trapezoid,
SAN_JUAN skylight, Sunflower bodiless gated), **0 new validate errors**, idempotent. Tester
(`test_window_converter_v2.py`) **4/4 fixtures, 394 checks, teeth-verified**.

**Gaudi pane↔frame "space" RESOLVED (2026-06-26, §6).** The earlier glazing-seating iterations
(recess→0, lapped→flush, full-height mullion) did NOT move it because the cause was not the glazing:
it was the **`IfcRectangleHollowProfileDef` frame** — Gaudi mis-renders the hollow ring's inner
opening. A 3-window side-by-side test (hollow frame = gap, 4 solid bars = flush, solid box = solid),
confirmed by the user in Gaudi, pinned it. **Fix:** `build_window_items` now authors the frame as 4
solid bars; user confirmed `-WIN2` renders flush.

**Open:** skylight/trapezoid templates; HandFlipped/FacingFlipped derivation; adjacency-merge;
pipeline orchestrator. (Gaudi gap + golden/`-WIN2` viewer review now resolved.)

---

## 6. Findings & gotchas (cross-cutting — apply to EVERY converter)

Hard-won, generalizable lessons (window converter was where they surfaced):

- **✅ ROOT-CAUSED (geometry) + FIXED (2026-06-26): the Gaudi pane↔frame "space" was the
  `IfcRectangleHollowProfileDef` frame, NOT properties.** Gaudi mis-renders the hollow profile —
  it draws the rectangular ring's inner opening LARGER than authored, leaving a uniform band between
  the lining and the pane. Blender/openIFC render the same mesh flush (and the repo README already
  noted openIFC *skips* hollow-profile window frames — same root issue). **Disproved hypotheses
  along the way (logged so we don't re-chase them):** (1) it is NOT a recess/lap — two earlier glaze
  changes (5 mm→0, lapped→flush) didn't move it; (2) it is NOT the lining/panel property *values* —
  authoring them value-less (matching the flush native `HUDSON_ADU.ifc`) did NOT close the gap; (3)
  it is NOT properties at all — a **geom-only** output (zero added props) still gapped, and the
  standalone goldens gapped. **The decisive test:** a 3-window diagnostic (`OUTPUT…/GEOM_DIAG_3windows.ifc`)
  — A=hollow-profile frame **gapped**, B=4-solid-bar frame **flush**, C=single solid box solid — the
  user confirmed in Gaudi. **Fix (both converters, shared recipe):** author the frame/lining as
  **FOUR solid `IfcRectangleProfileDef` bars** (head + sill full-width, two jambs spanning the inner
  height) instead of one hollow profile, in `golden_geometry.build_window_items` +
  `golden_door_geometry.build_door_items`. Mullion/transom now span the inner opening (meet the
  bars). Goldens regenerated (no hollow profiles; window single=5/double=7 solids, door single=6;
  7/7 + 16/16 validate clean); both converters `verify()` ALL PASS (0 new validate errors, rebuilt
  6/5/4/8 + 2/1/3/5); both testers 4/4 (their manipulability layers were rewritten: the frame is no
  longer a single drivable hollow profile, so `_is_manipulable` now asserts clean rect-only swept
  solids + an inset pane, and the resize check drives the SHARED recipe — building at W vs 1.5·W and
  asserting the lining grows with the border held constant). **CONFIRMED RESOLVED — the user verified
  a real converted `-WIN2`/`-D2` (in-wall, rotated) renders flush in Gaudi (2026-06-26).**
  **Incidental (NOT the gap fix):** along the way the lining/panel property *entities* were also made
  **value-less** (matching the flush native `HUDSON_ADU` — its lining/panel props are all-`None`; the
  FormX param contract rides on `IfcWindow/Door.OverallWidth/Height` + `Pset_*`), since valued props
  proved irrelevant to the gap. The temporary `FORMX_PROP_MODE` A/B toggle used to test the (wrong)
  property hypothesis has been removed — `schema_adapter.make_lining_props`/`make_panel_props` now
  author value-less unconditionally.
- **Units vary (feet / mm / m) — never assume.** Read `ifcopenshell.util.unit.calculate_unit_scale`
  per file and author in file units. (Real ADUs are in **feet**, scale 0.3048.)
- **The geom kernel returns vertices in METRES regardless of file units** — divide by unit scale
  to get file units.
- **The geom kernel returns nondeterministic EMPTY meshes on freshly-authored solids.** So in
  tests/verification, measure rebuilt geometry **analytically** (compute the bbox from profile dims
  + placement matrix), not by tessellation. (This bit hard while building the tester.)
- **Mapped representations are shared across instances.** Geometry often lives on a shared
  `IfcRepresentationMap` instanced via `IfcMappedItem` — **never edit it in place** (you'd change
  every instance). Give each converted element its own fresh, direct representation.
- **Removing an old representation safely:** the per-instance `IfcShapeRepresentation` is often also
  referenced by an `IfcPresentationLayerAssignment` — de-reference it there **first**, else you
  leave empty `Items` → schema error. Remove the per-instance items/shaperep/product-def-shape, but
  **never the shared `IfcRepresentationMap`**.
- **Swapping ONE representation while preserving siblings (e.g. keep `FootPrint`, replace `Body`):**
  don't rebuild the whole `IfcProductDefinitionShape` — edit `prod.Representations` in place. But
  **match the target shaperep by `.id()`, not Python `is`**: ifcopenshell returns a *fresh wrapper
  object* on every `.Representations` access, so an `is` comparison silently never matches → the
  list comprehension keeps the OLD shaperep, then cleanup removes it, leaving `Representations = ()`
  (→ `validate` "Not valid" + kernel "No suitable IfcRepresentation found"). Cost a debug cycle on
  the door converter. (Subtype trap nearby: `IfcRectangleHollowProfileDef` **is-a**
  `IfcRectangleProfileDef`, so test the hollow type FIRST when telling frame from leaf/pane.)
- **Surface styles live per representation item** (`IfcStyledItem → IfcSurfaceStyle`), not on the
  element. Fresh items have none → viewers render **gray**. Harvest the originals (bucket by
  `IfcSurfaceStyleRendering.Transparency`) and re-attach. IFC2X3 wraps styles in
  `IfcPresentationStyleAssignment`; IFC4/4X3 attach directly — reuse the original entities verbatim
  to stay schema-correct.
- **`validate` gate = output errors ≤ source errors**, not `== 0`. Real exports carry pre-existing
  `ifcopenshell.validate` errors; the contract is *introduce none*.
- **An occurrence can have only ONE type (`IfcRelDefinesByType` is `[0:1]` via the `IsTypedBy`
  inverse).** Revit-exported windows/doors are already typed, so authoring a *new* `IfcWindowType`
  + a 2nd `IfcRelDefinesByType` is invalid — it failed `validate` in IFC4/4X3 (silently fine in
  IFC2X3, where the validator doesn't enforce it). Author element-detail property sets
  (`Pset_*`, `IfcWindowLiningProperties`, `IfcWindowPanelProperties`) at the **occurrence** level
  via `IfcRelDefinesByProperties` (many-per-element) instead — no type needed, multiplicity-safe.
  (Cost a debug cycle on window converter v2.)
- **Schema-absent types:** some types don't exist in older schemas (e.g. `IfcLightFixture` is not in
  IFC2X3). Wrap `by_type` in `try/except RuntimeError`.
- **Local-frame orientation is solved empirically, not analytically.** Elements are rotated any
  which way; measure the element's own local bbox, take the thinnest axis as depth, author a
  symmetric shape filling that box. Don't reverse-engineer exporter axis conventions.
- **Some elements are exported NON-PLANAR — the "thinnest axis = depth" heuristic then lies.** A
  folding/bi-fold door is exported partly folded, so its bbox depth is the *folded projection*
  (~1.55 ft on the four-fold), not the leaf thickness — a naïve rebuild makes a 1.5-ft-thick door.
  Clamp depth for such classes (door converter `_door_depth`). Corollary: when you change the depth
  envelope on purpose (this clamp, or a proud handle), measure verify/test bbox drift on the
  **face plane** (the two largest axes), not all three.
- **A door body is a SOUP of many sub-solids with roles.** A per-class audit (measure each sub-
  solid's bbox; small-in-both-face-dims = hardware, long-thin = stile/rail, big = panel) reveals
  what each class actually contains — handles on French/pocket, per-leaf stiles on French/four-fold,
  a bare slab on flush singles. Use it to scope a rebuild; don't generalize from one door. The
  rebuild authors a *canonical* handle (promote-not-preserve) rather than copying baked hardware.
- **Gate edge shapes — preserve, don't flatten.** The neutral rectangular template would turn a
  trapezoid/arch into a rectangle. Use a **fill-ratio gate** (convex-hull silhouette ÷ bounding
  rect; < ~0.95 ⇒ non-rectangular) and leave those untouched, flagged. (Caught in Blender on
  LEXFORD's trapezoid window.)
- **Style/operation classification is cosmetic** — a keyword scan over the family/type Name is
  enough for the canonical Name + a binary semantic flag (e.g. WINDOW vs SKYLIGHT). A wrong label
  never affects geometry, so don't over-invest in a classifier. (Real exports rarely populate
  structured operation enums; the style lives in the Revit family Name string.)
- **Manipulability is testable in code, not just Blender.** Once geometry is a parametric profile,
  "make it wider" = set `profile.XDim`, keep `WallThickness`. Test it by actually doing that and
  asserting the border stays constant — deterministic, no viewer needed. Blender stays a rare
  human spot-check.

---

## 7. Decisions log

| Decision | Rationale | Status |
|---|---|---|
| **Go-forward: golden-template-swap supersedes measure-and-rebuild (project-wide)** | Team confirmed FormX has an element-type catalog + golden template IFCs. Classify → instantiate FormX's template + inject params *matches* FormX's catalog; code-authored neutral templates only *approximate* it (window v1 / door v2.1 kept as reference, not deleted). New window converter built fresh under this method. | **Active (set 2026-06-25)** |
| **Window v2: golden geometry = a SHARED code recipe (not runtime entity-transplant from the .ifc)** | The 7 golden `.ifc`s are the reviewable contract; `golden_geometry.py` authors them AND the converted instances, so output == golden scaled. Robust across IFC2X3/4/4X3 + feet/mm (cross-schema entity transplant is brittle). Modular, with `schema_adapter.py` as the per-IFC-type locus. (User steer: "proceed with what works, but modular + keep per-IFC-type divergence in mind.") | **Active (set 2026-06-25)** |
| **Window v2: author FormX params at OCCURRENCE level, never a 2nd `IfcWindowType`** | Revit windows are already typed; `IfcRelDefinesByType` is `[0:1]`. `Pset_WindowCommon` + lining/panel props attach via `IfcRelDefinesByProperties` (many-per-element). The PDF contract is the Pset + Name, not a type entity. | **Active (set 2026-06-25)** |
| **Window v2 edge dispositions: skylight + trapezoid + bodiless → gate; no-keyword → FIXED** | Skylight/trapezoid aren't FormX parametric types yet (PDF); bodiless `GeometricSet` has no readable solid → preserve+flag, don't corrupt. FIXED is the safe default panel. | **Active (set 2026-06-25)** |
| **Door v2: built mirroring window v2, with `door_types.py` as the single source of truth** | Both `generate_goldens.py` + `classify_door.py` import one 16-type table → the catalog is edited in one place ("come back and update the doors later"). Same shared-recipe / occurrence-Pset / Body-swap / verify+teeth backbone. | **Active (set 2026-06-26)** |
| **Door v2: recipe is scale-correct via `dims_in_units(scale)` (no mm in the build path)** | The shared recipe is driven by the converter at feet scale; hard-coded mm constants would've authored 32-ft handles + loose clamps gave negative panes on narrow subdivided doors (both caught by the adversarial golden review). All linear dims flow from a canonical mm table converted to file units. | **Active (set 2026-06-26)** |
| **Door v2: author all 16 PDF types as goldens + model canonical handles; clean-&-simplified first pass** | User decisions (2026-06-26): one golden per type (parity); handles authored (door v1 viewer regression was missing handles) though not a FormX param; bifold/combo flat, barn = track+rollers, pocket pull proud, DOOR_OPENING = cased opening — all flagged for the FormX-architecture viewer review to refine. | **Active (set 2026-06-26)** |
| **Window + Door v2: frame/lining = 4 solid bars, NOT `IfcRectangleHollowProfileDef`** | Gaudi mis-renders the hollow profile (draws its inner opening larger → a uniform pane↔frame "space"); Blender/openIFC render it flush (openIFC *skips* it outright). A 3-window side-by-side test (hollow=gap, 4 bars=flush) confirmed by the user in Gaudi pinned it. Four plain `IfcRectangleProfileDef` bars render flush everywhere. The disproven property hypotheses (valued vs value-less lining/panel props) are logged in §6 so they aren't re-chased. | **Active (set 2026-06-26)** |
| **Window + Door v2: lining/panel property sets authored VALUE-LESS** | The flush FormX-native reference (`HUDSON_ADU`) carries lining/panel props with all numeric fields null; the dimension contract rides on `IfcWindow/Door.OverallWidth/Height` + `Pset_*`. Value-less matches native and (proven) doesn't affect the Gaudi gap either way. The temporary `FORMX_PROP_MODE` A/B toggle used to test this was removed once geometry was found to be the real cause. | **Active (set 2026-06-26)** |
| **Match the proven recipe (clean geometry + Name + PredefinedType + relations), NOT rich type/Pset apparatus** | Gal's production tools author zero Psets/element-types; the richer "golden-spec" was Claude-authored, unverified against FormX, and v1 worked without it. Lower risk, more likely to drop into FormX. | **Superseded by the 2026-06-25 pivot** (was the v1 stance) |
| **Converters are self-contained (ifcopenshell only)** | Removed the window converter's imports of the old `classify.py`/`bakedness.py` (now archived). They were used only for a cosmetic Name + an informational log number — both inlined. New converters should follow suit: no dependency on `Old Context/`. | **Active (set 2026-06-23)** |
| **Rebuild from the element's OWN measured local bbox; preserve GlobalId + placement in place** | Orientation-agnostic; keeps the element in its opening; only the element's representation/Name/PredefinedType change → the opening/fill/void/containment chain stays valid. | Active |
| **Carry surface styles forward; gate edge shapes (preserve, don't flatten)** | New items render gray without styles; the rectangular template would corrupt odd shapes. | Active |
| **I/O: batch `INPUT/` → `OUTPUT/`, per-stage suffix, single-file args; target schema = whatever the input is** | Honors the repo folders + Gal's CLI shape; suffixes compose; real ADUs vary by schema (IFC2X3/IFC4/IFC4X3). | Active |
| **Done = "open + look right" (human) + structural `verify()` + automated tester with teeth** | No FormX-side acceptance spec exists; layered internal checks + a negative control are the gate, the viewer check is ground truth. | Active |
| **Golden-spec richness (IfcWindowType + lining/panel/operation enums + Pset + per-style topology)** | Was deferred/archived pending a trigger: "FormX confirms it needs operation/panel semantics." **Trigger fired 2026-06-25** — team confirmed FormX's window-type catalog + golden templates. The archived goldens (`Old Context/FormX 6.22 IFC Generated/`, §8) are now the prototype reference for the golden-template-swap converter. | **Promoting (trigger fired)** |

---

## 8. Old Context (research archive — now the PROTOTYPE REFERENCE for the golden-template pivot)

Everything in `Old Context/` is the research phase that *informed* the v1 window converter. It was
"not a dependency / ignorable" under the v1 measure-and-rebuild approach — **but the 2026-06-25
golden-template pivot makes it directly relevant**: the authored goldens + style taxonomy below are
the starting reference for the new converter. Read it before building the new window converter.

- **`Old Context/Form X 6.22 IFC Survey/`** — the research survey of real-world IFC files +
  `classify.py` (style classifier), `bakedness.py` (Parametric Integrity Score), `style_registry.csv`
  (12-style window taxonomy), `window_file_survey.csv`. Headline findings that shaped the recipe (all
  now baked into §2/§6): operational style is almost never machine-declared (88% of 208 surveyed
  windows were UNCLASSIFIED — style lives in the Revit family Name, multilingual); "Revit bakes
  everything" is schema-version-dependent (2011 IFC2x3 kept swept solids; modern IFC4 exports are
  `IfcAdvancedBrep`); the promote-not-reconstruct strategy (dimensions + a style hint usually survive
  even when geometry is baked).
- **`Old Context/FormX 6.22 IFC Generated/`** — 12 authored "golden target" parametric window IFCs +
  `author_goldens.py` (`IfcWindowType` + lining/panel/operation enums + `Pset_WindowCommon` +
  per-style mullion topology). **★ Now the prototype reference** for the golden-template-swap method:
  these are essentially golden template IFCs + the authoring patterns to instantiate them. The new
  window converter should study these (and reconcile with FormX's actual catalog/naming standard).
  `style_registry.csv` (12-style window taxonomy, in `Form X 6.22 IFC Survey/`) is the matching
  classifier vocabulary to build on.
- **`Old Context/FormX 6.22 Random Online IFC files/`** — random downloaded IFCs used for the survey.

Status: the golden-spec promotion trigger **fired 2026-06-25** (see §7) — this is where the new
golden-template work resumes, reconciled against FormX's confirmed window-type catalog + templates.

---

## 9. Glossary

- **Golden-template-swap (go-forward)** — §2a: classify a baked element into a FormX type, load that
  type's golden template IFC, inject params, swap it in. The project's go-forward method (2026-06-25).
- **The recipe (v1)** — the §2b ten-point measure-and-rebuild pattern (window v1 / door v2.1).
- **Promote vs. reconstruct** — promote: keep surviving identity/geometry/Name and put it onto a
  clean parametric form (v1: a code-authored template; go-forward: FormX's golden template).
  Reconstruct: infer parameters from a raw baked mesh (avoided in both).
- **Stage suffix** — per-converter output tag (`-W1`/`-L1`/`-F1`/`-WIN1`/`-D1`…); they compose along
  the pipeline.
- **Teeth (negative control)** — a test that must FAIL on a no-op converter, proving the suite can
  detect a non-working converter (the baked originals fail the manipulability check).
- **Golden-spec / golden template** — FormX's clean parametric per-type IFC the converter
  instantiates (go-forward); prototyped in `Old Context/FormX 6.22 IFC Generated/` (§8). **PIS /
  style_code** — research vocabulary from the survey; see `Old Context/` (§8).
