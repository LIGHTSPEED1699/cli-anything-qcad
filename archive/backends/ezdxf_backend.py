"""T1 backend: direct DXF edits via ezdxf (ported from dxf_editor.py)."""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cli_anything.qcad.utils.dxf_entity_index import DxfEntityIndex


class EzdxfBackend:
    """Execute simple DXF edits using ezdxf."""

    def __init__(self):
        self.editor = None

    def execute(self, annotation: Dict[str, Any], dxf_path: str, out_dxf: str = None) -> Dict[str, Any]:
        text = annotation.get("text", "")
        lowered = text.lower()
        action = annotation.get("inferred_action", "")
        category = annotation.get("category", "")
        target_path = out_dxf or dxf_path

        editor = DXFEditor(dxf_path)
        if not editor.load():
            return {"backend": "ezdxf", "success": False, "error": "Failed to load DXF"}

        # Revision-block additions/updates
        if category == "text_change" or action in ("replace", "change") or "rev \"" in lowered or "revision" in lowered:
            result = editor.update_revision_block(text)
            editor.save(target_path)
            return {**result, "backend": "ezdxf"}

        # Text replacement: "Change X to Y", "Replace X with Y"
        target, new_value = self._extract_target_and_value(text)
        if target and new_value:
            result = editor.replace_text(target, new_value)
            editor.save(target_path)
            return {**result, "backend": "ezdxf"}

        # Text-based deletion
        if action == "delete" or "delete" in lowered or "remove" in lowered:
            keywords = [target or text]
            result = editor.delete_by_keywords(keywords)
            editor.save(target_path)
            return {**result, "backend": "ezdxf"}

        return {
            "backend": "ezdxf",
            "success": False,
            "message": "No matching T1 action",
            "annotation": text,
        }

    @staticmethod
    def _extract_target_and_value(text: str) -> Tuple[str, str]:
        lowered = text.lower()
        for sep in [" with ", " to ", " → ", " -> "]:
            if sep in lowered:
                parts = text.split(sep, 1)
                return parts[0].strip(), parts[1].strip()
        return text.strip(), ""


