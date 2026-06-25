"""Fix DXF layer visibility by flipping negative color codes to positive."""
from pathlib import Path


def fix_layer_visibility(input_path: str, output_path: str) -> int:
    """Turn all layers ON by making LAYER table color indices positive."""
    path = Path(input_path)
    lines = path.read_text().splitlines(keepends=True)

    in_layer_table = False
    in_layer_entry = False
    modified = 0
    i = 0

    while i < len(lines):
        line = lines[i].strip()
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
    return modified
