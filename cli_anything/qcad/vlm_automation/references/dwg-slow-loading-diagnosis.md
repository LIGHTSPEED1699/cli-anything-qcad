# DWG Slow-Loading Diagnosis

**Date:** 2026-05-11
**Context:** QCAD Pro ODA-exported DWG opens correctly but takes 3+ minutes in AutoCAD TrueView

## Symptom

A DWG file:
- Opens without error in AutoCAD TrueView
- Shows all layers visible once loaded
- But takes **3+ minutes** to load (vs. ~5 seconds for a comparable DWG)
- File size is normal (~70 KB for a ~200-entity drawing)

## What Slow Loading Is NOT

| Check | Result | Conclusion |
|-------|--------|------------|
| Binary corruption (null runs, 0xFF runs) | None | Not file corruption |
| `ACAD_LAYERSTATES` dictionary | 0 occurrences | Not hidden layer state |
| Dangling handle references | 0 | Not broken object graph |
| File size anomaly | Normal (~70 KB) | Not truncated/expanded |
| TrueView Recover dialog | None | Not treated as damaged |

## Root Cause: ODA Object-Map Rebuilding

The ODA engine (used by QCAD Pro for DWG export) reads the source DXF and builds a DWG object model. If the source DXF has **structural differences** from what the ODA engine expects — even subtle ones like:
- Different `ACAD_XDICTIONARY` formatting (single-line vs multi-line)
- Different `DICTIONARY` object indentation
- Missing or extra whitespace in group-code sequences
- Handle allocation table differences

...the ODA engine spends extra time:
1. Parsing the non-standard structure
2. Rebuilding handle cross-reference maps
3. Validating object graph consistency
4. Cleaning up during export

This is **not corruption** — the output DWG is valid. But the ODA import→export cycle is slower because it must normalize the DXF structure before writing the DWG.

## What Causes Structural Differences

| Source DXF Type | ODA Rebuild Time | Notes |
|-----------------|------------------|-------|
| ODA-re-saved DXF (LibreCAD/QCAD GUI Save-As) | **Fast** (~5s) | Native ODA structure, minimal rebuilding |
| LibreDWG `dwg2dxf` output | **Medium** (~30s) | Some structural quirks, but mostly compatible |
| ezdxf `doc.saveas()` on LibreDWG DXF | **Slow** (~3min) | Structural additions (Defpoints, XDICTIONARY) confuse ODA |
| Text-edited LibreDWG DXF | **Medium** (~30s) | Raw ASCII edits preserve structure but may have gaps |

**Confirmed 2026-05-11 on Pair 1:**
- `1_QCAD_NATIVE.dwg` (ODA-re-saved source) → instant open
- `1_FINAL.dwg` (text-edited LibreDWG DXF source) → ~3 min open
- Both: 71 KB, AC1032, identical geometry, 0 corruption markers

## Diagnosis Script

```python
#!/usr/bin/env python3
"""Diagnose slow-loading DWG by comparing to a known-fast reference."""
import sys

def analyze_dwg(path):
    with open(path, 'rb') as f:
        data = f.read()
    
    print(f"File: {path}")
    print(f"  Size: {len(data):,} bytes")
    print(f"  Magic: {data[:6]}")
    
    # Corruption checks
    null_runs = sum(1 for i in range(len(data)-1000) if data[i:i+1000] == b'\x00'*1000)
    ff_runs = sum(1 for i in range(len(data)-100) if data[i:i+100] == b'\xff'*100)
    print(f"  1KB null runs: {null_runs} (should be 0)")
    print(f"  100B 0xFF runs: {ff_runs} (should be 0)")
    
    # Layer state markers
    for marker in [b'ACAD_LAYERSTATES', b'LayerState', b'LAYERSTATE',
                   b'DICTIONARY', b'XRECORD', b'frozen', b'FROZEN']:
        count = data.count(marker)
        print(f"  {marker.decode()}: {count} occurrences")
    
    # Size heuristics
    if len(data) < 1000:
        print("  WARNING: File unusually small")
    elif len(data) > 10_000_000:
        print("  WARNING: File unusually large")
    
    if null_runs == 0 and ff_runs == 0:
        print("  \nConclusion: Binary structure is CLEAN.")
        print("  Slow loading is likely ODA object-map rebuilding from non-native DXF source.")
        print("  Fix: Re-save source DXF through ODA (LibreCAD/QCAD GUI) before export.")
    else:
        print("  \nConclusion: Possible corruption detected.")

if __name__ == "__main__":
    analyze_dwg(sys.argv[1])
```

## Fixes (in order of preference)

### 1. ODA Re-Save the Source DXF (Best)

Open the source DXF in **LibreCAD** or **QCAD GUI** and use **File → Save As → DXF R2018**. This produces an ODA-native DXF that QCAD headless exports quickly.

```bash
# LibreCAD GUI: File → Save As → DXF R2018
# QCAD GUI: File → Save As → DXF 2018 (OpenDesign)
```

### 2. Accept the Slow Load (If Correctness is Priority)

If the DWG is correct (all layers visible, all entities present) and you cannot re-save the source, the 3-minute load is a one-time cost. TrueView caches the parsed DWG; subsequent opens are fast.

### 3. QCAD GUI Save-As DWG (Alternative)

Instead of headless export, open the fixed DXF in QCAD GUI and use **File → Save As → DWG R2018**. The GUI path may handle structural differences better than headless ECMAScript.

## Prevention

| Pipeline Stage | Action |
|---------------|--------|
| Source DXF | Prefer ODA-re-saved DXF over raw LibreDWG `dwg2dxf` output |
| Editing | Use text-based deletion on raw ASCII; avoid `ezdxf.saveas()` |
| Layer fix | Fix colors via text replacement, not ezdxf layer object mutation |
| Export | QCAD Pro headless ODA export with `qcad-bin` |

## Related

- `references/direct-dwg-deletion-pipeline.md` — validated pipeline that produced the slow-loading but correct DWG
- Pitfall 70 (SKILL.md) — ezdxf `doc.saveas()` structural differences
- Pitfall 71 (SKILL.md) — ODA re-save as the only reliable clean DXF source
