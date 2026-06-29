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
| 1 | `DOOR_SINGLE` | ⬜ | — |
| 2 | `DOOR_INTERIOR_SINGLE` | ⬜ | — |
| 3 | `DOOR_SINGLE_FLUSH` | ⬜ | — |
| 4 | `DOOR_DOUBLE` | 🔧 | Now `leaf_frame=True` (two framed leaves + knob on stile) — inherits the double-door fix; awaiting Gaudi verify |
| 5 | `DOOR_INTERIOR_DOUBLE` | 🔧 | `leaf_frame=True`: two framed leaves (outer lining + per-leaf stile/rail frame + inset glass), knob mounted on each meeting **stile** (not glass). Awaiting Gaudi verify |
| 6 | `DOOR_POCKET` | 🔧 | `pocket=True`: opening half (3-sided lining, no sill) + leaf retracted in pocket half (runs to floor, full width) + pull at leaf leading edge projecting from interior face only (single-sided, facing inward). nom 1800mm, 5 solids. Awaiting Gaudi verify. |
| 7 | `DOOR_SLIDING` | ✅ | Mimics the **real San Juan Cypress** sliding glass door: two equal framed-glass sashes (each ~53% width, 72mm stiles), overlapping ~6% at centre, on two depth tracks. LEFT sash on front track. No mullion/handle. 14 solids. **Verified in Gaudi.** |
| 8 | `DOOR_BARN` | ⬜ | Confirm 2-leaf vs 1-leaf interpretation |
| 9 | `DOOR_SINGLE_BARN` | ⬜ | — |
| 10 | `DOOR_SHOWER` | ⬜ | Slim frame, glazed single leaf |
| 11 | `DOOR_BIFOLDING_GLASS` | ⬜ | Panels flat/coplanar (first pass) |
| 12 | `DOOR_INTERIOR_BIFOLDING_2_PANEL` | ⬜ | Panels flat/coplanar (first pass) |
| 13 | `DOOR_SLIDE_AND_SWING` | ⬜ | Split leaf combo (flat) |
| 14 | `DOOR_SLIDING_SWING_COMBO` | ⬜ | Split leaf combo (flat) |
| 15 | `DOOR_BIFOLDING_SWING_COMBO` | ⬜ | Bifold+swing combo (flat) |
| 16 | `DOOR_OPENING` | ⬜ | Lining only, no leaf |

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
| 2026-06-26 | `DOOR_POCKET` refinements (user): (1) removed the bottom of the frame → 3-sided lining (`_border_bars` gained a `sill=False` mode: head + 2 full-height jambs); leaf now runs to the floor. (2) Pull moved to the leaf's leading edge, projecting from ONE face only (interior, facing inward) instead of proud of both. Item count 6→5. `_expected_item_count` pocket branch updated. Tester 4/4, verify ALL PASS (0.0mm drift). |
| 2026-06-26 | `DOOR_POCKET` reworked. Decoded the real Sunflower pocket door (13 breps) = casing frame around the OPENING half + thin leaf RETRACTED in the in-wall POCKET half (leaf reaches full bbox width) + flush pull on the leaf's leading edge. New `pocket` recipe flag: skips the full-width outer lining, frames the opening half, retracts the leaf into the pocket half, authors its own leading-edge pull. nom_w 900→1800 (shows opening+pocket). Item count unchanged (6). Tester 4/4, verify ALL PASS (0.0mm drift). |
| 2026-06-26 | Handle-height fix: knob was landing near the top on doors whose local height axis points down in world (Revit exports both ways). Added `up_sign` (from the placement) through `build_door_items`→`_build_handles` so `handle_h`=1000 mm is measured above the WORLD bottom. Verified knobs now sit 1000 mm up on every LEXFORD door (consistent). Applies to all lever doors; goldens unchanged. Tester 4/4, verify ALL PASS. |
