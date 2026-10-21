"""Audit an applied operation list against the file that was actually written.

"ok" from the script means "the call did not throw". It does not mean the change is
in the file. Two real bugs lived in that gap: a delete that used the wrong apply call,
and a text added without a height, which exports with empty text.

So every op gets checked against the exported DWG, re-read through QCAD, and the
audit decides success. A caller that wants to be sure must look at the audit.
"""
from typing import Any, Dict, List, Optional, Tuple


def _find_by_handle(rows: List[Dict[str, Any]], handle: Any) -> Optional[Dict[str, Any]]:
    for r in rows:
        if str(r.get("h")) == str(handle):
            return r
    return None


def _find_by_text(rows: List[Dict[str, Any]], text: str) -> List[Dict[str, Any]]:
    needle = (text or "").lower()
    return [r for r in rows if r.get("t") and needle in r["t"].lower()]


def _near(a: Optional[float], b: Optional[float], tol: float) -> bool:
    if a is None or b is None:
        return False
    return abs(a - b) <= tol


def audit_ops(ops: List[Dict[str, Any]],
              before_rows: List[Dict[str, Any]],
              after_rows: List[Dict[str, Any]],
              coord_tol: float = 0.01) -> Dict[str, Any]:
    """Check every op against the re-read output file.

    before_rows / after_rows are entity indexes of the input and output drawings.
    """
    results: List[Dict[str, Any]] = []
    for op in ops:
        kind = op.get("op")
        check: Dict[str, Any] = {"op": kind, "verified": False, "detail": ""}

        if kind == "delete":
            handle = op.get("handle")
            was = _find_by_handle(before_rows, handle)
            now = _find_by_handle(after_rows, handle)
            if was is None:
                check["detail"] = "handle not present in the input"
            elif now is not None:
                check["detail"] = "handle still present in the output"
            else:
                check["verified"] = True
                check["detail"] = "removed"

        elif kind == "set_text":
            handle = op.get("handle")
            now = _find_by_handle(after_rows, handle)
            if now is None:
                check["detail"] = "handle missing from the output"
            elif now.get("t") == op.get("text"):
                check["verified"] = True
                check["detail"] = f"text is now {now.get('t')!r}"
            else:
                check["detail"] = f"text is {now.get('t')!r}, expected {op.get('text')!r}"

        elif kind == "move":
            handle = op.get("handle")
            before = _find_by_handle(before_rows, handle)
            now = _find_by_handle(after_rows, handle)
            if before is None or now is None:
                check["detail"] = "handle missing from before or after"
            else:
                want_x = (before.get("x") or 0) + op.get("dx", 0)
                want_y = (before.get("y") or 0) + op.get("dy", 0)
                if _near(now.get("x"), want_x, coord_tol) and _near(now.get("y"), want_y, coord_tol):
                    check["verified"] = True
                    check["detail"] = f"moved to ({now.get('x')}, {now.get('y')})"
                else:
                    check["detail"] = (f"at ({now.get('x')}, {now.get('y')}), "
                                       f"expected ({want_x}, {want_y})")

        elif kind == "add_text":
            hits = _find_by_text(after_rows, op.get("text", ""))
            if not hits:
                check["detail"] = "no entity carries this text"
            elif not _near(hits[0].get("x"), op.get("x"), coord_tol) or \
                    not _near(hits[0].get("y"), op.get("y"), coord_tol):
                check["detail"] = (f"text is at ({hits[0].get('x')}, {hits[0].get('y')}), "
                                   f"expected ({op.get('x')}, {op.get('y')})")
            else:
                check["verified"] = True
                check["detail"] = f"present at ({hits[0].get('x')}, {hits[0].get('y')})"

        elif kind in ("add_line", "add_box"):
            # Geometry ops are checked by count, not identity.
            if kind == "add_box":
                want = 4
                got = sum(1 for r in after_rows if r not in before_rows)
                check["verified"] = got >= want
                check["detail"] = f"{got} new entities, expected at least {want}"
            else:
                # a line shows up as new endpoints at the requested coords
                hit = any(_near(r.get("x1"), op.get("x1"), coord_tol) and
                           _near(r.get("y1"), op.get("y1"), coord_tol)
                           for r in after_rows)
                check["verified"] = hit
                check["detail"] = "endpoint found" if hit else "no line at the requested start"

        else:
            check["detail"] = f"no audit rule for op {kind!r}"

        results.append(check)

    failed = [r for r in results if not r["verified"]]
    return {
        "passed": not failed,
        "checked": len(results),
        "verified": len(results) - len(failed),
        "failed": failed,
        "results": results,
    }
