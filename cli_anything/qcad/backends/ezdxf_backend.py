"""T1 backend: direct DXF edits via ezdxf."""
from typing import Any, Dict


class EzdxfBackend:
    """Execute simple DXF edits using ezdxf."""

    def execute(self, annotation: Dict[str, Any], dxf_path: str) -> Dict[str, Any]:
        text = annotation.get("text", "").lower()
        action = annotation.get("inferred_action", "")

        if action == "delete" or "delete" in text or "remove" in text:
            return self.delete_by_text(dxf_path, annotation)

        if action in ("replace", "change_property"):
            return self.replace_text(dxf_path, annotation)

        return {
            "backend": "ezdxf",
            "success": False,
            "message": "No matching T1 action",
            "annotation": annotation.get("text"),
        }

    def _extract_target_and_value(self, annotation: Dict[str, Any]) -> tuple:
        """Naive extraction: 'Replace X with Y' -> (X, Y)."""
        text = annotation.get("text", "")
        lowered = text.lower()
        for sep in [" with ", " to ", " → ", " -> "]:
            if sep in lowered:
                parts = text.split(sep, 1)
                return parts[0].strip(), parts[1].strip()
        return text.strip(), ""

    def replace_text(self, dxf_path: str, annotation: Dict[str, Any]) -> Dict[str, Any]:
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
            doc.saveas(dxf_path)
            return {"backend": "ezdxf", "success": count > 0, "entities_modified": count}
        except Exception as e:
            return {"backend": "ezdxf", "success": False, "error": str(e)}

    def delete_by_text(self, dxf_path: str, annotation: Dict[str, Any]) -> Dict[str, Any]:
        target, _ = self._extract_target_and_value(annotation)
        # For delete, target is the full annotation text usually, so fall back to keyword search
        keywords = [target]
        text_field = annotation.get("text", "")
        if "delete" in text_field.lower():
            # crude: assume subject follows "delete"
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
            doc.saveas(dxf_path)
            return {"backend": "ezdxf", "success": removed > 0, "entities_deleted": removed}
        except Exception as e:
            return {"backend": "ezdxf", "success": False, "error": str(e)}
