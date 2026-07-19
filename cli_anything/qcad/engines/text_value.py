"""Text value modification engine: replace TEXT/MTEXT/ATTRIB content."""
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import re

try:
    import ezdxf
except ImportError as e:  # pragma: no cover
    raise ImportError("ezdxf is required") from e

from cli_anything.qcad.utils.dxf_entity_index import DxfEntityIndex


def _find_text_entities(doc, target: str, near_point: Optional[Tuple[float, float]] = None,
                        near_tol: float = 2.0, regex: bool = False) -> List[Any]:
    """Find TEXT/MTEXT/ATTRIB entities matching target, optionally near a point."""
    msp = doc.modelspace()
    matches = []
    target_upper = target.upper()
    for ent in msp:
        etype = ent.dxftype()
        text = ""
        pt = None
        if etype == "TEXT":
            text = (ent.dxf.text or "").upper()
            pt = (ent.dxf.insert.x, ent.dxf.insert.y)
        elif etype == "MTEXT":
            text = (ent.text or "").upper()
            pt = (ent.dxf.insert.x, ent.dxf.insert.y)
        elif etype == "ATTRIB":
            text = (ent.dxf.text or "").upper()
            try:
                pt = (ent.dxf.insert.x, ent.dxf.insert.y)
            except Exception:
                pass
        if not text:
            continue
        matched = (target_upper == text) or (target_upper in text) if not regex else bool(re.search(target, text, re.IGNORECASE))
        if not matched:
            continue
        if near_point and pt:
            dx = pt[0] - near_point[0]
            dy = pt[1] - near_point[1]
            if dx * dx + dy * dy > near_tol * near_tol:
                continue
        matches.append(ent)
    return matches


def _nearest_text_style(doc, point: Tuple[float, float], tol: float = 1.0) -> Dict[str, Any]:
    """Find nearest TEXT/MTEXT to point and return style properties."""
    msp = doc.modelspace()
    best = None
    best_dist = float("inf")
    for ent in msp:
        etype = ent.dxftype()
        if etype not in ("TEXT", "MTEXT"):
            continue
        try:
            pt = (ent.dxf.insert.x, ent.dxf.insert.y)
        except Exception:
            continue
        d = (pt[0] - point[0]) ** 2 + (pt[1] - point[1]) ** 2
        if d < best_dist:
            best_dist = d
            best = ent
    if not best or best_dist > tol * tol:
        return {"height": 0.125, "layer": "0", "style": "Standard"}
    if best.dxftype() == "TEXT":
        return {
            "height": getattr(best.dxf, "height", 0.125),
            "layer": best.dxf.layer,
            "style": getattr(best.dxf, "style", "Standard"),
            "rotation": getattr(best.dxf, "rotation", 0.0),
        }
    return {
        "height": getattr(best.dxf, "text_height", 0.125),
        "layer": best.dxf.layer,
        "style": getattr(best.dxf, "style", "Standard"),
    }


def _point_to_segment_dist(pt: Tuple[float, float],
                           a: Tuple[float, float],
                           b: Tuple[float, float]) -> Tuple[float, Tuple[float, float]]:
    """Distance from point to line segment a-b, and closest point on segment."""
    import math
    dx, dy = b[0] - a[0], b[1] - a[1]
    seg_len2 = dx * dx + dy * dy
    if seg_len2 < 1e-12:
        d = math.hypot(pt[0] - a[0], pt[1] - a[1])
        return d, a
    t = ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / seg_len2
    t = max(0.0, min(1.0, t))
    closest = (a[0] + t * dx, a[1] + t * dy)
    d = math.hypot(pt[0] - closest[0], pt[1] - closest[1])
    return d, closest


def _snap_to_nearest_wire(msp, point: Tuple[float, float],
                          max_dist: float = 2.0) -> Optional[Tuple[float, float]]:
    """Find the nearest LINE entity to *point* and return a label position on it.

    The returned point is the midpoint of the nearest line segment, shifted
    slightly above (in +y) for label readability.  Returns None if no LINE
    is within *max_dist*.
    """
    import math
    best_dist = float("inf")
    best_line = None

    for ent in msp:
        if ent.dxftype() != "LINE":
            continue
        try:
            a = (ent.dxf.start.x, ent.dxf.start.y)
            b = (ent.dxf.end.x, ent.dxf.end.y)
        except Exception:
            continue
        d, closest = _point_to_segment_dist(point, a, b)
        if d < best_dist:
            best_dist = d
            best_line = (a, b, closest)

    if best_dist > max_dist or best_line is None:
        return None

    a, b, closest = best_line
    # Place label at the closest point on the line, offset slightly in +y
    # so the text sits just above the wire (standard wire label convention).
    offset = 0.15  # ~1.5x typical text height
    return (closest[0], closest[1] + offset)


