from setuptools import setup, find_packages

setup(
    name="cli-anything-qcad",
    version="0.1.0",
    description="CLI-Anything harness for QCAD: PDF markup → verified DWG",
    author="Hongbin Li",
    url="https://github.com/LIGHTSPEED1699/cli-anything-qcad",
    packages=find_packages(),
    python_requires=">=3.10",
    install_requires=[
        "click>=8.0",
        "ezdxf>=1.0",
        "pymupdf>=1.23",
        "Pillow>=10.0",
    ],
    extras_require={
        "dev": ["pytest>=7.0"],
    },
    entry_points={
        "console_scripts": [
            "cli-anything-qcad=cli_anything.qcad.qcad_cli:entrypoint",
        ],
    },
)
