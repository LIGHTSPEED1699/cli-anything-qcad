# Pair 1 V10→V11 Lessons: Ground Reference Lines & QCAD Screenshot Workflow

## Signal: Ground Reference Lines Caught by Cloud Polygon Sweep

**Date:** 2026-05-17  
**File:** `1.dxf` (cloud annotation deletion pipeline)  
**User Report:** "two short lines on the right side of F174, which should be kept on the drawing. These two lines are representing F174 is wired to electrical ground as voltage reference."

### What Happened

In V10, the cloud polygon sweep (C1=RIGHT-TOP) identified F174 at approximately `(8.60, 8.28)`. Two short LINE entities extending to the right of F174 were caught by the C1 polygon and deleted because their endpoints fell inside the cloud boundary. However, these lines are **ground reference / voltage reference wires** — they happen to pass through the cloud area but represent intentional circuit wiring that must remain.

### Why the Generic Polygon Sweep Failed Here

The V10 pipeline used cloud polygon `contains_point()` (with boundary-margin expansion) to decide deletion. It had no concept of:
- **Functional wire vs. annotation markup** — a line inside a cloud boundary could be either
- **Ground reference convention** — short lines extending from F-label boxes to the right typically indicate ground/equipotential bonding

### Fix Applied (V11)

**Handle:** `4B6E` — POLYLINE with 3 vertices, color=2, extending rightward from F174 box  
**Action:** Removed `4B6E` from the V10 deletion list (105 → 104 deletions)  
**Verification:** QCAD screenshot confirmed the two ground-reference lines restored next to F174  
**Deliverable:** `1_FINAL_v11.dwg` (46,785 bytes)

### New Rule for Cloud Deletion Pipeline

When a cloud polygon encompasses an area near an **instrument label** (F-label box, 101-108 text, Tb- text), any short LINE or LWPOLYLINE entities extending from that label into the cloud area should be **presumed intentional wiring** unless explicitly confirmed as annotation markup. Use label-based spatial exclusion zones (±0.5 units around label centers) instead of pure polygon containment.

**Practical implementation:** After building the initial deletion list from polygon containment, do a second pass that scans for entities within 0.5 units of any preserved instrument label. If an entity's start or end point is within that zone and the entity is short (< 1.0 unit length), **remove it from the deletion list** — it's likely a ground reference, voltage reference, or wiring jumper.

## Signal: QCAD Screenshot via xdotool + ImageMagick `import -window` Works

**Date:** 2026-05-17  
**Method:** xdotool windowactivate/raise + ImageMagick `import -window`

### Working Pattern

```bash
export DISPLAY=:1
export XAUTHORITY=/run/user/1000/gdm/Xauthority

# Get QCAD window ID
window_id=$(xdotool search --onlyvisible --class qcad | head -1)

# Focus and raise BEFORE screenshot
xdotool windowactivate $window_id
xdotool windowraise $window_id
sleep 2

# Capture with ImageMagick (reliable on GNOME)
import -window $window_id /tmp/qcad_screenshot.png
```

### What Does NOT Work
- xdotool `key` (Ctrl+O, F11) — keystrokes do not reach QCAD Qt6 window
- xdotool menu clicks — coordinate-based file menu clicks miss in Qt6
- Geisterhand `/screenshot` — black screenshots on GNOME compositor
- QCAD headless `-autostart` DXF→DWG — crashes with exit -9/-11 SIGSEGV

### Critical Log Confirmation
QCAD log confirms file loaded when using:
```bash
qcad /path/to/file.dxf &
```
Look for `openFiles: /path/to/file.dxf` in stderr/log.
