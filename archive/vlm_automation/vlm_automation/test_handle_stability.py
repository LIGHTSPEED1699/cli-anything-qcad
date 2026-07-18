#!/usr/bin/env python3
"""
Test whether QCAD Pro preserves entity handles across DWG→DWG import/export.

This is a prerequisite for Option C (direct DWG handle-based deletion).
If handles are NOT stable, the JSON target list becomes stale after export
and handle-based deletion will silently delete wrong entities.

Usage:
    python3 test_handle_stability.py <input.dwg> [--qcaddir /path/to/qcad]

Exit codes:
    0 = handles stable (safe for Option C)
    1 = handles changed (Option C unsafe, use spatial+text matching instead)
    2 = test infrastructure failure

Requires: QCAD Pro 3.32+ with headless ECMAScript support.
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import textwrap


QCADDIR_DEFAULT = os.path.expanduser("$HOME/opt/qcad")

QCAD_SCRIPT = textwrap.dedent(r'''
    include("scripts/library.js");
    function main() {
        var inputFile = args[args.length - 2];
        var outputFile = args[args.length - 1];
        if (!inputFile) { qcad.quit(2); return; }
        if (!outputFile) { outputFile = inputFile.replace(/\.dwg$/i, "_roundtrip.dwg"); }
        var fi = new QFileInfo(inputFile);
        if (!fi.isAbsolute()) { inputFile = RSettings.getLaunchPath() + QDir.separator + inputFile; }

        var storage = new RMemoryStorage();
        var spatialIndex = new RSpatialIndexSimple();
        var doc = new RDocument(storage, spatialIndex);
        var di = new RDocumentInterface(doc);
        var rc = di.importFile(inputFile, "DWG");
        if (rc !== RDocumentInterface.IoErrorNoError) {
            qWarning("IMPORT_FAILED," + rc);
            qcad.quit(2);
            return;
        }

        // Dump all handles before export
        var allEntities = doc.queryAllEntities();
        var handlesBefore = [];
        for (var i = 0; i < allEntities.length; i++) {
            var e = doc.queryEntity(allEntities[i]);
            if (e) { handlesBefore.push({id: allEntities[i], handle: e.getHandle().getValueString(), type: e.getType()}); }
        }

        // Export
        if (!di.exportFile(outputFile, "DWG")) {
            qWarning("EXPORT_FAILED");
            qcad.quit(2);
            return;
        }

        // Re-import and dump handles after
        var doc2 = new RDocument(new RMemoryStorage(), new RSpatialIndexSimple());
        var di2 = new RDocumentInterface(doc2);
        rc = di2.importFile(outputFile, "DWG");
        if (rc !== RDocumentInterface.IoErrorNoError) {
            qWarning("REIMPORT_FAILED," + rc);
            qcad.quit(2);
            return;
        }
        var allEntities2 = doc2.queryAllEntities();
        var handlesAfter = [];
        for (var j = 0; j < allEntities2.length; j++) {
            var f = doc2.queryEntity(allEntities2[j]);
            if (f) { handlesAfter.push({id: allEntities2[j], handle: f.getHandle().getValueString(), type: f.getType()}); }
        }

        // Compare handle sets (ignore object ID, compare by handle string)
        var beforeMap = {};
        var afterMap = {};
        for (var a = 0; a < handlesBefore.length; a++) { beforeMap[handlesBefore[a].handle] = handlesBefore[a]; }
        for (var b = 0; b < handlesAfter.length; b++) { afterMap[handlesAfter[b].handle] = handlesAfter[b]; }

        var beforeOnly = [];
        for (var h in beforeMap) { if (!(h in afterMap)) { beforeOnly.push(h); } }
        var afterOnly = [];
        for (var hh in afterMap) { if (!(hh in beforeMap)) { afterOnly.push(hh); } }

        print("HANDLE_STABILITY_REPORT," + handlesBefore.length + "," + handlesAfter.length + "," + beforeOnly.length + "," + afterOnly.length);
        if (beforeOnly.length > 0) { print("LOST_HANDLES," + beforeOnly.join(";")); }
        if (afterOnly.length > 0) { print("NEW_HANDLES," + afterOnly.join(";")); }

        if (beforeOnly.length === 0 && afterOnly.length === 0) {
            print("STABLE: all " + handlesBefore.length + " handles preserved.");
            qcad.quit(0);
        } else {
            print("UNSTABLE: " + beforeOnly.length + " lost, " + afterOnly.length + " new.");
            qcad.quit(1);
        }
    }
    if (typeof(including) === 'undefined' || including === false) { main(); }
''')


def run_qcad_roundtrip(dwg_path, qcaddir):
    """Run the ECMAScript roundtrip test via QCAD headless."""
    env = os.environ.copy()
    env["QCADDIR"] = qcaddir
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["LD_LIBRARY_PATH"] = f"{qcaddir}:{qcaddir}/plugins"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".js", delete=False) as f:
        f.write(QCAD_SCRIPT)
        script_path = f.name

    with tempfile.NamedTemporaryFile(mode="w", suffix=".dwg", delete=False) as f:
        output_path = f.name

    cmd = [
        f"{qcaddir}/qcad-bin",
        "-no-gui",
        "-platform", "offscreen",
        "-allow-multiple-instances",
        "-autostart", script_path,
        dwg_path,
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
    os.unlink(script_path)

    return result


def parse_report(stdout, stderr):
    """Parse the HANDLE_STABILITY_REPORT line from QCAD stdout."""
    for line in stdout.splitlines() + stderr.splitlines():
        if line.startswith("HANDLE_STABILITY_REPORT,"):
            parts = line.split(",")
            return {
                "before": int(parts[1]),
                "after": int(parts[2]),
                "lost": int(parts[3]),
                "new": int(parts[4]),
            }
        if "STABLE:" in line:
            return {"stable": True}
        if "UNSTABLE:" in line:
            return {"stable": False}
    return None


def main():
    parser = argparse.ArgumentParser(description="Test QCAD handle stability")
    parser.add_argument("dwg", help="Input DWG file")
    parser.add_argument("--qcaddir", default=QCADDIR_DEFAULT, help="Path to QCAD Pro installation")
    args = parser.parse_args()

    if not os.path.isfile(args.dwg):
        print(f"ERROR: not found: {args.dwg}", file=sys.stderr)
        sys.exit(2)

    result = run_qcad_roundtrip(args.dwg, args.qcaddir)
    print("=== QCAD stdout ===")
    print(result.stdout)
    if result.returncode == 0:
        print("\n✅ HANDLES STABLE — Option C (direct DWG deletion) is safe.")
        sys.exit(0)
    else:
        report = parse_report(result.stdout, result.stderr)
        if report and report.get("stable") is False:
            print("\n⚠️  HANDLES UNSTABLE — Option C may delete wrong entities.")
            print("Fallback: use spatial+text matching (slower but safe).")
            sys.exit(1)
        else:
            print(f"\n❌ Test failed (exit={result.returncode}):")
            print(result.stderr)
            sys.exit(2)


if __name__ == "__main__":
    main()
