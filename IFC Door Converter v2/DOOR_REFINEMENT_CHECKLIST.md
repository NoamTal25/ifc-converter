# Door Golden Refinement — Cross-Chat Checklist

**Purpose:** Track visual refinement of the 16 FormX golden door templates across chat sessions.
One entry per door type. Check off ✅ once Noam confirms it looks correct in Gaudi.

**To resume in a new chat:** paste this file path and say "continue door refinement".
The new chat should read this file + `golden_door_geometry.py` + `door_types.py` to pick up where we left off.

**Run after every change:**
```
cd "IFC Door Converter v2"
python3.11 generate_goldens.py        # 16 write, validate clean
python3.11 test_door_converter_v2.py  # must stay 4/4
python3.11 IFC_door_converter_V2.py   # verify() ALL CHECKS PASSED, 0 new validate errors
```

---

## Status

| # | Type | Status | Notes / outstanding issue |
|---|---|---|---|
| 1 | `DOOR_SINGLE` | 🔧 | Rail-and-stile panelled leaf (2 stiles + 3 rails + 2 recessed panels) + 3 hinges + knob. 15 solids. Awaiting Gaudi verify. |
| 2 | `DOOR_INTERIOR_SINGLE` | 🔧 | Same panelled leaf, NO exposed hinges (cleaner interior). 12 solids. Awaiting Gaudi verify. |
| 3 | `DOOR_SINGLE_FLUSH` | 🔧 | Plain flush slab (kept flush) + 3 hinges + knob → reads as a hung leaf. 9 solids. Awaiting Gaudi verify. |
| 4 | `DOOR_DOUBLE` | 🔧 | Two **panelled** leaves + astragal over the meeting joint + 3 hinges/leaf + 2 knobs. 27 solids. Awaiting Gaudi verify. |
| 5 | `DOOR_INTERIOR_DOUBLE` | 🔧 | Two **French** glazed leaves + divided-lite muntin grid (lock rail + 1 vert + 2 horiz bars/leaf) + 3 hinges/leaf + 2 knobs. 30 solids. Awaiting Gaudi verify. |
| 6 | `DOOR_POCKET` | 🔧 | Opening half (3-sided lining) + leaf retracted in pocket half + single-sided pull. 5 solids. Awaiting Gaudi verify. |
| 7 | `DOOR_SLIDING` | ✅ | Two framed-glass sashes overlapping at centre on two depth tracks. 14 solids. **Verified in Gaudi.** |
| 8 | `DOOR_BARN` | 🔧 | Two ledged plank leaves (slab + 3 horizontal battens) + overhead track (+2 end stops) + 2 strap hangers/leaf + floor guide + bar pull. 18 solids. Awaiting Gaudi verify. |
| 9 | `DOOR_SINGLE_BARN` | 🔧 | One ledged plank leaf + track + 2 straps + floor guide + pull. 11 solids. Awaiting Gaudi verify. |
| 10 | `DOOR_SHOWER` | 🔧 | Semi-frameless: slim pivot jamb + low threshold + big glass lite + 3 pivot hinge blocks + tall towel-bar pull on 2 standoffs. 9 solids. Awaiting Gaudi verify. |
| 11 | `DOOR_BIFOLDING_GLASS` | 🔧 | 4 framed glazed leaves (coplanar) + 2 fold-hinge knuckles per joint + top track guide + pull. 32 solids. Awaiting Gaudi verify. |
| 12 | `DOOR_INTERIOR_BIFOLDING_2_PANEL` | 🔧 | 2 framed leaves + fold hinges + track guide + pull. 18 solids. Awaiting Gaudi verify. |
| 13 | `DOOR_SLIDE_AND_SWING` | 🔧 | Differentiated combo: framed sliding sash (track+2 rollers+floor guide+bar pull) on one half + panelled swing leaf (rails+panels+2 hinges+knob) on the other, split by a divider. 25 solids. Awaiting Gaudi verify. |
| 14 | `DOOR_SLIDING_SWING_COMBO` | 🔧 | Same differentiated slide+swing combo. 25 solids. Awaiting Gaudi verify. |
| 15 | `DOOR_BIFOLDING_SWING_COMBO` | 🔧 | 3 framed leaves + fold hinges + track guide + pull. 25 solids. Awaiting Gaudi verify. |
| 16 | `DOOR_OPENING` | 🔧 | 3-sided lining + architrave casing (head + 2 legs) on both wall faces. 9 solids. Awaiting Gaudi verify. |

---

## Legend
- ⬜ Not started
- 🔧 In progress / proposed, awaiting Gaudi verify
- ✅ Verified in Gaudi by Noam

---

## Change log

