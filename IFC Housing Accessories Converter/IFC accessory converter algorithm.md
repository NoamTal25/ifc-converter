# IFC Housing Accessories Converter v1 — Algorithm

**Method: PRESERVE-AND-TAG.** A deliberately simple sibling of the golden-template-swap window/door
converters. It handles the non-structural **accessory** objects in an ADU — furniture, plants, wall
lights, plumbing fixtures, appliances, decor — and makes each one a clean, self-contained **whole
object** that FormX can **move** and **replace** (swap for a catalog accessory), while it keeps
**looking exactly as exported**.

> **HEADLINE GUARANTEE — ZERO visual change.** The baked mesh, surface styles and `ObjectPlacement`
> are kept verbatim. The converter authors NO geometry. The ONLY change per accessory is a single
> occurrence-level `FormX_Accessory` property set (+1 `IfcPropertySet`, +1
> `IfcRelDefinesByProperties`, +6 `IfcPropertySingleValue`). Nothing about how an object renders
> changes.

Self-contained (ifcopenshell only). `python3.11` / ifcopenshell 0.8.5. Stage suffix **`-ACC1`**.

---

## Step 1 — Scan + de-duplicate the candidate pool

The accessory **root classes** (`accessory_types.ROOTS`) — `by_type` already returns subtypes:

| Root | Subtypes pulled | In ADUs |
|---|---|---|
| `IfcFurnishingElement` | `IfcFurniture` (IFC4/4X3) | chairs, sofas, tables, desks, cabinets, counters, casework, beds, wardrobes, bookcases, TVs, vanities, picture frames |
| `IfcBuildingElementProxy` | — | plants, appliances, mirrors, niches, "Generic Models", "Specialty Equipment", laptop (+ trim/text → gated) |
| `IfcFlowTerminal` | `IfcLightFixture`, `IfcSanitaryTerminal` (IFC4+) | toilets, lavatories, sinks, washer/dryers, shower heads/mixers, wall lights |

- Each `by_type` is wrapped in `try/except RuntimeError` — `IfcLightFixture` / `IfcSanitaryTerminal`
  / `IfcFurniture` do **not** exist in IFC2X3 (CLAUDE.md §6).
- **De-dup by `.id()`.** A light fixture is reachable both as `IfcLightFixture` and via
  `IfcFlowTerminal`; a furniture both as `IfcFurniture` and `IfcFurnishingElement`. Without dedup it
  would be tagged 2–3× (34 overlap hits in Turnberry).

## Step 2 — Gate (preserve untouched + log; never corrupt)

Per candidate, in order:
1. **Structural-trim name keywords** (`accessory_types.GATE_KEYWORDS`: fascia, soffit, trim, gutter,
   flashing, molding, cornice, coping, baseboard, skirting) → gate. Checked first so a trim element
   *with* a Body is still gated. (Gates `Fascia:Fascia-Flat 1x12`.)
2. **No real 3D Body** → gate. The element must have a `Body` `IfcShapeRepresentation` with items and
   a 3D `RepresentationType` (`accessory_types.SOLID_REP_TYPES` — `SweptSolid` / `Brep` /
   `AdvancedBrep` / `MappedRepresentation` / …). Gates the 13 `Text:…MS GOTHIC` proxies
   (`Annotation` / `Annotation2D`, no Body).
3. **Unreadable** → the per-element tag is wrapped in `try/except`; any exception → skip + log.

No fill-ratio / tessellation gate is needed (we author no geometry; odd shapes are kept verbatim).

## Step 3 — Classify (class-prior, then name-refine)

`classify_accessory.classify(el, etype, container_name)` — the IFC **class** is the strong prior;
the **Name** (joined `Name` + `ObjectType` + type `Name`, normalized) only refines *within* a class:

```
IfcLightFixture                 → LIGHTING
IfcSanitaryTerminal             → SANITARY_FIXTURE
IfcFlowTerminal                 → LIGHTING if name∈lighting kw, APPLIANCE if washer/dryer/…, else SANITARY_FIXTURE
IfcFurnishingElement/IfcFurniture → _refine_furniture()  (FURNITURE_RULES)
IfcBuildingElementProxy         → _refine_proxy()        (PROXY_RULES)
else                            → GENERIC
```

