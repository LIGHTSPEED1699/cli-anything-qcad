## Layer-diagnostic script

`scripts/qcad_layer_diagnostic.js` — QCAD Pro headless ECMAScript that imports a DXF/DWG and prints every layer's name, color code (positive/negative), ON/OFF state, and FROZEN/THAWED state.

```
export QCADDIR=/home/hongbin/opt/qcad-3.32.7-pro-linux-qt6-x86_64
LD_LIBRARY_PATH="$QCADDIR:$QCADDIR/plugins" \
  $QCADDIR/qcad-bin -no-gui -platform offscreen -allow-multiple-instances \
  -autostart $(realpath ~/.hermes/skills/data-science/vlm-cad-automation/scripts/qcad_layer_diagnostic.js) \
  path/to/file.dwg
```

Use to quickly determine whether blank-canvas behavior is caused by frozen layers, negative color OFF, or both.
