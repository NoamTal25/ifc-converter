# FormX IFC Converter

Turn Revit-exported building IFCs into **clean, parametric, manipulable** geometry that FormX's
chat-driven design tool can actually edit — *"make this window wider"* needs a live width parameter
to grab, not a frozen mesh.

This repo ships the **Window Converter (v2)** and the **Door Converter (v2)** as its proven,
go-forward components — both built on the same golden-template-swap method. They're pieces of a
larger, composable pipeline (see [The broader picture](#the-broader-picture)).

---

## The problem in one paragraph

FormX builds AI-driven modular homes. Designs are authored in Revit (parametric internally), but
Revit's IFC **export bakes that intelligence away** — the recipe is lost and only frozen geometry
(Brep / mesh) survives. *"Make it wider"* has nothing to grab. Each converter in this repo undoes
that for **one element type**, rebuilding the baked geometry into clean parametric form while
preserving the element's identity and every relationship around it.

---

## What the Window Converter does

For every `IfcWindow` in an exported ADU, the converter:

1. **Classifies** it into one of FormX's defined window types from its name (FormX's *Template
   Gallery* taxonomy).
2. **Rebuilds** it as that type's clean parametric **golden template**, sized to the window's own
   measured dimensions.
3. **Swaps** the baked geometry for the parametric rebuild — keeping the window's `GlobalId`,
   placement, host-wall opening, spatial containment, surface colors, and `FootPrint` untouched.
4. **Stamps** the FormX parameter set (dimensions, panel type, flip flags, …) onto the window so
   downstream tooling can drive it.

The result: a window whose width/height are live parameters, that sits exactly where it did, looks
the same, and carries its FormX semantics — instead of an inert baked solid.

> **Faithful, not pixel-identical.** Overall size, position, colors, and FormX params are matched;
> the rebuilt shape is FormX's clean canonical template (lining + panes ± mullion/transom), not a
> reproduction of every Revit detail.

### FormX window taxonomy (what it can produce)

| FormX type | Geometry | Panel subtypes |
|---|---|---|
| `SINGLE_PANEL_WINDOW` | lining + 1 pane | Fixed · Casement · Awning · Slider · Double-hung |
| `DOUBLE_HORIZONTAL_WINDOW` | lining + **vertical mullion** + left/right panes | per panel |
| `DOUBLE_VERTICAL_WINDOW` | lining + **horizontal transom** + top/bottom panes | per panel |
| `TRAPEZOID_WINDOW` | *gated* — no parametric template yet → left untouched | — |

Windows it can't faithfully template (trapezoid, skylight, non-rectangular, or geometry it can't
read) are **gated**: left exactly as-is and flagged, never corrupted.

---

## Quick start

> **Requires `python3.11` with `ifcopenshell` 0.8.5.** Plain `python3` won't work on this machine.
> Keep the quotes around paths — the folder names contain spaces.

### 1. Generate / regenerate the golden templates (optional — already committed)

```bash
python3.11 "IFC Window Converter v2/generate_goldens.py"
```
Writes the 7 reviewable template IFCs to `IFC Window Converter v2/golden_templates/`. Open them in
any IFC viewer to inspect the canonical shapes.

### 2. Convert your ADUs

Drop source `.ifc` files into `INPUT_IFC_FILES_HERE/`, then:

```bash
# Batch: every file in INPUT_IFC_FILES_HERE/ → OUTPUT_IFC_FILES_HERE/<name>-WIN2.ifc
python3.11 "IFC Window Converter v2/IFC_window_converter_V2.py"

# Or a single file:
python3.11 "IFC Window Converter v2/IFC_window_converter_V2.py" path/to/in.ifc
python3.11 "IFC Window Converter v2/IFC_window_converter_V2.py" path/to/in.ifc path/to/out.ifc
```

Output gets the `-WIN2` suffix. The original is **never modified** (the converter works on a copy).
A built-in `verify()` runs after every conversion and prints a pass/fail report.

> **Viewer note:** outputs render correctly in Blender, ifcopenshell, and full IFC kernels. The
> web-based **openIFC** viewer skips the hollow-profile window frames, so converted windows may look
> blank there — a renderer limitation, not a data problem.

---

## How it works (architecture)

The converter is **modular** — geometry, classification, schema quirks, and orchestration are
separated so each can change independently. Everything is **self-contained** (depends only on
`ifcopenshell`).

```
IFC Window Converter v2/
├── generate_goldens.py        → writes the 7 golden templates (for review)
├── golden_templates/*.ifc     the reviewed canonical shapes + parameter contract
├── golden_geometry.py    ★    the SHARED parametric recipe
├── classify_window.py         name → FormX-type recipe
├── schema_adapter.py          per-IFC2X3 / IFC4 / IFC4X3 differences
├── IFC_window_converter_V2.py main converter + built-in verify()
└── test_window_converter_v2.py + WINDOW_CONVERTER_V2_TESTING_AGENT.md
```

