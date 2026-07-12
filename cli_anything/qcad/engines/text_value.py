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
