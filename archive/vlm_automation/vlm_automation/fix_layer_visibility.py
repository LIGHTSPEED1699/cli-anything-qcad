#!/usr/bin/env python3
"""
Fix DXF layer visibility: turn all layers ON by making color indices positive.
In DXF, group code 62 in the LAYER table controls ON/OFF state:
  positive  = layer ON
  negative  = layer OFF
We strip the negative sign but preserve the color index magnitude.
"""

import sys


def fix_layers_in_dxf(input_path, output_path):
    with open(input_path, 'r') as f:
        lines = f.readlines()

    in_layer_table = False
    in_layer_entry = False
    modified = 0
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Detect start of LAYER table
        if line == 'TABLE' and i + 2 < len(lines):
            if lines[i+1].strip() == '2' and lines[i+2].strip() == 'LAYER':
                in_layer_table = True

        # Detect end of LAYER table
        if in_layer_table and line == 'ENDTAB':
            in_layer_table = False

        # Detect start of a LAYER entry
        if in_layer_table and line == 'LAYER' and i > 0 and lines[i-1].strip() == '0':
            in_layer_entry = True

        # Detect end of a LAYER entry
        if in_layer_entry and line == '0' and i + 1 < len(lines):
            nxt = lines[i+1].strip()
            if nxt in ('LAYER', 'ENDTAB'):
                in_layer_entry = False

        # Fix negative color code within a LAYER entry
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

    with open(output_path, 'w') as f:
        f.writelines(lines)

    print(f"Modified {modified} layer color entries (negative → positive).")
    print(f"Output: {output_path}")


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} input.dxf output.dxf")
        sys.exit(1)
    fix_layers_in_dxf(sys.argv[1], sys.argv[2])
