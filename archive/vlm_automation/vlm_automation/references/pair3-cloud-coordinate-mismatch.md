# Pair 3 Cloud Coordinate Mismatch (2026-05-13)

## Problem

Cloud annotation `3_cloud.pdf` (Polygon type, 4 vertices) extracted and mapped using the confirmed `swap_xy` formula (`x_dxf = y_pdf/72, y_dxf = x_pdf/72`) produced DXF coordinates **x ≈ 8.6–11.5, y ≈ 9.3–10.0**.

However, the actual source entities to copy (PLC21/CA-1451 terminals 4/5/6) are located at **y ≈ 19–20** in the DWG — a **10-unit vertical offset**.

## Cloud PDF vs. Base PDF Comparison

| Property | Base PDF (`3.pdf`) | Cloud PDF (`3_cloud.pdf`) |
|----------|-------------------|---------------------------|
| Page size | 1224 × 792 pt | 1224 × 792 pt |
| Annotations | 4 (from dwg2dxf) | 5 (cloud + 3 FreeText + 1 Line) |
| Cloud polygon | N/A | 4 vertices at y ≈ 620–830 pt |

The cloud was drawn by the user on what appears to be a **different PDF view** (possibly a zoomed or alternate rendering), not the `dwg2dxf` output. The cloud PDF page dimensions match the base PDF, but the **y-position of the cloud** (~620 pt from bottom) maps to DXF y ≈ 9.3–10.0, not y ≈ 19–20.

## Implication

Cloud-based entity selection **cannot proceed** until the correct cloud coordinate mapping is resolved. Two possibilities:
1. The cloud was drawn on a different rendering of the DWG (e.g., a zoomed-in section at a different scale or origin).
2. The DWG itself has a different internal coordinate convention than the `swap_xy` mapping assumes.

## Recommended Next Step

Request the user to either:
- **Re-draw the cloud** on the exact same `3.pdf` (dwg2dxf output) that the pipeline uses, OR
- **Confirm the DWG coordinate system** by inspecting a known entity (e.g., PLC21 text) and comparing its DXF position to its visual position on the cloud PDF.

## Key Lesson

Cloud-based selection pipelines must validate coordinate alignment **before** entity matching. Always render an overlay image (cloud polygon + DWG entities) and ask the user to confirm visually before proceeding with automated cloning/deletion.