The key idea: **`golden_geometry.py` is used by both the golden generator and the converter.** So a
converted window is *provably identical* to its golden template, just scaled to the instance — the
templates aren't a separate spec that can drift, they're the same code.

**Per-window flow:**

1. **Measure** the window in its own local frame → width, height, depth (thinnest axis = through-wall;
   the more-vertical face axis = height, so mullions/transoms orient correctly under any rotation).
2. **Classify** by name (`classify_window.py`): ordered keyword rules pick the FormX type + panel
   type(s) + split (single / vertical-mullion / horizontal-transom). No keyword → defaults to Fixed.
3. **Gate** anything non-rectangular, bodiless, skylight, or trapezoid → preserve + flag.
4. **Author** the geometry via `golden_geometry.py` in the file's own units & schema — a hollow
   lining (`IfcRectangleHollowProfileDef`, whose wall thickness is the drivable frame border) plus
   inset glazing panes and any mullion/transom bar.
5. **Carry styles** — harvest the original window's surface colors (glass = transparent, frame =
   opaque) and re-attach them, so nothing renders gray.
6. **Swap** only the `Body` representation in place, preserving `FootPrint`, `GlobalId`, placement,
   and the opening→fill→void→host chain. Stamp `Pset_WindowCommon` (overall/rough dims, panel type,
   split, hand/facing-flipped) + lining/panel properties at the occurrence level.

**Schema handling.** Real ADUs come in IFC2X3, IFC4, and IFC4X3, in feet. `schema_adapter.py` is the
single place that knows the differences (style wrapping, attribute availability, value types) — the
intended home for any future per-schema special-casing.

A deeper, step-by-step spec lives in
[`IFC Window Converter v2/IFC window converter v2 algorithm.md`](IFC%20Window%20Converter%20v2/IFC%20window%20converter%20v2%20algorithm.md).

---

## Testing

### Run the tester

```bash
python3.11 "IFC Window Converter v2/test_window_converter_v2.py"        # all fixtures
python3.11 "IFC Window Converter v2/test_window_converter_v2.py" -v     # show every check
python3.11 "IFC Window Converter v2/test_window_converter_v2.py" one.ifc # single file
```

It converts each fixture in `INPUT_IFC_FILES_HERE/` into a throwaway temp (never touches
`OUTPUT_IFC_FILES_HERE/`), re-derives every invariant from scratch, and prints `PASS`/`FAIL` per
fixture with an `ALL PASS (n/n)` summary. **Exit code 0 = all pass**, non-zero = a failure (handy
for scripting). It's safe to run repeatedly.

### What the tester checks (6 layers)

| Layer | Asserts |
|---|---|
| **A — Conservation** | Counts of every non-window element & relationship unchanged; window `GlobalId`s identical; fill/void edges identical. |
| **B — Preservation** | Openings unmoved; rebuilt windows keep their exact placement; `FootPrint` preserved; **source file byte-identical** after the run. |
| **C — Manipulable state** | Each rebuilt window = one hollow lining (border > 0) + ≥1 inset pane (+ optional bars), every part styled. |
| **D — Manipulation** | Drive the lining width/height ×1.5 → border stays constant, window grows on that axis only. Move (rigid). Rotate (geometry intact). No new validate errors after each edit. |
| **E — Idempotency** | Re-running the converter on its own output changes nothing. |
| **F — Teeth** | The *same* manipulable test on the **original baked windows must fail** (a brep has no drivable parameter), and the rebuilt count must match a pinned per-fixture baseline — so a no-op or silent regression can't pass. |

Manipulation is measured **analytically** (from the profile + placement), not by tessellation,
because the geometry kernel returns nondeterministic empty meshes on freshly-authored solids.

The viewer check (open the goldens + outputs and look) remains the final ground truth; the testing
methodology is documented in
[`IFC Window Converter v2/WINDOW_CONVERTER_V2_TESTING_AGENT.md`](IFC%20Window%20Converter%20v2/WINDOW_CONVERTER_V2_TESTING_AGENT.md).

### Current results

`verify()` passes on all 4 real ADUs (IFC2X3 / IFC4 / IFC4X3) with **0 new validate errors**; the
tester passes **4/4 fixtures (394 checks)**, teeth verified. Rebuilt counts: 6 / 5 / 4 / 8 (the
remainder being correctly-gated trapezoid, skylight, and bodiless windows).

---

## The Door Converter (v2)

Same method, same architecture, applied to `IfcDoor`. It classifies each baked door from its name
into one of FormX's **16 door types** (the *Template Gallery* DOORS taxonomy), rebuilds it as that
type's clean parametric golden template — sized to the door's measured dimensions and **coloured from
the door's own harvested surface styles** — and swaps only the `Body`, preserving identity, placement,
the host-wall opening chain, and `FootPrint`. Output suffix `-D2`.

