"""Developer-only: assemble a private, isolated CPython runtime from a Windows x64 install."""
import argparse
from pathlib import Path
import shutil
import sys
import zipfile


def build(python_root, package):
    runtime = package / "runtime"
    dlls = runtime / "DLLs"
    dlls.mkdir(parents=True, exist_ok=True)
    version = "python{}{}".format(sys.version_info.major, sys.version_info.minor)
    for name in ["python.exe", "python3.dll", version + ".dll", "vcruntime140.dll", "vcruntime140_1.dll"]:
        shutil.copy2(python_root / name, runtime / name)
    shutil.copy2(python_root / "LICENSE.txt", runtime / "PYTHON-LICENSE.txt")
    extensions = ["_sqlite3", "_decimal", "_ctypes", "select", "_socket", "_overlapped", "_asyncio",
                  "_queue", "unicodedata", "_wmi", "_uuid", "_elementtree", "pyexpat",
                  "_multiprocessing", "_zoneinfo", "_bz2", "_lzma", "_zstd"]
    for name in [name + ".pyd" for name in extensions] + ["sqlite3.dll", "libffi-8.dll", "zlib1.dll"]:
        source = python_root / "DLLs" / name
        if source.is_file():
            shutil.copy2(source, dlls / name)
    excluded = {"site-packages", "__pycache__", "test", "tests", "tkinter", "idlelib", "turtledemo",
                "ensurepip", "venv", "__phello__"}
    with zipfile.ZipFile(runtime / (version + ".zip"), "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for source in sorted((python_root / "Lib").rglob("*.py")):
            relative = source.relative_to(python_root / "Lib")
            if not excluded.intersection(relative.parts):
                archive.write(source, relative.as_posix())
    # No site import: ignore user site-packages, Python environment overrides and registry paths.
    (runtime / (version + "._pth")).write_text(version + ".zip\nDLLs\n..\n", encoding="utf-8")
    print("Portable runtime built at " + str(runtime))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python-root", type=Path, default=Path(sys.base_prefix))
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    build(args.python_root.resolve(), args.output.resolve())
