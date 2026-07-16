# dxf2dwg Conversion Lessons (2026-06-10)

LibreDWG `dxf2dwg` is the fallback DWG writer when QCAD headless and ODA are unavailable. It is NOT a transparent DXF round-trip tool. Specific DXF constructions cause it to segfault (exit 139) or abort with non-zero exit.

## Known Failure Modes

### 1. ezdxf `saveas()` → dxf2dwg segfault

**Symptom:** `dxf2dwg` segfaults (exit 139) on a DXF produced by ezdxf `saveas()`.
**Root cause:** ezdxf regenerates handles on save. The LibreDWG-roundtripped DXF contains duplicate handles (from `dwg2dxf`). When ezdxf renumbers them, it creates conflicts with OBJECTS-section entries that still reference the old handles.
**Fix:** Do NOT use ezdxf `saveas()` on LibreDWG-roundtripped DXFs. Use text-level editing on the raw DXF instead.

### 2. Unknown DXF code 330 for HATCH

**Symptom:** `dxf2dwg` prints `ERROR: Unknown DXF code 330 for HATCH` repeatedly.
**Root cause:** HATCH entities with `PolyEdgePath` have `330` soft-pointer references to their boundary edge entities. When those boundary edges are deleted, the `330` references become dangling.
**Fix:** Do not delete boundary edges of HATCH entities. Either keep them or delete the entire HATCH (and renumber handles).

### 3. Duplicate handle warnings → write abort

**Symptom:** `dxf2dwg` prints hundreds of "Duplicate handle X for object Y" warnings, then exits non-zero.
**Root cause:** The DXF contains duplicate handles. This happens naturally in LibreDWG `dwg2dxf` output (original has 344 duplicates). ezdxf `saveas()` can exacerbate the problem.
**Fix:** The original `dwg2dxf` output already has duplicates — dxf2dwg tolerates them. The issue is when ezdxf's renumbering creates NEW duplicates or breaks handle sequences. Use text-level edits without handle renumbering.

### 4. "File not overwritten, use -y"

**Symptom:** `dxf2dwg` exits 1 with `ERROR: File not overwritten: /path/to.dwg, use -y.`
**Fix:** Add `-y` flag: `dxf2dwg input.dxf -y -o output.dwg`

### 5. ODA-generated AC1032 DXF → dxf2dwg segfault (NEW — 2026-06-11)

**Symptom:** `dxf2dwg` segfaults (exit 139) immediately with `ERROR: Unknown DXF code 330 for HATCH`, `ERROR: dwg_dynapi_entity_set_value: Invalid BLOCK_HEADER field`, and `Duplicate handle` errors.
**Root cause:** ODA File Converter produces AC1032 (AutoCAD 2018) binary DXFs that use features LibreDWG cannot parse: `MATERIAL` entries in OBJECTS section, `MLEADERSTYLE` objects, `330` soft-pointer references on HATCH entities, and different BLOCK_HEADER field layouts. LibreDWG `dxf2dwg` was designed for R2004-era DXFs; AC1032 is structurally incompatible.
**Fix:** LibreDWG `dxf2dwg` is NOT a general-purpose DXF→DWG converter. It works for its own `dwg2dxf` output (R2004-compatible) but fails on ODA-generated DXFs. **When starting from an ODA DXF, the only viable DWG conversion paths are:** (a) ODA File Converter GUI/CLI (if it works in your environment), or (b) opening the DXF in AutoCAD/QCAD and using File → Save As → DWG. See `references/oda-dxf-ezdxf-editing.md` for the validated ODA DXF editing workflow.

## Working Pattern

```bash
# 1. Original DWG -> DXF (LibreDWG)
dwg2dxf original.dwg -o original.dxf

# 2. Edit DXF using text-level changes (NOT ezdxf saveas)
python3 text_edit_dxf.py original.dxf edited.dxf

# 3. Convert back to DWG (LibreDWG)
dxf2dwg edited.dxf -y -o output.dwg
```

## Expected Benign Warnings

The following warnings are normal and do NOT indicate failure:
- `Warning: Unknown HEADER.CMLSTYLE Standard dxf:2`
- `Warning: Object handle not found ...` (a few dozen)
- `Warning: Skip HATCH common handles due to short handle stream`
- `Warning: Unstable Class object 637 TABLESTYLE`

## When dxf2dwg Works vs. Fails

| Input DXF Source | dxf2dwg Result | Notes |
|---|---|---|
| Original `dwg2dxf` output | ✅ Success | Some warnings, DWG valid |
| ezdxf `saveas()` after edits | ❌ Segfault | Handle corruption |
| Text-level edited, no handle changes | ✅ Success | Preferred path |
| Text-level edited, handles renumbered | ❌ Segfault | Don't renumber |
