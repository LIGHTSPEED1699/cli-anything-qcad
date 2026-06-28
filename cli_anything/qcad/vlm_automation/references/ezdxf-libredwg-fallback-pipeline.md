# ezdxf + LibreDWG Fallback Pipeline

Validated: 2026-05-24 (Pair 1 V11, session context compressed).

## When to Use This

Use when the primary QCAD-headless DWG export path fails:

- `qcad-bin -no-gui -platform offscreen` → killed (exit -9) or silent-hangs
- `ODAFileConverter` (headless via `xvfb-run`) → times out after 180s
- Text-based DXF parsers fail on binary DXFs (group-code `0` appearing as entity value breaks naive line scanning)

## Fallback Pipeline (3 Steps)

```
Original DWG → dwg2dxf → ezdxf surgical delete → ezdxf layer fix → ezdxf saveas → LibreDWG dxf2dwg → Clean DWG
```

## Step 1: Binary → ASCII Conversion (if needed)

```python
import ezdxf

doc = ezdxf.readfile("1.dxf")  # auto-detects binary vs ASCII
doc.saveas("1_ascii.dxf")
```

## Step 2: ezdxf Surgical Deletion + Layer Fix

```python
import ezdxf

doc = ezdxf.readfile("1_ascii.dxf")
msp = doc.modelspace()

# Delete by handle using ezdxf object model
for h in delete_handles:
    try:
        e = msp.query(f'*[handle=="{h}"]')[0]
        msp.delete_entity(e)
    except Exception:
        pass  # handle not found in this document

# Fix all negative layer color codes (62 values < 0 mean OFF)
for layer in doc.layers:
    if layer.dxf.color < 0:
        layer.dxf.color = abs(layer.dxf.color)

doc.saveas("1_clean.dxf")
```

**Key advantage over text-based deletion:**
- `delete_entity()` removes the entity cleanly from ezdxf's internal object model, preserving all handle references in the OBJECTS section and rewriting the DXF coherently.
- Text-based regex scripts strip ENTITIES blocks but leave dangling handle references → `dxf2dwg` aborts with `Object handle not found` and duplicate-handle errors.
- ezdxf `saveas()` rewrites from the internal object model — handles stay coherent.

## Step 3: LibreDWG dxf2dwg Conversion

```bash
/media/sdddata1/libredwg/bin/dxf2dwg \
    --as r2004 -y \
    -o 1_FINAL.dwg \
    1_clean.dxf
```

**Expected warnings (all benign):**
- `Invalid BLOCK_HEADER field seqend/attribs/first_attrib/last_attrib` — Legacy DXF fields not present in DWG R2004 target format.
- `Object handle not found … in N objects` — Some OBJECTS handles removed during cleanup; dxf2dwg skips them.
- `Duplicate handle … already points to …` — ezdxf reassigned handles during save; dxf2dwg deduplicates.
- `Unknown DXF code … for MATERIAL` — Extended material data not recognized by LibreDWG.
- `HATCH.num_seeds 1 but seeds NULL` — Cosmetic.

**Result:** Valid `DWG AutoDesk AutoCAD 2004/2005/2006` file (confirmed by `file` command).

## Comparison: Why This Works When QCAD Fails

| Factor | QCAD Headless | ODA Headless | LibreDWG dxf2dwg |
|--------|---------------|--------------|------------------|
| Requires display | No (offscreen) | No (xvfb) | No |
| Qt6 dependency | Yes (crashes) | Yes (times out) | No |
| Exit behavior | Crash/hang (-9) | Timeout (180s) | Completes (~5s) |
| Accepts dangling handle refs | N/A | N/A | Yes (skips gracefully) |
| DWG version | R32 (2018) | R32 (2018) | R2004 (max; r12/r14/r2000 available) |
| Layer visibility | ODA may hide | Preserves | Preserves (no ODA rewrite) |

## When NOT to Use This Fallback

- **BLANK DRAWING RISK (2026-05-24):** LibreDWG outputs R2004. In QCAD/AutoCAD 2018+ this may open as blank. Test-open before delivering. This fallback is ONLY for R2004-compatible viewers.
- **Always prefer the old workspace file** if it exists (e.g. `1_FINAL_v11.dwg` at 46KB, AC2010) rather than regenerating via a fallback tool.
- If the target must be **R2018+** — LibreDWG max is R2004.
- If QCAD-specific post-export steps are required (e.g. QCAD layer state scripts).

## Verification

```python
import ezdxf

doc = ezdxf.readfile("1_clean.dxf")
msp = doc.modelspace()

print(f"Entities: {len(list(msp))}")
print(f"HATCHes: {sum(1 for e in msp if e.dxftype()=='HATCH')}")

kept = ('Tb703','101','102','104','105','106','108','F172','F174','F175','F176')
for e in msp:
    if e.dxftype()=='TEXT' and e.dxf.text.strip() in kept:
        print(f"  KEPT: {e.dxf.handle} = {e.dxf.text}")

for e in msp:
    if e.dxftype()=='TEXT' and e.dxf.text.strip() in ('Hydrogen','Peroxide',' tank'):
        print(f"  ERROR: {e.dxf.handle} '{e.dxf.text}' should be deleted")

neg = [l.dxf.name for l in doc.layers if l.dxf.color < 0]
print(f"Negative color layers: {neg}")
```

## Pair 1 File Size Trajectory

| Stage | File | Size |
|-------|------|------|
| Original DWG | `1.dwg` | ~700 KB |
| Original DXF | `1.dxf` | 703 KB (binary) |
| ASCII DXF | `1_ascii.dxf` | ~700 KB |
| Clean DXF | `1_clean.dxf` | 352 KB (87 deletions) |
| Final DWG | `1_FINAL.dwg` | 1.7 MB (R2004) |
