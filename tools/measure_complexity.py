import json
import pathlib
import subprocess
import sys
import os
import re
import shutil


# -----------------------------
# config
# -----------------------------
CMAKE_SOURCE_DIR = sys.argv[1] if len(sys.argv) > 1 else "."
BUILD_DIR = pathlib.Path(".analysis_build")
COMPILE_DB = BUILD_DIR / "compile_commands.json"


# -----------------------------
# step 1: generate compile_commands.json
# -----------------------------
def generate_compile_db():
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)

    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        "cmake",
        "-S",
        CMAKE_SOURCE_DIR,
        "-B",
        str(BUILD_DIR),
        "-DCMAKE_EXPORT_COMPILE_COMMANDS=ON",
    ]

    print("[INFO] running cmake:", " ".join(cmd), file=sys.stderr)

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        raise RuntimeError("cmake configure failed")

    if not COMPILE_DB.exists():
        raise RuntimeError("compile_commands.json not generated")

    print("[INFO] compile DB generated at", COMPILE_DB, file=sys.stderr)


# -----------------------------
# step 2: load compile DB
# -----------------------------
def load_sources():
    with open(COMPILE_DB, encoding="utf-8") as f:
        db = json.load(f)

    sources = []
    for entry in db:
        path = entry["file"]
        if path.endswith((".c", ".cc", ".cpp", ".cxx", ".hpp", ".h")):
            sources.append(path)

    return sorted(set(sources))


# -----------------------------
# step 3: run clang-tidy
# -----------------------------
def run_clang_tidy(file_path):
    cmd = [
        "clang-tidy",
        file_path,
        "-p",
        str(BUILD_DIR),
        "-checks=readability-function-cognitive-complexity",
        "-config={CheckOptions: [{key: readability-function-cognitive-complexity.Threshold, value: \"0\"}]}",
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    return result.stdout


# -----------------------------
# step 4: parse output
# -----------------------------
pattern = re.compile(
    r"function '([^']+)' has cognitive complexity of (\d+)"
)


def parse(output, file_path):
    results = []
    for m in pattern.finditer(output):
        results.append(
            {
                "file": file_path,
                "function": m.group(1),
                "complexity": int(m.group(2)),
            }
        )
    return results


# -----------------------------
# main
# -----------------------------
def main():
    generate_compile_db()

    sources = load_sources()

    print(f"[INFO] sources: {len(sources)}", file=sys.stderr)

    all_results = []

    for src in sources:
        print(f"[INFO] analyzing {src}", file=sys.stderr)

        output = run_clang_tidy(src)

        results = parse(output, src)

        for r in results:
            print(f"{r['file']}::{r['function']} = {r['complexity']}")

            all_results.append(r)

    # optional JSON dump for later steps
    with open("complexity.json", "w", encoding="utf-8") as f:
        json.dump(all_results, f, indent=2)


if __name__ == "__main__":
    main()

