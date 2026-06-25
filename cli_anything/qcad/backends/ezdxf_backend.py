"""T1 backend: direct DXF edits via ezdxf."""
from typing import Any, Dict


class EzdxfBackend:
    """Execute simple DXF edits using ezdxf."""

    def execute(self, annotation: Dict[str, Any], dxf_path: str) -> Dict[str, Any]:
        """Run the appropriate ezdxf edit for this annotation."""
        # TODO: port dxf_editor.py logic here
        return {
            "backend": "ezdxf",
            "success": False,
            "message": "T1 backend stub: implement per-category actions",
            "annotation": annotation.get("text"),
        }

    def replace_text(self, dxf_path: str, target: str, new_value: str) -> Dict[str, Any]:
        """Replace TEXT/MTEXT content."""
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

    def delete_by_text(self, dxf_path: str, target: str) -> Dict[str, Any]:
        try:
            import ezdxf
            doc = ezdxf.readfile(dxf_path)
            msp = doc.modelspace()
            removed = 0
            for entity in list(msp.query("TEXT MTEXT")):
                text = entity.dxf.text if entity.dxftype() == "TEXT" else entity.text
                if target in text:
                    msp.delete_entity(entity)
                    removed += 1
            doc.saveas(dxf_path)
            return {"backend": "ezdxf", "success": removed > 0, "entities_deleted": removed}
        except Exception as e:
            return {"backend": "ezdxf", "success": False, "error": str(e)}