class DXFEditor:
    """Edit DXF files directly using ezdxf (ported from QCAD-VLM-automation)."""

    def __init__(self, dxf_path: str):
        self.dxf_path = dxf_path
        self.doc = None
        self.msp = None
        self.index = None

    def load(self) -> bool:
        try:
            import ezdxf
            self.doc = ezdxf.readfile(self.dxf_path)
            self.msp = self.doc.modelspace()
            self.index = DxfEntityIndex(self.dxf_path)
            self.index.load()
            return True
        except Exception as e:
            print(f"ERROR: Failed to load DXF: {e}")
            return False

    def _find_entity(self, target_text: str) -> Optional["DxfEntity"]:
        exact = self.index.search_exact(target_text)
        if exact:
            return exact[0]
        fuzzy = self.index.search_fuzzy(target_text, threshold=0.6)
        if fuzzy:
            return fuzzy[0][0]
        import re
        m = re.match(r'^([A-Za-z]{2,})(\d{3,})$', target_text)
        if m:
            alt = f"{m.group(1)}-{m.group(2)}"
            exact = self.index.search_exact(alt)
            if exact:
                return exact[0]
        return None

    def _get_dxf_entity(self, handle: str):
        try:
            return self.doc.entitydb.get(handle)
        except Exception:
            return None

    def replace_text(self, target_text: str, new_value: str) -> Dict[str, Any]:
        entity = self._find_entity(target_text)
        if not entity:
            # Fallback: substring replacement across all TEXT/MTEXT
            return self._replace_text_substring(target_text, new_value)

        dxf_ent = self._get_dxf_entity(entity.handle)
        if not dxf_ent:
            return {"success": False, "action": "replace_text", "error": f"Handle {entity.handle} not found"}

        etype = dxf_ent.dxftype()
        try:
            if etype in ('TEXT', 'MTEXT'):
                old_text = getattr(dxf_ent.dxf, 'text', '')
                if etype == 'TEXT':
                    dxf_ent.dxf.text = new_value
                else:
                    dxf_ent.text = new_value
                return {
                    "success": True, "action": "replace_text",
                    "target_text": target_text, "new_value": new_value,
                    "entity_handle": entity.handle, "entity_type": etype,
                    "old_text": old_text, "entities_modified": 1,
                }
            elif etype == 'INSERT':
                if new_value in self.doc.blocks:
                    old_block = dxf_ent.dxf.name
                    dxf_ent.dxf.name = new_value
                    return {
                        "success": True, "action": "replace_block",
                        "target_text": target_text, "new_value": new_value,
                        "entity_handle": entity.handle, "entity_type": etype,
                        "old_block": old_block, "entities_modified": 1,
                    }
                return {"success": False, "action": "replace_text", "error": f"Block '{new_value}' not found"}
            else:
                return {"success": False, "action": "replace_text", "error": f"Unsupported entity type {etype}"}
        except Exception as e:
            return {"success": False, "action": "replace_text", "error": str(e)}

    def _replace_text_substring(self, target_text: str, new_value: str) -> Dict[str, Any]:
        try:
            count = 0
            for entity in self.msp.query("TEXT MTEXT"):
                text = entity.dxf.text if entity.dxftype() == "TEXT" else entity.text
                if target_text in text:
                    if entity.dxftype() == "TEXT":
                        entity.dxf.text = text.replace(target_text, new_value)
                    else:
                        entity.text = text.replace(target_text, new_value)
                    count += 1
            return {"success": count > 0, "action": "replace_text_substring", "target_text": target_text, "new_value": new_value, "entities_modified": count}
        except Exception as e:
            return {"success": False, "action": "replace_text", "error": str(e)}

    def delete_by_keywords(self, keywords: List[str]) -> Dict[str, Any]:
        try:
            removed = 0
            for entity in list(self.msp.query("TEXT MTEXT")):
                text = entity.dxf.text if entity.dxftype() == "TEXT" else entity.text
                if any(kw and kw in text for kw in keywords):
                    self.msp.delete_entity(entity)
                    removed += 1
            return {"success": removed > 0, "action": "delete_by_keywords", "keywords": keywords, "entities_deleted": removed}
        except Exception as e:
            return {"success": False, "action": "delete", "error": str(e)}

    def update_revision_block(self, text: str) -> Dict[str, Any]:
        entries = self._extract_revision_entries(text)
        if not entries:
            return {"success": False, "action": "update_revision_block", "error": "Could not parse revision entries"}

        table_entities = self._find_revision_table(self.msp)

        def pos(e):
            try:
                return (round(e.dxf.insert[1], 2), round(e.dxf.insert[0], 2))
            except Exception:
                return (0, 0)

        table_entities.sort(key=pos, reverse=True)
        modified = 0

        for entity in table_entities:
            ent_text = ""
            if entity.dxftype() == "TEXT":
                ent_text = entity.dxf.text
            elif entity.dxftype() == "MTEXT":
                ent_text = entity.text
            elif entity.dxftype() == "ATTRIB":
                ent_text = entity.dxf.text
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

        return {"success": modified > 0, "action": "update_revision_block", "entities_modified": modified, "revision_entries": entries}

    def _extract_revision_entries(self, text: str) -> Dict[str, str]:
        result = {}
        text = re.sub(r"^(Add|Update|Change)\s+REV\s*", "", text, flags=re.IGNORECASE)
        parts = [p.strip().strip('"').strip("'") for p in re.split(r'",\s*"|",\s*|\"', text) if p.strip()]
        keys = ["rev_number", "description", "by", "date", "checked", "approved"]
        for i, p in enumerate(parts[:len(keys)]):
            result[keys[i]] = p
        return result

    def _find_revision_table(self, msp) -> List[Any]:
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

    def save(self, output_path: Optional[str] = None) -> str:
        if output_path is None:
            output_path = self.dxf_path
        if output_path == self.dxf_path and os.path.exists(output_path):
            shutil.copy2(output_path, output_path + ".backup")
        self.doc.saveas(output_path)
        return output_path

    def move_entity(self, target_text: str, new_x: float, new_y: float) -> Dict[str, Any]:
        entity = self._find_entity(target_text)
        if not entity:
            return {"success": False, "action": "move", "error": f"Entity not found for '{target_text}'"}
        dxf_ent = self._get_dxf_entity(entity.handle)
        if not dxf_ent:
            return {"success": False, "action": "move", "error": f"Handle {entity.handle} not found"}
        etype = dxf_ent.dxftype()
        try:
            if etype in ('TEXT', 'MTEXT', 'INSERT'):
                dxf_ent.move_to(new_x, new_y)
                return {"success": True, "action": "move", "entity_handle": entity.handle, "entity_type": etype, "new_coords": (new_x, new_y)}
            elif etype == 'DIMENSION':
                dxf_ent.dxf.text_midpoint = (new_x, new_y)
                return {"success": True, "action": "move", "entity_handle": entity.handle, "entity_type": etype, "new_coords": (new_x, new_y)}
            else:
                return {"success": False, "action": "move", "error": f"Unsupported type {etype}"}
        except Exception as e:
            return {"success": False, "action": "move", "error": str(e)}
