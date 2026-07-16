# Pair 1 V10→V11 Lessons — Ground Wire False Positive

## Root Cause

Handle `4B6E` was wrongly deleted in V10 as a "white dot" target. In reality, it is **not** an annotation target — it's a small right-angle POLYLINE (3 vertices, forming an L-shape) representing the electrical ground connection for instrument F174.

The entity's bounding box fell inside the C1 cloud polygon (RIGHT-TOP), so the cloud-deletion pipeline flagged it for removal. But the user's annotation was targeting **HATCH fills** (white dots / solid circles) and **strikethrough lines**, not engineering symbols attached to instrument labels.

| Property | Annotation Target | 4B6E (Wrongly Deleted) |
|----------|----------------|------------------------|
| Type | HATCH (SOLID/DOTS), LINE, TEXT | POLYLINE |
| Geometry | 100-vertex circles, long horizontal lines | 3-vertex L-shape |
| Purpose | Annotation markup | Electrical ground symbol |
| Context | Inside cloud/strikethrough band | Adjacent to F174 label |

## Entity Analysis

```
POLYLINE 4B6E:
  Vertices: (9.1678,8.3461) → (9.3883,8.3461) → (9.3883,8.1965)
  BBox: x=[9.1678,9.3883], y=[8.1965,8.3461]
  Center: (9.3148, 8.2962)
  Distance to F174 label (8.600,8.279): 0.72 units
  Distance to nearest "white dot" (F174 cluster): 0.13 units
```

The short distance to F174's instrument cluster made it appear visually grouped with the dots, but it's an electrical wiring symbol.

## Fix Applied in V11

Removed handle `4B6E` from the deletion list. Rather than regenerate from scratch (the text-based delete script corrupts the DXF when handling this many blank lines), the entity was manually re-inserted into the V10 fixed DXF by:
1. Extracting the full entity block (POLYLINE + VERTEXes + SEQEND) from the original DXF
2. Writing it into the ENTITIES section of the V10 DXF at the correct insertion point
3. Re-exporting via QCAD to produce `1_FINAL_v11.dwg`

## Key Pitfall

**Pitfall: Annotation targets vs engineering symbols inside clouds**
Not every entity inside a cloud polygon is an annotation target. CAD drawings contain dense engineering symbols (ground marks, arrows, measurement ticks, instrument boxes) that may overlap with annotation regions. The deletion pipeline should classify candidates by:

| Type | Likely Target? | Why |
|------|----------------|-----|
| HATCH with SOLID/DOTS pattern | Yes | Visual fill markers (white dots, arrows) |
| HATCH with arc/line edges | Yes | Complex fill shapes |
| LINE with color=1 (red) inside strikethrough band | Yes | Strikethrough lines |
| POLYLINE with >50 vertices | Usually | Large shape (likely circle/box around label) |
| POLYLINE with 2–4 vertices, near F-label | No | Small engineering symbol (ground, arrowhead) |
| TEXT inside cloud (not terminal label) | Yes | Annotation text to be removed |
| INSERT (block reference) with no ATTRIBs | Context | Wire end symbols = keep; stray blocks = delete |

## Prevention

Before finalizing deletions, visually inspect small POLYLINEs near instrument labels (Fxxx, Bxxx, Txxx) with few vertices (<5). Cross-reference with the user's intent — are they removing "white dots" (HATCH fills) or actual engineering symbols?