class ChangeTextValueEngine:
    """Replace existing text/MTEXT/ATTRIB content."""

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        target = parameters.get("target_text") or parameters.get("target_description")
        new_value = parameters.get("new_value", "")
        near = parameters.get("near_point")
        regex = parameters.get("regex", False)

        doc = ezdxf.readfile(dxf_path)

        # If no explicit target text but we have a near_point, find the
        # nearest TEXT/MTEXT entity and change it.  This handles instructions
        # like "Change to TB-21" where the original text isn't specified but
        # the annotation's location tells us which text to change.
        if (not target or "entity" in (target or "").lower()
                or "bounding box" in (target or "").lower()) and near:
            msp = doc.modelspace()
            best_ent = None
            best_dist = float("inf")
            for ent in msp:
                etype = ent.dxftype()
                if etype not in ("TEXT", "MTEXT", "ATTRIB"):
                    continue
                try:
                    pt = (ent.dxf.insert.x, ent.dxf.insert.y)
                except Exception:
                    continue
                d = (pt[0] - near[0]) ** 2 + (pt[1] - near[1]) ** 2
                if d < best_dist:
                    best_dist = d
                    best_ent = ent
            # Also search ATTRIBs inside INSERT entities (title block values)
            for ent in msp:
                if ent.dxftype() != "INSERT":
                    continue
                try:
                    for attrib in ent.attribs:
                        pt = (attrib.dxf.insert.x, attrib.dxf.insert.y)
                        d = (pt[0] - near[0]) ** 2 + (pt[1] - near[1]) ** 2
                        if d < best_dist:
                            best_dist = d
                            best_ent = attrib
                except Exception:
                    pass
            if best_ent and best_dist < 100:  # reasonable proximity
                etype = best_ent.dxftype()
                try:
                    if etype == "TEXT" or etype == "ATTRIB":
                        old_text = best_ent.dxf.text
                        best_ent.dxf.text = new_value
                    elif etype == "MTEXT":
                        old_text = best_ent.text
                        best_ent.text = new_value
                    doc.saveas(out_dxf)
                    return {
                        "engine": "change_text_value",
                        "target": f"nearest to {near}",
                        "old_value": old_text,
                        "new_value": new_value,
                        "matches_found": 1,
                        "changed": 1,
                        "output_dxf": out_dxf,
                    }
                except Exception:
                    pass

        if not target or new_value is None:
            return {"engine": "change_text_value", "success": False, "error": "missing target or new_value"}

        # When target_text is a real search pattern (not a vague description
        # like "entity" or "bounding box"), search ALL text without filtering
        # by near_point.  The near_point filter is only useful when the target
        # is vague and we need to rely on the annotation's location.
        vague_target = ("entity" in target.lower() or "bounding box" in target.lower()
                        or "text" == target.lower().strip())
        search_near = near if vague_target else None

        doc = ezdxf.readfile(dxf_path)
        matches = _find_text_entities(doc, target, near_point=search_near, regex=regex)
        changed = 0
        for ent in matches:
            etype = ent.dxftype()
            try:
                if etype == "TEXT" or etype == "ATTRIB":
                    ent.dxf.text = new_value
                elif etype == "MTEXT":
                    ent.text = new_value
                changed += 1
            except Exception:
                pass

        doc.saveas(out_dxf)
        return {
            "engine": "change_text_value",
            "target": target,
            "new_value": new_value,
            "matches_found": len(matches),
            "changed": changed,
            "output_dxf": out_dxf,
        }


def _estimate_text_width(text: str, height: float) -> float:
    """Rough text width estimate: chars * height * 0.6 (monospace-ish)."""
    return len(text) * height * 0.6


def _text_bbox(ent) -> Tuple[float, float, float, float]:
    """Get (x1, y1, x2, y2) bounding box of a TEXT/MTEXT entity."""
    x = ent.dxf.insert.x
    y = ent.dxf.insert.y
    if ent.dxftype() == "TEXT":
        h = getattr(ent.dxf, "height", 2.5)
        t = ent.dxf.text or ""
        w = _estimate_text_width(t, h)
    elif ent.dxftype() == "MTEXT":
        h = getattr(ent.dxf, "char_height", None) or getattr(ent.dxf, "height", 2.5)
        t = ent.text or ""
        w = _estimate_text_width(t, h)
    else:
        return (x, y, x, y)
    # TEXT origin is bottom-left; y is baseline
    return (x, y, x + w, y + h)