```
IFC Door Converter v2/
├── door_types.py          ★ single source of truth — the 16 FormX door types (edit here)
├── generate_goldens.py      → writes the 16 golden templates (for review)
├── golden_templates/*.ifc   the reviewable canonical shapes + parameter contract
├── golden_door_geometry.py ★ the SHARED, scale-correct parametric recipe
├── classify_door.py         name → FormX-type recipe (PDF rules, tuned to real names)
├── schema_adapter.py        per-IFC2X3 / IFC4 / IFC4X3 differences
├── IFC_door_converter_V2.py main converter + built-in verify()
└── test_door_converter_v2.py + DOOR_CONVERTER_V2_TESTING_AGENT.md
```

The 16 types span single-swing, double-swing, sliding, pocket, barn (track + rollers), shower (glass),
bifold (multi-panel), slide/swing combos, and `DOOR_OPENING` (a leafless cased opening). To add or
retune a type, edit `door_types.py` and re-run `generate_goldens.py` — both the generator and the
classifier read that one table.

> **First pass — refine after viewer review.** Bi-fold / combo leaves are flat (not articulated),
> the barn track is a straight bar + roller tabs, and the pocket pull is proud rather than recessed.
> These simplifications are flagged in the algorithm doc for the FormX-architecture review.

**Run it:**
```bash
python3.11 "IFC Door Converter v2/generate_goldens.py"          # (re)write the 16 goldens
python3.11 "IFC Door Converter v2/IFC_door_converter_V2.py"     # batch INPUT → OUTPUT (-D2)
python3.11 "IFC Door Converter v2/test_door_converter_v2.py"    # 6-layer tester (teeth)
```

**Current results:** goldens 16/16 validate clean; `verify()` passes on all 4 ADUs (all 11 doors
rebuilt: 2 / 1 / 3 / 5) with 0 new validate errors; the tester passes **4/4 fixtures (276 checks)**,
teeth verified — including a classification layer that pins the rebuilt FormX-type multiset (so a
misclassification can't slip through). Spec: [`IFC Door Converter v2/IFC door converter v2 algorithm.md`](IFC%20Door%20Converter%20v2/IFC%20door%20converter%20v2%20algorithm.md).

---

## The broader picture

The window converter is **one component of a composable "fix any building IFC" pipeline** — one
converter per element type, each obeying the same contract: *touch only your element type, preserve
everything else exactly, leave clean parametric geometry behind.* They chain via output suffixes
(`-WIN2`, `-D1`, …), so a file can pass through several stages.

| Component | Location | Status |
|---|---|---|
| **Window converter** | `IFC Window Converter v2/` | ✅ Active — golden-template-swap (this README) |
| **Door converter** | `IFC Door Converter v2/` | ✅ Active — golden-template-swap, 16 FormX door types (suffix `-D2`) |
| Door converter v1 | `reference/IFC Door Converter v1/` | 🔧 Reference — classification-driven, measure-and-rebuild (superseded by v2) |
| Walls / levels / floors | `reference/Gal_Similar_Project_Refrences/` | Reference — the design template these tools follow |
| *Pipeline orchestrator* | — | Planned — run the per-element converters in dependency order |

The go-forward method (used by the window converter) is **classify → instantiate FormX's golden
template → inject params → swap**, which makes outputs *match* FormX's catalog. Earlier converters
(window v1, door v2.1) used **measure-and-rebuild**, which authored a neutral template in code that
only *approximates* the catalog — kept as working reference.

---

## Repo layout

```
ifc-converter/
├── README.md                    ← you are here
├── CLAUDE.md                    project memory / playbook (the "why")
├── INPUT_IFC_FILES_HERE/        drop source ADUs here (batch input + test fixtures)
├── OUTPUT_IFC_FILES_HERE/       converter outputs (gitignored)
├── IFC Window Converter v2/     ACTIVE — the window converter
├── IFC Door Converter v2/       ACTIVE — the door converter (golden-template-swap, 16 types)
└── reference/
    ├── IFC Window Converter v1/        superseded by v2 (measure-and-rebuild)
    ├── IFC Door Converter v1/          door v1 (measure-and-rebuild) — superseded by door v2
    ├── Gal_Similar_Project_Refrences/  walls / levels / floors tools (design template)
    └── Old Context/                    research + golden-spec prototype (see below)
```

---

## A note on `reference/Old Context/`

The research phase that informed all of this lives in `reference/Old Context/`: a survey of
real-world window IFCs, a style classifier and a "parametric integrity" scorer, a 12-style window
taxonomy, and **12 hand-authored "golden target" window IFCs** (`author_goldens.py`). That prototype
— clean parametric windows with full type + property apparatus — is the conceptual ancestor of the
golden-template method this converter now implements. It's reference material, not a dependency.

For the full design history, decisions log, and hard-won IFC gotchas, see
[`CLAUDE.md`](CLAUDE.md).
