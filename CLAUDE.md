# FormX IFC Converter — Project Context

**What this is.** A growing set of per-component IFC "converters" that rebuild Revit-exported
building elements into clean, **parametric, manipulable** geometry for FormX — *one converter
per element type*, designed to compose into a single "fix any building IFC" pipeline. The
**window converter is the proven reference implementation**; the **door converter is next**
(`IFC Door Converter/`, empty). This file is the generalized playbook **and** the project memory.

**Status.** Window converter v1: built, **self-contained** (depends on ifcopenshell only),
validated on every real ADU (IFC2X3 / IFC4 / IFC4X3), shipped with an automated manipulability
tester (teeth-verified). Door: not started. Gal's three sibling tools (walls/levels/floors) are
the original design template — see §4.

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

**Core strategy — promote/measure-and-rebuild, never reverse-engineer a mesh.** We don't try to
infer parameters out of a triangle soup. We measure the element's real extents, then author clean
parametric geometry that occupies the same space — preserving identity and relationships.

**Open question (unconfirmed with CTO):** does FormX only get post-export IFC, or could it read
source `.rvt`? Direct `.rvt` parameter extraction would bypass the baking problem entirely. Treat
as a blocking scoping question, not a settled assumption.

---

## 2. THE CONVERTER RECIPE — the generalized process every converter follows

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

The next element is **the Door** (`IFC Door Converter/`). General procedure for any component:

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