Class-prior is what defuses the trap: `Vanity Counter Top w Square Sink Hole` is an
`IfcFurnishingElement` containing "sink", but it stays in the furniture branch (→ TABLE), never
SANITARY_FIXTURE. **Ordering note (load-bearing):** in `FURNITURE_RULES`, BED is tested *after*
SEATING + TABLE, else "Bedside Table" → BED and the SOLLERÖN "daybed" sofa → BED. Only the genuine
"Murphy bed" lands on BED.

**TYPES vocabulary** (`accessory_types.TYPES`): `PLANT, SEATING, TABLE, STORAGE, BED, APPLIANCE,
SANITARY_FIXTURE, LIGHTING, DECOR, OUTDOOR_FURNITURE, GENERIC`.

**Derived fields** (no geometry):
- `CatalogReference` = the Revit family/type Name minus the trailing `:<numeric elementid>` (the swap
  key FormX uses); fallback to the raw Name, then `SourceClass`.
- `Location` = `Outdoor` if the containing storey is GRADE/Site or the name says outdoor/exterior,
  else `Indoor`.
- `SourceClass` = `el.is_a()`.

## Step 4 — Tag (the only mutation)

Author one `FormX_Accessory` `IfcPropertySet` at the **occurrence** level via
`IfcRelDefinesByProperties` (never a 2nd type — `IfcRelDefinesByType` is [0:1]; CLAUDE.md §6):

| Property | IFC type | Value |
|---|---|---|
| `AccessoryType` | `IfcLabel` | TYPES member |
| `CatalogReference` | `IfcIdentifier` | family/type Name minus `:elementid` |
| `Movable` | `IfcBoolean` | `True` |
| `Location` | `IfcLabel` | `Indoor` / `Outdoor` |
| `SourceClass` | `IfcLabel` | original IFC class |
| `FormXConverted` | `IfcBoolean` | `True` |

These value types exist in all 3 schemas. `Name`/`Description`/`ObjectType`, geometry, styles and
placement are **untouched**.

**Idempotency** = the pset's presence (not a `Description` stamp): an element already carrying a
`FormX_Accessory` pset is skipped. **IFC2X3 with no `IfcOwnerHistory`** → the whole file is gated
(a null-owner pset would be a new validate error / fabricating an owner would change a count).

## Step 5 — verify() (PRESERVE-ONLY, stricter than door/window)

Re-open src + out; key all comparisons by `GlobalId` (ids renumber on write). `T` = newly-tagged:
1. product `GlobalId` set unchanged;
2. every product's `ObjectPlacement` matrix unchanged;
3. all geometry/style entity-type counts identical;
4. full type histogram identical EXCEPT `IfcPropertySet` +T, `IfcRelDefinesByProperties` +T,
   `IfcPropertySingleValue` +6T;
5. **keystone** — per-product `IsDefinedBy` delta = 1 on newly-tagged, 0 everywhere else (catches a
   pset leaking onto a non-accessory);a
6. exactly one well-formed `FormX_Accessory` pset (6 props) per tagged accessory;
7. no new validate errors (`errors_after ≤ errors_before`).

---

## Results (the 4 ADUs)

| Fixture | schema | candidates | tagged | gated |
|---|---|---|---|---|
| LEXFORD_OFFICE-C1 | IFC2X3 | 9 | 9 | 0 |
| SAN_JUAN_CYPRESS…-W1-L1 | IFC4X3 | 7 | 7 | 0 |
| Sunflower_A | IFC2X3 | 37 | 36 | 1 (Fascia) |
| Turnberry…-C1 | IFC4 | 64 | 51 | 13 (Text) |

All four: `verify()` ALL CHECKS PASSED, 0 new validate errors, idempotent. Tester
`test_accessory_converter_V1.py` 4/4 (837 checks), teeth verified.

## Open questions (recorded for FormX review)
1. `IfcFlowTerminal` SANITARY-vs-APPLIANCE split — washer/dryer → APPLIANCE, else SANITARY_FIXTURE.
2. `GENERIC` bucket ("Generic Models N", "Specialty Equipment", laptop) — tagged as movable wholes;
   confirm GENERIC is an acceptable replace-hook.
3. Niches (`Nichoniche_*`) → DECOR — arguably architectural; could move to the gate list.
4. `Location` is containment+name based; some source data is odd (Turnberry bedside tables are
   contained in the `GRADE` storey → tagged Outdoor). Refine if FormX needs a precise flag.
