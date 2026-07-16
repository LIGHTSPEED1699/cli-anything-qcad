#!/usr/bin/env python3
"""Verify the VLM-CAD pipeline environment before running tests."""
import sys, subprocess, importlib.util, re, json, requests
from pathlib import Path

ERRORS = []
WARNINGS = []

def check(title, command, expected_in_output=None, critical=True):
    try:
        result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
        text = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            raise RuntimeError(text[:200])
        if expected_in_output and expected_in_output not in text:
            raise RuntimeError(text[:200])
        print(f"  ✓ {title}")
        return True
    except Exception as e:
        msg = f"  ✗ {title}: {e}"
        if critical:
            ERRORS.append(msg)
        else:
            WARNINGS.append(msg)
        print(msg)
        return False

def check_import(module_name, package_name=None, critical=True):
    try:
        __import__(module_name)
        print(f"  ✓ import {module_name} OK")
    except ImportError as e:
        msg = f"  ✗ import {module_name}: {e}"
        if critical:
            ERRORS.append(msg)
        else:
            WARNINGS.append(msg)
        print(msg)

def check_ollama_model(name):
    try:
        r = requests.get("http://localhost:11434/api/tags", timeout=5)
        models = [m["name"] for m in r.json().get("models", [])]
        if name in models:
            print(f"  ✓ Ollama model {name}")
            return True
        msg = f"  ✗ Ollama model {name} NOT FOUND; pulled models: {models}"
        ERRORS.append(msg)
        print(msg)
    except Exception as e:
        msg = f"  ✗ Ollama check failed: {e}"
        ERRORS.append(msg)
        print(msg)

print("=" * 60)
print("VLM-CAD Pipeline Environment Verification")
print("=" * 60)

print("\n1. LibreDWG binaries")
check("dwg2dxf exists", "/media/sdddata1/libredwg/bin/dwg2dxf --help", expected_in_output="Usage")
check("dxf2dwg exists", "/media/sdddata1/libredwg/bin/dxf2dwg --help", expected_in_output="Usage")

print("\n2. QCAD")
check("librecad installed", "which librecad", critical=False)

print("\n3. Python environment")
check_import("ezdxf", critical=True)
check_import("pymupdf", critical=True)
check_import("requests", critical=True)

# Find and validate hermes venv python
venv_python = Path.home() / ".hermes" / "venv" / "bin" / "python3"
if venv_python.exists():
    check("Hermes venv python3", f"{venv_python} -c 'import ezdxf; print(ezdxf.__version__)'", expected_in_output="ezdx")

print("\n4. Ollama & models")
check("Ollama reachable", "curl -s http://localhost:11434", expected_in_output="", critical=False)
check_ollama_model("qwen2.5vl:latest")
check_ollama_model("glm-ocr:latest")

print("\n5. Workspace files")
data_dir = Path.home() / ".hermes" / "kanban" / "workspaces" / "testfiles_2026.05.07"
if data_dir.exists():
    dwgs = list(data_dir.glob("*.dwg"))
    dxfs = list(data_dir.glob("*.dxf"))
    pdfs = list(data_dir.glob("*.pdf"))
    print(f"  ✓ Data dir exists: {len(dwgs)} DWG, {len(dxfs)} DXF, {len(pdfs)} PDF")

print("\n" + "=" * 60)
if ERRORS:
    print(f"FAIL: {len(ERRORS)} critical error(s)")
    for e in ERRORS:
        print(e)
if WARNINGS:
    print(f"WARNINGS: {len(WARNINGS)}")
    for w in WARNINGS:
        print(w)
if not ERRORS:
    print("PASS: Environment ready for VLM-CAD pipeline")
print("=" * 60)

sys.exit(1 if ERRORS else 0)