| Path | What it is | Status |
|---|---|---|
| `IFC Window Converter/IFC_window_converter_V1.py` | **The reference converter.** Rebuilds each `IfcWindow` into a clean parametric hollow-frame + pane from its own measured bbox; preserves GlobalId/placement/relationships + surface styles; canonical Name + `PredefinedType`; gates non-rectangular + unreadable windows. **Self-contained (ifcopenshell only).** Batch INPUT→OUTPUT `-WIN1`, also single-file args. | Built; all fixtures pass |
| `IFC Window Converter/IFC window converter algorithm.md` | Living spec (Gal's doc structure). | Current |
| `IFC Window Converter/test_window_converter.py` + `WINDOW_CONVERTER_TESTING_AGENT.md` | **Automated manipulability tester** + its subagent spec. Runs the converter on every `INPUT/` fixture in throwaway temps, re-derives invariants independently (does NOT call the converter's `verify()`), and **actually manipulates each rebuilt window** (parametric resize/move/rotate) — asserting frame border stays constant, moves rigidly, stays valid. **Kernel-free** (analytic bbox from profile+placement — the geom kernel returns nondeterministic empty meshes on fresh solids). Teeth: same test on baked originals MUST fail + pinned `BASELINE_REBUILT`. Run `python3.11 test_window_converter.py`. | 4/4 fixtures pass; teeth verified |
| `IFC Door Converter/` | **Next component — empty.** | Not started |
| `Gal_Similar_Project_Refrences/` | Gal's three production tools (walls cleanup / levels organizer / floors definer) + their algorithm.md & testing docs. The **design template** (CLI shape, built-in `verify()`, "only-touch-your-element" discipline, testing methodology). | Reference |
| `INPUT_IFC_FILES_HERE/` | Real FormX ADUs — the converter's batch input **and** the tester's fixture corpus: `LEXFORD_OFFICE-C1` (IFC2X3), `SAN_JUAN_CYPRESS…-W1-L1` (IFC4X3, already through walls+levels), `Sunflower_A` (IFC2X3), `Turnberry…-C1` (IFC4). | Active |
| `OUTPUT_IFC_FILES_HERE/` | Converter outputs (`-WIN1` etc.), gitignored. | — |
| `Old Context/` | Pre-converter research + the deferred golden-spec. **Not needed to build new converters** — see §8. | Archived |

---

## 5. Door — starting notes (brief)

- **~11 `IfcDoor` across the ADUs** (schema-safe; also `IfcDoorStandardCase`). Like windows, a
  door **fills an `IfcOpeningElement`** voided into a wall (`IfcRelFillsElement → IfcOpeningElement
  → IfcRelVoidsElement → IfcWall`) — so the window converter's opening-preservation + measure-and-
  rebuild pattern **transfers almost directly**.
- Differences to design for: a door is a leaf + frame (often a panel that swings), `OverallWidth`/
  `OverallHeight`, swing/operation in the name; bottom usually meets the floor (sill at 0), unlike
  windows. Suffix `-D1` is free.
- Start by scanning the real doors (geometry kind, schemas, units, opening linkage), then clone the
  window spine. Keep style/operation cosmetic (inline keyword scan), as with windows.

---

## 6. Findings & gotchas (cross-cutting — apply to EVERY converter)

Hard-won, generalizable lessons (window converter was where they surfaced):

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
- **Surface styles live per representation item** (`IfcStyledItem → IfcSurfaceStyle`), not on the
  element. Fresh items have none → viewers render **gray**. Harvest the originals (bucket by
  `IfcSurfaceStyleRendering.Transparency`) and re-attach. IFC2X3 wraps styles in
  `IfcPresentationStyleAssignment`; IFC4/4X3 attach directly — reuse the original entities verbatim
  to stay schema-correct.
- **`validate` gate = output errors ≤ source errors**, not `== 0`. Real exports carry pre-existing
  `ifcopenshell.validate` errors; the contract is *introduce none*.
- **Schema-absent types:** some types don't exist in older schemas (e.g. `IfcLightFixture` is not in
  IFC2X3). Wrap `by_type` in `try/except RuntimeError`.
- **Local-frame orientation is solved empirically, not analytically.** Elements are rotated any
  which way; measure the element's own local bbox, take the thinnest axis as depth, author a
  symmetric shape filling that box. Don't reverse-engineer exporter axis conventions.
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
| **Match the proven recipe (clean geometry + Name + PredefinedType + relations), NOT rich type/Pset apparatus** | Gal's production tools author zero Psets/element-types; the richer "golden-spec" was Claude-authored, unverified against FormX, and v1 worked without it. Lower risk, more likely to drop into FormX. | Active |
| **Converters are self-contained (ifcopenshell only)** | Removed the window converter's imports of the old `classify.py`/`bakedness.py` (now archived). They were used only for a cosmetic Name + an informational log number — both inlined. New converters should follow suit: no dependency on `Old Context/`. | **Active (set 2026-06-23)** |
| **Rebuild from the element's OWN measured local bbox; preserve GlobalId + placement in place** | Orientation-agnostic; keeps the element in its opening; only the element's representation/Name/PredefinedType change → the opening/fill/void/containment chain stays valid. | Active |
| **Carry surface styles forward; gate edge shapes (preserve, don't flatten)** | New items render gray without styles; the rectangular template would corrupt odd shapes. | Active |
| **I/O: batch `INPUT/` → `OUTPUT/`, per-stage suffix, single-file args; target schema = whatever the input is** | Honors the repo folders + Gal's CLI shape; suffixes compose; real ADUs vary by schema (IFC2X3/IFC4/IFC4X3). | Active |
| **Done = "open + look right" (human) + structural `verify()` + automated tester with teeth** | No FormX-side acceptance spec exists; layered internal checks + a negative control are the gate, the viewer check is ground truth. | Active |
| **Golden-spec richness (IfcWindowType + lining/panel/operation enums + Pset + per-style topology) deferred, archived in `Old Context/`** | Value is conditional on FormX consuming operation semantics — unverified, and v1 worked without it. Promote only on trigger (FormX confirms it needs operation/panel semantics, or a viewer test shows mullions matter). | **Deferred** |

---

## 8. Old Context (archive — NOT needed to build new converters)

Everything in `Old Context/` is the research phase that *informed* the window converter but is **not
a dependency** of it (the converter is self-contained). A new-converter author can ignore all of this.

- **`Old Context/Form X 6.22 IFC Survey/`** — the research survey of real-world IFC files +
  `classify.py` (style classifier), `bakedness.py` (Parametric Integrity Score), `style_registry.csv`
  (12-style window taxonomy), `window_file_survey.csv`. Headline findings that shaped the recipe (all
  now baked into §2/§6): operational style is almost never machine-declared (88% of 208 surveyed
  windows were UNCLASSIFIED — style lives in the Revit family Name, multilingual); "Revit bakes
  everything" is schema-version-dependent (2011 IFC2x3 kept swept solids; modern IFC4 exports are
  `IfcAdvancedBrep`); the promote-not-reconstruct strategy (dimensions + a style hint usually survive
  even when geometry is baked).
- **`Old Context/FormX 6.22 IFC Generated/`** — 12 authored "golden target" parametric window IFCs +
  `author_goldens.py`. This is the **deferred golden-spec** (`IfcWindowType` + lining/panel/operation
  enums + `Pset_WindowCommon` + per-style mullion topology). Validated in isolation but **more than
  the working system is shown to need**; parked (see §7 deferred row) with an explicit promotion
  trigger. The window converter does **not** use it.
- **`Old Context/FormX 6.22 Random Online IFC files/`** — random downloaded IFCs used for the survey.

If FormX later confirms it consumes operation-type/panel semantics, the golden-spec is where that
work resumes — until then it stays archived.

---

## 9. Glossary

- **The recipe** — the §2 ten-point pattern every converter follows.
- **Promote vs. reconstruct** — promote: measure surviving geometry/Name and rebuild onto a clean
  parametric template (what we do). Reconstruct: infer parameters from a raw baked mesh (avoided).
- **Stage suffix** — per-converter output tag (`-W1`/`-L1`/`-F1`/`-WIN1`/`-D1`…); they compose along
  the pipeline.
- **Teeth (negative control)** — a test that must FAIL on a no-op converter, proving the suite can
  detect a non-working converter (the baked originals fail the manipulability check).
- **Golden-spec / PIS / style_code** — archived research vocabulary; see `Old Context/` (§8).
