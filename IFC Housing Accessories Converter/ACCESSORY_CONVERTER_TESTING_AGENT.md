# Accessories Converter v1 — Testing Agent Spec

## What "correct" means

The accessories converter is **PRESERVE-AND-TAG**: it tags the non-structural accessory objects
(furniture, plants, lights, plumbing fixtures, appliances, decor) with an occurrence-level
`FormX_Accessory` property set so FormX can **move** and **replace** them — while keeping them
**visually identical** to the export. So "correct" is:

1. **Zero visual change** — geometry, surface styles and `ObjectPlacement` are byte-for-byte
   preserved; the converter authors no geometry.
2. **Only-FormX-added** — the only delta is, per tagged accessory, +1 `IfcPropertySet`, +1
   `IfcRelDefinesByProperties`, +6 `IfcPropertySingleValue`. Everything else (every other element,
   relationship, geometry/style entity, GlobalId, placement) is unchanged.
3. **Right things tagged** — the expected accessories are tagged (not structural trim, not 2D text),
   each classified into the correct FormX type.
4. **Idempotent** — re-running adds nothing.
5. **No new validate errors** vs source.

## Run it

```
python3.11 "IFC Housing Accessories Converter/test_accessory_converter_V1.py"        # all fixtures
python3.11 "IFC Housing Accessories Converter/test_accessory_converter_V1.py" -v     # verbose
python3.11 "IFC Housing Accessories Converter/test_accessory_converter_V1.py" <f.ifc># one fixture
```

Each fixture is converted into a throwaway temp; the source is never touched. The tester re-derives
every invariant independently (it does NOT call the converter's `verify()`), except the layer-E
self-tests which deliberately call the shipped `verify()` to prove it has teeth.

## The 6 layers

- **A — Conservation.** Full entity histogram (`Counter(e.is_a())`) identical EXCEPT
  `IfcPropertySet`/`IfcRelDefinesByProperties`/`IfcPropertySingleValue` grow by exactly `+T/+T/+6T`;
  product GlobalId multiset identical; every geometry/style entity-type count identical.
- **B — Preservation.** Every product's `ObjectPlacement` matrix unchanged;
  `Name`/`Description`/`ObjectType` verbatim unchanged on every product; representation fingerprint
  `{(RepId, RepType, #Items)}` unchanged; shared `IfcRepresentationMap` count unchanged; **source
  file byte-identical** (sha256 + mtime).
- **C — Tag-correctness.** Exactly one `FormX_Accessory` pset per tagged accessory; 6 correctly-typed
  properties; `AccessoryType ∈ TYPES`; `Movable` True; `Location ∈ {Indoor, Outdoor}`; `SourceClass
  == element.is_a()`; `CatalogReference` non-empty with no trailing `:<digits>`.
- **D — Idempotency.** Re-run on the output tags nothing new; histogram delta zero.
- **E — Negative-control TEETH.** `T == BASELINE_TAGGED[fixture]` and `T > 0` (a no-op fails);
  **0 accessories pre-tagged** in source (the marker is meaningful); and three self-tests that corrupt
  the output and assert the shipped `verify()` returns **False**: a moved non-accessory wall (placement
  teeth), a stray non-FormX pset leaked onto a wall (histogram + keystone teeth), a stray geometry
  entity (geometry-count teeth).
- **F — Classification TEETH.** The tagged `AccessoryType` multiset matches the pinned per-fixture
  ground truth, and that ground truth sums to `BASELINE_TAGGED`. Forcing every accessory to one type
  passes A–E but fails here.

## Pinned baselines

```
BASELINE_TAGGED = {LEXFORD: 9, SAN_JUAN: 7, Sunflower: 36, Turnberry: 51}
```
`BASELINE_TYPES` pins the per-fixture `{AccessoryType: count}` multiset (recomputed from the
converter's real output). If the classifier or allow-list changes intentionally, recompute the
multiset by running the converter once and re-pin — the sums must still equal `BASELINE_TAGGED`.

## Teeth — verified

- A **no-op** converter (tags nothing) → layer E fails (`T == 0`).
- A **force-all-GENERIC** converter → layer F fails (multiset mismatch).
- A **broken placement / leaked pset / stray geometry** in the output → the shipped `verify()`
  returns False (layer E self-tests).

## Scope note

The verify/tester catch *structural* changes (added/removed entities, moved placements, leaked
relationships) — the realistic failure modes of a preserve-and-tag converter, which never writes to
a geometry attribute. A coordinate-level mesh mutation is not a failure mode of this converter (no
code path touches geometry), so it is intentionally out of scope; the geometry-count + placement +
representation-fingerprint checks are the proportionate guard.

## Expected result

`ALL PASS (4/4 fixtures)` — currently 837 checks, 0 failed.
