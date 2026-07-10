"""Fix DXF layer visibility by flipping negative color codes to positive.

Also upgrades DXF version from AC1015 (AutoCAD 2000) to AC1032 (AutoCAD 2018)
to prevent the QCAD/ODA DWG converter from flipping layer colors back to
negative (OFF) during DXF→DWG conversion.  AC1015 DXFs are particularly
susceptible to this color-flipping behavior.
"""
from pathlib import Path


def fix_layer_visibility(input_path: str, output_path: str) -> int:
    """Turn all layers ON by making LAYER table color indices positive.

    Also upgrades DXF version header from AC1015 to AC1032 to prevent
    the DWG converter from flipping colors back to negative.
    """
    path = Path(input_path)
    lines = path.read_text().splitlines(keepends=True)

    modified = 0
    version_upgraded = False
    in_layer_table = False
    in_layer_entry = False
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Upgrade DXF version: AC1015 → AC1032 (prevents DWG converter color flip)
        if line == "$ACADVER" and i + 1 < len(lines):
            val_line = lines[i + 1].strip()
            if val_line == "AC1015":
                lines[i + 1] = lines[i + 1].replace("AC1015", "AC1032")
                version_upgraded = True

        if line == 'TABLE' and i + 2 < len(lines):
            if lines[i+1].strip() == '2' and lines[i+2].strip() == 'LAYER':
                in_layer_table = True
        if in_layer_table and line == 'ENDTAB':
            in_layer_table = False
        if in_layer_table and line == 'LAYER' and i > 0 and lines[i-1].strip() == '0':
            in_layer_entry = True
        if in_layer_entry and line == '0' and i + 1 < len(lines):
            nxt = lines[i+1].strip()
            if nxt in ('LAYER', 'ENDTAB'):
                in_layer_entry = False
        if in_layer_table and line == '62':
            if i + 1 < len(lines):
                val_str = lines[i+1].strip()
                try:
                    val = int(val_str)
                    if val < 0:
                        lines[i+1] = str(abs(val)) + '\n'
                        modified += 1
                except ValueError:
                    pass
        i += 1

    Path(output_path).write_text(''.join(lines))
    if version_upgraded:
        print(f"[layer_fix] DXF version upgraded AC1015 → AC1032")
    return modified