def _check_text_collision(all_texts: List[Any],
                          cx: float, cy: float,
                          label_width: float, label_height: float,
                          exclude_text: str = "",
                          margin: float = 0.5) -> bool:
    """Check if a new text at (cx, cy) with given width/height would overlap
    any existing TEXT/MTEXT entity. exclude_text skips entities matching
    the label being placed (to avoid self-collision).

    Returns True if a collision is detected, False if the position is clear.
    """
    # New label bbox (origin at bottom-left)
    nx1, ny1 = cx - margin, cy - margin
    nx2, ny2 = cx + label_width + margin, cy + label_height + margin

    exclude_upper = exclude_text.strip().upper() if exclude_text else None

    for ent in all_texts:
        if exclude_upper:
            et = (ent.dxf.text or "").strip().upper() if ent.dxftype() == "TEXT" else \
                 (ent.text or "").strip().upper() if ent.dxftype() == "MTEXT" else ""
            if et == exclude_upper:
                continue
        ex1, ey1, ex2, ey2 = _text_bbox(ent)
        ex1 -= margin
        ey1 -= margin
        ex2 += margin
        ey2 += margin
        # Rectangle intersection test
        if nx1 < ex2 and nx2 > ex1 and ny1 < ey2 and ny2 > ey1:
            return True
    return False


