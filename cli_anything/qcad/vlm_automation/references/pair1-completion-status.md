# Pair 1 Completion Status

**Status:** ✅ COMPLETED as of 2026-05-18.

## Final Deliverable

File: `1_FINAL_v11.dwg`  
Path: `/home/hongbin/.hermes/kanban/workspaces/testfiles_2026.05.07/1_FINAL_v11.dwg`  
Size: 46,785 bytes  

## What Was Done

- All PDF cloud/strikethrough annotations removed (104 deletions total).
- HATCH fills (SOLID circle fills and DOTS rectangle fills) removed.
- Red strikethrough lines removed.
- Unwanted texts ("Hydrogen Peroxide Tank Level Display") removed.
- Ground-reference lines near F174 (handle `4B6E`) **restored** in V11.
- Instrument labels (Tb703, 101–108) and F-label boxes preserved.

## Versions That Existed

| Version | Deletions | Key Fixes |
|---------|-----------|-----------|
| v7 | 109 | Initial cloud-based deletion |
| v8 | 109 | Fixed C1/C3 mapping — three white dots still present |
| v9 | 112 | Corrected cloud positions (LEFT vs RIGHT), fixed overlay |
| v10 | 105 | Removed HATCHes, red line, restored labels, kept ground refs |
| v11 | 104 | Restored handle 4B6E (two ground-reference lines near F174) |

## Key Technical Pitfalls

1. **Cloud coordinate mapping**: Use `swap_xy` for 1224×792 landscape PDF (x_dxf = y_pdf/72, y_dxf = x_pdf/72). Verified by user.
2. **HATCH edge detection**: HATCH entities use `edges` (ArcEdge/LineEdge), not vertices. Must test bounding box against cloud polygons.
3. **Boundary-point exclusion**: `contains_point()` returns False for points on polygon boundary. Use expanded polygon (margin=0.08) or explicit boundary check for strikethrough lines with endpoints on cloud edge.
4. **Label exclusion**: Instrument labels at x≈5.2 inside C0/C2 clouds — explicitly exclude from deletion even if inside polygon.
5. **ezdxf `saveas()` crash**: Use text-based `fix_layer_visibility.py` instead of ezdxf for layer color fixes on malformed DXFs.
6. **QCAD headless**: Must call `qcad-bin` with `-platform offscreen`, not the `qcad` wrapper. Set `LD_LIBRARY_PATH` to QCAD directory. Kill lingering processes with `pkill -9 -f qcad`.
7. **Ground reference lines**: Always verify user hasn't deleted wiring symbols (ground reference, line terminators, junction dots) that happen to be near cloud boundaries.

## DO NOT RESUME (Deprecated)

The original "DO NOT RESUME" note has been removed. Pair 1 continues to be the primary testbed for pipeline modifications. Subsequent sessions should re-run Pair 1 when validating pipeline changes.

**Current pipeline:**
1. ODA GUI `dwg2dxf` (preserves revision block) → `1_ascii.dxf`
2. ezdxf surgical deletion + layer color fix → `1_clean.dxf`
3. QCAD headless `qcad-bin` + `qcad_dxf2dwg.js` → `1_FINAL.dwg` (AC2018, 44KB)

**Validated 2026-05-24:** QCAD headless works on this system when `DISPLAY=:1`, `XAUTHORITY`, and `DBUS_SESSION_BUS_ADDRESS` are exported, running `qcad-bin` directly (NOT the `qcad` wrapper) with `-platform offscreen -allow-multiple-instances`.
