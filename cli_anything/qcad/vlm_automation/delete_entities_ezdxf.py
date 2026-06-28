"""Delete DXF entities by handle using ezdxf."""
from pathlib import Path
from typing import Iterable
import ezdxf


def delete_entities_by_handles(dxf_in: str, dxf_out: str, handles: Iterable[str]) -> int:
    """Remove all entities whose group-code-5 handle matches the given set."""
    doc = ezdxf.readfile(dxf_in)
    handles = {str(h).upper() for h in handles}
    modelspace = doc.modelspace()
    deleted = 0
    for entity in list(modelspace):
        if str(entity.dxf.handle).upper() in handles:
            modelspace.delete_entity(entity)
            deleted += 1
    doc.saveas(dxf_out)
    return deleted


if __name__ == "__main__":
    import json, sys
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} input.dxf output.dxf handles.json")
        sys.exit(1)
    with open(sys.argv[3]) as f:
        handles = json.load(f)
    n = delete_entities_by_handles(sys.argv[1], sys.argv[2], handles)
    print(f"Deleted {n} entities from modelspace by handle")
