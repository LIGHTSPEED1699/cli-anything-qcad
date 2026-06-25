"""T1 backend: direct DXF edits via ezdxf."""
import json
import re
from typing import Any, Dict, List


class EzdxfBackend:
    """Execute simple DXF edits using ezdxf."""

    def execute(self, annotation: Dict[str, Any], dxf_path: str, out_dxf: str = None) -> Dict[str, Any]:
        text = annotation.get("text", "").lower()
        action = annotation.get("inferred_action", "")
        category = annotation.get("category", "")
        target_path = out_dxf or dxf_path

        if category == "text_change" or action in ("replace", "change") or "rev \"" in text:
            return self.update_revision_block(dxf_path, annotation, target_path)

        if action == "delete" or "delete" in text or "remove" in text:
            return self.delete_by_text(dxf_path, annotation, target_path)

        return {
            "backend": "ezdxf",
            "success": False,
            "message": "No matching T1 action",
            "annotation": annotation.get("text"),
        }

    def _extract_revision_entries(self, text: str) -> Dict[str, str]:
        """Parse 'Add REV \"4\", \"P302D removal\", \"HL\", 2026-06-10, ...' into column values."""
        result = {}
        text = re.sub(r"^(Add|Update|Change)\s+REV\s*", "", text, flags=re.IGNORECASE)
        parts = [p.strip().strip('\"').strip("'") for p in re.split(r'",\s*"|",\s*|\"', text) if p.strip()]
        if len(parts) >= 1:
            result["rev_number"] = parts[0]
        if len(parts) >= 2:
            result["description"] = parts[1]
        if len(parts) >= 3:
            result["by"] = parts[2]
        if len(parts) >= 4:
            result["date"] = parts[3]
        if len(parts) >= 5:
            result["checked"] = parts[4]
        if len(parts) >= 6:
            result["approved"] = parts[5]
        return result

    def _find_revision_table(self, msp) -> List[Any]:
        """Heuristic: find entities that look like a revision block / title block table."""
        candidates = []
        for entity in msp:
            text = ""
            if entity.dxftype() == "TEXT":
                text = entity.dxf.text
            elif entity.dxftype() == "MTEXT":
                text = entity.text
            elif entity.dxftype() == "ATTRIB":
                text = entity.dxf.text
            if text and any(k in text.upper() for k in ("REV", "REVISION", "DATE", "DESCRIPTION", "BY")):
                candidates.append(entity)
        return candidates

    def update_revision_block(self, dxf_path: str, annotation: Dict[str, Any], out_dxf: str) -> Dict[str, Any]:
        text = annotation.get("text", "")
        entries = self._extract_revision_entries(text)
        if not entries:
            return {"backend": "ezdxf", "success": False, "error": "Could not parse revision entries"}

        try:
            import ezdxf
            doc = ezdxf.readfile(dxf_path)
            msp = doc.modelspace()
            modified = 0

            table_entities = self._find_revision_table(msp)

            def pos(e):
                try:
                    return (round(e.dxf.insert[1], 2), round(e.dxf.insert[0], 2))
                except Exception:
                    return (0, 0)
            table_entities.sort(key=pos, reverse=True)

            for entity in table_entities:
                ent_text = ""
                if entity.dxftype() == "TEXT":
                    ent_text = entity.dxf.text
                elif entity.dxftype() == "MTEXT":
                    ent_text = entity.text
                if not ent_text:
                    continue
                upper = ent_text.upper()
                if upper.startswith("REV") and entries.get("rev_number"):
                    entity.dxf.text = f"REV {entries['rev_number']}"
                    modified += 1
                elif "DESCRIPTION" in upper and entries.get("description"):
                    entity.dxf.text = entries["description"]
                    modified += 1
                elif upper in ("BY", "DWR.") and entries.get("by"):
                    entity.dxf.text = entries["by"]
                    modified += 1
                elif upper in ("DATE", "CHD.") and entries.get("date"):
                    entity.dxf.text = entries["date"]
                    modified += 1
                elif upper in ("CHK'D", "CHKD") and entries.get("checked"):
                    entity.dxf.text = entries["checked"]
                    modified += 1
                elif upper in ("APPROVED", "APPR.") and entries.get("approved"):
                    entity.dxf.text = entries["approved"]
                    modified += 1

            if modified == 0:
                for entity in msp.query("TEXT MTEXT"):
                    ent_text = entity.dxf.text if entity.dxftype() == "TEXT" else entity.text
                    if ent_text.strip() == entries.get("rev_number", ""):
                        modified += 1
                        break

            doc.saveas(out_dxf)
            return {
                "backend": "ezdxf",
                "success": modified > 0,
                "entities_modified": modified,
                "revision_entries": entries,
            }
        except Exception as e:
            return {"backend": "ezdxf", "success": False, "error": str(e)}

    def _extract_target_and_value(self, annotation: Dict[str, Any]) -> tuple:
        """Naive extraction: 'Replace X with Y' -> (X, Y)."""
        text = annotation.get("text", "")
        lowered = text.lower()
        for sep in [" with ", " to ", " → ", " -> "]:
            if sep in lowered:
                parts = text.split(sep, 1)
                return parts[0].strip(), parts[1].strip()
        return text.strip(), ""

    def replace_text(self, dxf_path: str, annotation: Dict[str, Any], out_dxf: str = None) -> Dict[str, Any]:
        target, new_value = self._extract_target_and_value(annotation)
        if not target or not new_value:
            return {"backend": "ezdxf", "success": False, "error": "Could not parse target/new_value"}

        try:
            import ezdxf
            doc = ezdxf.readfile(dxf_path)
            msp = doc.modelspace()
            count = 0
            for entity in msp.query("TEXT MTEXT"):
                text = entity.dxf.text if entity.dxftype() == "TEXT" else entity.text
                if target in text:
                    if entity.dxftype() == "TEXT":
                        entity.dxf.text = text.replace(target, new_value)
                    else:
                        entity.text = text.replace(target, new_value)
                    count += 1
            doc.saveas(out_dxf or dxf_path)
            return {"backend": "ezdxf", "success": count > 0, "entities_modified": count}
        except Exception as e:
            return {"backend": "ezdxf", "success": False, "error": str(e)}

    def delete_by_text(self, dxf_path: str, annotation: Dict[str, Any], out_dxf: str = None) -> Dict[str, Any]:
        target, _ = self._extract_target_and_value(annotation)
        keywords = [target]
        text_field = annotation.get("text", "")
        if "delete" in text_field.lower():
            keywords = [text_field.replace("delete", "").replace("remove", "").strip()]

        try:
            import ezdxf
            doc = ezdxf.readfile(dxf_path)
            msp = doc.modelspace()
            removed = 0
            for entity in list(msp.query("TEXT MTEXT")):
                text = entity.dxf.text if entity.dxftype() == "TEXT" else entity.text
                if any(kw in text for kw in keywords if kw):
                    msp.delete_entity(entity)
                    removed += 1
            doc.saveas(out_dxf or dxf_path)
            return {"backend": "ezdxf", "success": removed > 0, "entities_deleted": removed}
        except Exception as e:
            return {"backend": "ezdxf", "success": False, "error": str(e)}