| Date | What changed |
|---|---|
| 2026-06-26 | Checklist created |
| 2026-06-26 | Handle: flat lever bar → compact proud knob (55×55 mm, 30 mm proud). Applies to all lever doors. Tester 4/4 pass. |
| 2026-06-26 | `DOOR_INTERIOR_DOUBLE` screenshot: knobs were floating in the glass → fixed via `leaf_frame=True`. |
| 2026-06-26 | `DOOR_SLIDING`: 1 panel + head rail → **2 panels + mullion, no head rail**, nom width 1800 mm (matches the 2-panel patio-door look in the template screenshot). Tester 4/4, verify ALL PASS. |
| 2026-06-26 | Added `leaf_frame` mode to the shared recipe: each leaf = its own 4-bar stile/rail frame around an inset glass/slab (edge-to-edge meeting stiles = centre divider, no separate mullion); knob now mounts ON the meeting stile. Enabled for `DOOR_DOUBLE` + `DOOR_INTERIOR_DOUBLE` (`leaf_frame_thk`=70 mm). `_expected_item_count` updated (leaf_frame: 4 + 5n solids). Goldens 16/16, tester 4/4, converter 11 rebuilt + verify ALL PASS + 0 new validate errors. |
| 2026-06-26 | `DOOR_SLIDING` reworked from the offset-panel idea (looked broken — a Z-offset poked a panel outside the wall depth) to **mirror the real San Juan Cypress door**: decoded its 8 breps = outer frame + 2 framed-glass sashes (~53% width each, overlapping ~6% at centre) on 2 depth tracks. New `sliding` recipe builds outer lining + 2 sashes (4 frame bars + glass each, depth-staggered but kept inside lining depth). CANON: `sash_frame_thk`/`sash_overlap`/`sash_depth_ratio`. `_expected_item_count`: sliding = 4+5n like leaf_frame. 14 solids. Tester 4/4, verify ALL PASS. |
| 2026-06-26 | `DOOR_SLIDING` ✅ verified in Gaudi (after flipping left sash to the front track). |
| 2026-06-26 | Adversarial review workflow (5 lenses → independent verify) on the enriched recipe: 2 confirmed findings, both fixed — barn plank-body role `panel`→`plank` (consistency); combo sliding hardware (track/rollers/guide/pull) FRONT-mounted instead of proud of both faces (was poking 16mm out the back). Re-gated green. Docs updated (algorithm §4, CLAUDE.md §5b/§7). |
| 2026-06-26 | **Big design pass — all remaining types enriched** (design-research workflow: 9 archetype agents + critic; then implemented in waves, each gate-green). New recipe modes/knobs in the shared recipe: `panelled` (rail-and-stile leaf: helper `_panelled_leaf`), `hinges` (helper `_hinge_stack`, 3 butt hinges/leaf), `muntins` (helper `_muntin_grid`, divided-lite grid + lock rail), `astragal`, `bifold` (fold-hinge knuckles + top track guide), `combo` (differentiated slide+swing), `shower` (semi-frameless), `barn` (ledged plank + track + straps + guide), `casing` (architrave). One canonical CANON value per physical part (one hinge/stile/rail set) + per-mode clamps; positional ratios inlined as literals (no `_dimensionless` trap). New roles `sill`/`standoff`/`track_guide` added to `_ROLE_BUCKET`; style wiring centralised via `gg.bucket_for`/`gg.GLASS_ROLES` (both generator + converter). `_expected_item_count` extended to mirror every mode. Goldens 16/16 validate clean + counts match; tester 4/4 (layer D probe switched to DOOR_INTERIOR_DOUBLE for constant-border); converter verify ALL PASS on 4 ADUs + 0 new validate errors + 0.0mm drift; feet-scale stress test (nominal/narrow/wide) found no degenerate dims. **All 15 enriched types 🔧 awaiting Gaudi verify.** |
| 2026-06-26 | `DOOR_POCKET` refinements (user): (1) removed the bottom of the frame → 3-sided lining (`_border_bars` gained a `sill=False` mode: head + 2 full-height jambs); leaf now runs to the floor. (2) Pull moved to the leaf's leading edge, projecting from ONE face only (interior, facing inward) instead of proud of both. Item count 6→5. `_expected_item_count` pocket branch updated. Tester 4/4, verify ALL PASS (0.0mm drift). |
| 2026-06-26 | `DOOR_POCKET` reworked. Decoded the real Sunflower pocket door (13 breps) = casing frame around the OPENING half + thin leaf RETRACTED in the in-wall POCKET half (leaf reaches full bbox width) + flush pull on the leaf's leading edge. New `pocket` recipe flag: skips the full-width outer lining, frames the opening half, retracts the leaf into the pocket half, authors its own leading-edge pull. nom_w 900→1800 (shows opening+pocket). Item count unchanged (6). Tester 4/4, verify ALL PASS (0.0mm drift). |
| 2026-06-26 | Handle-height fix: knob was landing near the top on doors whose local height axis points down in world (Revit exports both ways). Added `up_sign` (from the placement) through `build_door_items`→`_build_handles` so `handle_h`=1000 mm is measured above the WORLD bottom. Verified knobs now sit 1000 mm up on every LEXFORD door (consistent). Applies to all lever doors; goldens unchanged. Tester 4/4, verify ALL PASS. |
