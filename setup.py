from setuptools import setup, find_packages

setup(
    name="cli-anything-qcad",
    version="0.2.0",
    description="CLI-Anything harness for QCAD: PDF markup → verified DWG",
    author="Hongbin Li",
    packages=find_packages(),
    python_requires=">=3.9",
    install_requires=[
        "click>=8.0",
        "ezdxf>=1.0",
        "PyMuPDF>=1.23",
        "matplotlib>=3.5",
    ],
    extras_require={
        "dev": ["pytest>=7.0"],
    },
    entry_points={
        "console_scripts": [
            "cli-anything-qcad=cli_anything.qcad.qcad_cli:cli",
        ],
    },
    include_package_data=True,
    package_data={
        "cli_anything.qcad.backends.ecma": ["*.js"],
        "cli_anything.qcad.vendored": ["*.py"],
    },
    zip_safe=False,
)
