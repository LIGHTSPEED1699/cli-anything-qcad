/**
 * Headless DXF to DWG converter for QCAD Pro — NO layer manipulation.
 * Use this when the input DXF has already been fixed (e.g. by fix_layer_visibility.py)
 * and you only need a clean DWG export via QCAD's ODA engine.
 *
 * Usage:
 *   export QCADDIR="$HOME/opt/qcad-3.32.7-pro-linux-qt6-x86_64"
 *   export QT_QPA_PLATFORM=offscreen
 *   export LD_LIBRARY_PATH="$QCADDIR:$QCADDIR/plugins"
 *   "$QCADDIR/qcad-bin" -no-gui -platform offscreen -allow-multiple-instances \
 *     -autostart qcad_dxf2dwg.js input.dxf output.dwg
 */
include("scripts/library.js");

function main() {
    // args[]: [qcad flags..., script path, input.dxf, output.dwg]
    var inputFile  = args[args.length - 2];
    var outputFile = args[args.length - 1];

    if (!inputFile || !outputFile || inputFile.indexOf("-") === 0) {
        print("Usage: qcad-bin -autostart qcad_dxf2dwg.js <input.dxf> <output.dwg>");
        qcad.quit(1);
        return;
    }

    // Resolve relative paths against launch dir
    if (!new QFileInfo(inputFile).isAbsolute()) {
        inputFile = RSettings.getLaunchPath() + QDir.separator + inputFile;
    }
    if (!new QFileInfo(outputFile).isAbsolute()) {
        outputFile = RSettings.getLaunchPath() + QDir.separator + outputFile;
    }

    print("Converting DXF -> DWG via QCAD Pro ODA engine");
    print("  from: " + inputFile);
    print("  to  : " + outputFile);

    var storage = new RMemoryStorage();
    var spatialIndex = new RSpatialIndexSimple();
    var doc = new RDocument(storage, spatialIndex);
    var di = new RDocumentInterface(doc);

    print("Importing DXF...");
    var importResult = di.importFile(inputFile);
    if (importResult !== RDocumentInterface.IoErrorNoError) {
        qWarning("ERROR: Cannot import DXF (code " + importResult + ")");
        qcad.quit(1);
        return;
    }
    print("  Imported. Entities: " + doc.queryAllEntities().length);
    print("  Layers: " + doc.queryAllLayers().length);

    // Fix layer visibility: force all layers ON before DWG export.
    // The QCAD ODA DWG writer re-introduces negative (OFF) layer colors
    // even when the input DXF has positive (ON) colors. This makes cloned
    // entities on layers like E-SYMB and E-TEXT invisible in the output DWG.
    // Without this fix, cloned wires/labels are present in the DXF but
    // invisible in the rendered DWG — VLM verification then falsely reports
    // missing wires at target terminals.
    print("Fixing layer visibility (force all layers ON)...");
    var layerIds = doc.queryAllLayers();
    var layerOp = new RModifyObjectsOperation();
    var layerFixCount = 0;
    for (var li = 0; li < layerIds.length; li++) {
        var layer = doc.queryLayer(layerIds[li]);
        if (!layer) continue;
        if (layer.isOff()) { layer.setOff(false); layerOp.addObject(layer, false); layerFixCount++; }
        if (layer.isFrozen()) { layer.setFrozen(false); layerOp.addObject(layer, false); layerFixCount++; }
        if (layer.isLocked()) { layer.setLocked(false); layerOp.addObject(layer, false); layerFixCount++; }
    }
    if (layerFixCount > 0) {
        di.applyOperation(layerOp);
        print("  Fixed " + layerFixCount + " layer properties (OFF/Frozen/Locked → ON)");
    } else {
        print("  All layers already ON/Unfrozen/Unlocked");
    }

    print("Exporting DWG...");
    var formats = ["DWG R32 (2018)", "R32 (2018) DWG", "DWG", "R32"];
    var success = false;
    for (var i = 0; i < formats.length; i++) {
        if (di.exportFile(outputFile, formats[i])) {
            print("  Exported with format: " + formats[i]);
            success = true;
            break;
        }
    }

    if (!success) {
        qWarning("ERROR: All DWG export attempts failed.");
        qcad.quit(1);
        return;
    }

    print("SUCCESS: " + outputFile);
    if (typeof(QCoreApplication) !== 'undefined') {
        QCoreApplication.quit(0);
    }
}

if (typeof(including) === 'undefined' || including === false) {
    main();
}
