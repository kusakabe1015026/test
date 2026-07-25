import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"

print("Generating compile_commands.json...")

BUILD.mkdir(exist_ok=True)

subprocess.run(
    [
        "cmake",
        "-S", str(ROOT),
        "-B", str(BUILD),
        "-G", "Ninja",
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
    ],
    check=True,
)

subprocess.run(
    [
        sys.executable,
        str(ROOT / "tools" / "get_changed_functions.py"),
    ],
    check=True,
    cwd=ROOT,
)

subprocess.run(
    [
        sys.executable,
        str(ROOT / "tools" / "measure_complexity.py"),
        str(ROOT / "changed_functions.json"),
    ],
    check=True,
    cwd=ROOT,
)

subprocess.run(
    [
        sys.executable,
        str(ROOT / "tools" / "comment_pr.py"),
        str(ROOT / "complexity.json"),
    ],
    check=True,
    cwd=ROOT,
)