class AddTextLabelEngine:
    """Add a new TEXT/MTEXT label at a specified location, matching nearby style.

    If the annotation references "revision note" or "revision row", this engine
    fills in the revision table ATTRIB slots (REV_N, REV_DATE_N, etc.) on the
    title block INSERT instead of adding a standalone TEXT entity.
    """

    def run(self, dxf_path: str, parameters: Dict[str, Any],
            out_dxf: str) -> Dict[str, Any]:
        text = parameters.get("text", "")
        new_value = parameters.get("new_value", "")
        point = parameters.get("point")
        layer = parameters.get("layer")
        height = parameters.get("height")
        region = parameters.get("region")

        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()

        # ── Batch label mode ─────────────────────────────────
        # "add labels Y521 besides 5-5-01, Y522 to 5-5-02, ..., Y536 besides 5-5-16"
        # Parameters: batch_labels=["Y521",...,"Y536"], batch_targets=["5-5-01",...,"5-5-16"]
        # For each target, find the existing TEXT entity by its text, then place
        # the corresponding label beside it (slight X offset, same Y, same style).
        batch_labels = parameters.get("batch_labels")
        batch_targets = parameters.get("batch_targets")
        if batch_labels and batch_targets and len(batch_labels) == len(batch_targets):
            return self._add_batch_labels(doc, msp, batch_labels, batch_targets,
                                          parameters, out_dxf, dxf_path)

        # ── Revision row filling mode ──────────────────────────
        # If the annotation text mentions "revision note" or "revision row",
        # fill in the next empty revision table ATTRIB slot.
        annotation_text = (text or "").lower()
        if "revision" in annotation_text and ("note" in annotation_text
                                               or "row" in annotation_text):
            result = self._fill_revision_row(doc, msp, new_value, parameters, out_dxf, dxf_path=dxf_path)
            if result is not None:
                return result
            # Fall through to standalone TEXT if revision table not found

        if not point and region:
            # Pick a sensible point: upper-left of bbox
            bbox = region.get("bbox")
            if bbox:
                point = (bbox[0], bbox[3])
        if not point:
            return {"engine": "add_text_label", "success": False, "error": "no insertion point"}

        # Snap to nearest LINE entity: if the point is near a wire line,
        # place the text on that line (midpoint of nearest line segment).
        # This corrects for affine calibration residual error.
        snap_pt = _snap_to_nearest_wire(msp, point, max_dist=2.0)
        if snap_pt:
            point = snap_pt

        style = _nearest_text_style(doc, point)
        if layer:
            style["layer"] = layer
        if height:
            style["height"] = height

        msp.add_text(text, dxfattribs={
            "insert": point,
            "height": style["height"],
            "layer": style["layer"],
            "style": style["style"],
            "rotation": style.get("rotation", 0.0),
        })
        doc.saveas(out_dxf)
        return {
            "engine": "add_text_label",
            "text": text,
            "insert": point,
            "snapped_to_wire": snap_pt is not None,
            "output_dxf": out_dxf,
        }

    def _add_batch_labels(self, doc, msp, labels: List[str], targets: List[str],
                          parameters: Dict[str, Any], out_dxf: str,
                          dxf_path: str = None) -> Dict[str, Any]:
        """Add multiple labels beside existing target text entities.

        For each target text (e.g. "5-5-01"), find its TEXT entity in the
        DXF, then place the corresponding label (e.g. "Y521") beside it
        with a small X offset, matching the target's text style (height,
        layer, style, rotation).

        The X offset defaults to the target text height (placing the label
        just to the left of the wire number), but can be overridden via
        parameters["x_offset"].

        **Collision detection:** Before placing a label, scans for existing
        TEXT/MTEXT entities whose bounding boxes would overlap the proposed
        position. If a collision is found, tries alternative offsets (left,
        right, above, below) and picks the first that doesn't collide. If
        all positions collide, skips the label and reports it as blocked.
        """
        added = []
        not_found = []
        blocked = []

        # Build a text index: map text content -> entity
        text_map: Dict[str, Any] = {}
        all_texts: List[Any] = []  # for collision detection
        for ent in msp:
            if ent.dxftype() == "TEXT":
                txt = (ent.dxf.text or "").strip().upper()
                if txt:
                    text_map.setdefault(txt, []).append(ent)
                all_texts.append(ent)
            elif ent.dxftype() == "MTEXT":
                all_texts.append(ent)

        for label, target in zip(labels, targets):
            target_upper = target.strip().upper()
            candidates = text_map.get(target_upper, [])
            if not candidates:
                # Try partial match (target might be a substring)
                for txt, ents in text_map.items():
                    if target_upper in txt or txt in target_upper:
                        candidates = ents
                        break
            if not candidates:
                not_found.append(target)
                continue

            # Use the first candidate (or the one closest to the annotation point)
            target_ent = candidates[0]

            # Get target text style
            t_height = getattr(target_ent.dxf, "height", 2.5)
            t_layer = target_ent.dxf.layer
            t_style = getattr(target_ent.dxf, "style", "Standard")
            t_rotation = getattr(target_ent.dxf, "rotation", 0.0)

            # Estimate label width (rough: chars * height * 0.6 for monospace-ish)
            label_width = len(label) * t_height * 0.6
            label_height = t_height

            # Candidate positions: try left, right, above, below the target
            x_offset = parameters.get("x_offset", t_height * 1.5)
            target_x = target_ent.dxf.insert.x
            target_y = target_ent.dxf.insert.y

            candidate_positions = [
                ("left", target_x - x_offset - label_width, target_y),
                ("right", target_x + x_offset + label_width, target_y),
                ("above", target_x, target_y + label_height * 1.5),
                ("below", target_x, target_y - label_height * 1.5),
                ("far_left", target_x - x_offset * 3 - label_width, target_y),
                ("far_right", target_x + x_offset * 3 + label_width, target_y),
            ]

            # Check if label already exists at any candidate position (avoid duplicates)
            already_exists = False
            for ent in all_texts:
                if ent.dxftype() == "TEXT":
                    et = (ent.dxf.text or "").strip().upper()
                    if et == label.upper():
                        ex, ey = ent.dxf.insert.x, ent.dxf.insert.y
                        for _, cx, cy in candidate_positions:
                            if abs(ex - cx) < 2.0 and abs(ey - cy) < 2.0:
                                already_exists = True
                                break
                if already_exists:
                    break

            if already_exists:
                added.append({"label": label, "target": target,
                              "position": candidate_positions[0][1:3],
                              "duplicate": True})
                continue

            # Collision detection: find a position that doesn't overlap existing text
            chosen_pos = None
            chosen_side = None
            for side, cx, cy in candidate_positions:
                if _check_text_collision(all_texts, cx, cy, label_width, label_height,
                                         exclude_text=label):
                    continue
                chosen_pos = (cx, cy)
                chosen_side = side
                break

            if chosen_pos is None:
                # All positions collide — skip and report
                blocked.append({"label": label, "target": target,
                                "reason": "all candidate positions collide with existing text"})
                continue

            msp.add_text(label, dxfattribs={
                "insert": chosen_pos,
                "height": t_height,
                "layer": t_layer,
                "style": t_style,
                "rotation": t_rotation,
            })
            added.append({"label": label, "target": target,
                          "position": chosen_pos, "side": chosen_side,
                          "duplicate": False})

        doc.saveas(out_dxf)
        return {
            "engine": "add_text_label",
            "mode": "batch",
            "labels_added": len([a for a in added if not a.get("duplicate")]),
            "labels_duplicate": len([a for a in added if a.get("duplicate")]),
            "labels_blocked": len(blocked),
            "targets_not_found": not_found,
            "blocked": blocked,
            "details": added,
            "output_dxf": out_dxf,
        }

    def _fill_revision_row(self, doc, msp, new_value: str,
                           parameters: Dict[str, Any], out_dxf: str,
                           dxf_path: str = None) -> Optional[Dict[str, Any]]:
        """Fill the next empty revision ATTRIB slot on the title block INSERT.

        Revision table structure is auto-discovered from DrawingProfile when
        dxf_path is provided. Falls back to REV_1..REV_8 convention.

        Finds the first empty REV_N slot, fills it with the new revision value,
        copies DRAW/CHK from the previous filled row, and returns the result.
        Returns None if no revision table INSERT is found.
        """
        # Discover revision table structure from profile
        rev_block_name = None
        tag_pattern = "REV_{n}"  # default
        max_rows = 8
        date_pattern = "REV_DATE_{n}"
        draw_pattern = "REV_DRAW_{n}"
        chk_pattern = "REV_CHK_{n}"

        if dxf_path:
            try:
                from cli_anything.qcad.utils.drawing_profile import DrawingProfile
                profile = DrawingProfile.from_dxf(dxf_path)
                if profile.rev_table:
                    rev_block_name = profile.rev_table.block_name
                    tag_pattern = profile.rev_table.tag_pattern
                    max_rows = profile.rev_table.max_rows
                    date_pattern = profile.rev_table.date_tag_pattern
                    draw_pattern = profile.rev_table.draw_tag_pattern
                    chk_pattern = profile.rev_table.chk_tag_pattern
            except Exception:
                pass

        # Parse new_value: "B, 2026/07/10" -> rev="B", date="2026/07/10"
        rev_letter = ""
        rev_date = ""
        if "," in new_value:
            parts = new_value.split(",", 1)
            rev_letter = parts[0].strip()
            rev_date = parts[1].strip()
        else:
            rev_letter = new_value.strip()

        # Find INSERT with REV_N ATTRIBs (title block)
        for ent in msp:
            if ent.dxftype() != "INSERT":
                continue
            # If profile found a specific block name, only match that.
            # Otherwise, match any INSERT with REV_1 (or REV1) ATTRIBs.
            if rev_block_name and ent.dxf.name != rev_block_name:
                continue
            attribs = {a.dxf.tag: a for a in ent.attribs}
            # Check if this INSERT has revision table ATTRIBs
            first_rev_tag = tag_pattern.replace("{n}", "1").replace("{n:02d}", "01")
            if first_rev_tag not in attribs:
                continue

            # Find the first empty revision row (REV_N where REV_N.text is empty)
            for n in range(1, max_rows + 1):
                if "{n:02d}" in tag_pattern:
                    rev_tag = tag_pattern.replace("{n:02d}", f"{n:02d}")
                else:
                    rev_tag = tag_pattern.replace("{n}", str(n))
                if rev_tag in attribs:
                    current = attribs[rev_tag].dxf.text or ""
                    if not current.strip():
                        # This is the next empty row — fill it
                        if rev_letter:
                            attribs[rev_tag].dxf.text = rev_letter
                        if rev_date and date_pattern:
                            date_tag = date_pattern.replace("{n}", str(n)).replace("{n:02d}", f"{n:02d}")
                            if date_tag in attribs:
                                attribs[date_tag].dxf.text = rev_date
                        # Copy DRAW and CHK from the previous filled row
                        prev = n - 1
                        if prev >= 1:
                            for pattern in [draw_pattern, chk_pattern]:
                                if not pattern:
                                    continue
                                prev_tag = pattern.replace("{n}", str(prev)).replace("{n:02d}", f"{prev:02d}")
                                curr_tag = pattern.replace("{n}", str(n)).replace("{n:02d}", f"{n:02d}")
                                if prev_tag in attribs and curr_tag in attribs:
                                    attribs[curr_tag].dxf.text = attribs[prev_tag].dxf.text

                            doc.saveas(out_dxf)
                            return {
                                "engine": "add_text_label",
                                "success": True,
                                "revision_row_filled": n,
                                "rev_letter": rev_letter,
                                "rev_date": rev_date,
                                "insert_handle": ent.dxf.handle,
                                "output_dxf": out_dxf,
                            }

            # All rows filled — can't add more
            doc.saveas(out_dxf)
            return {
                "engine": "add_text_label",
                "success": False,
                "error": f"All {max_rows} revision table rows are filled",
                "output_dxf": out_dxf,
            }

        return None  # No revision table found
