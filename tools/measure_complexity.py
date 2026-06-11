import json
import pathlib
import subprocess
import sys
import re
import os


# --------------------------------------------------
# config
# --------------------------------------------------
COMPILE_DB = pathlib.Path("build/compile_commands.json")


# --------------------------------------------------
# changed functions load
# --------------------------------------------------
def load_changed_functions(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------
# normalize path (CI-safe)
# --------------------------------------------------
def normalize_path(p):
    return os.path.basename(str(p))


# --------------------------------------------------
# load compile_commands.json
# --------------------------------------------------
def load_compile_db_sources():
    if not COMPILE_DB.exists():
        raise FileNotFoundError(f"{COMPILE_DB} not found")

    with open(COMPILE_DB, encoding="utf-8") as f:
        db = json.load(f)

    return {
        normalize_path(entry["file"])
        for entry in db
    }


# --------------------------------------------------
# run clang-tidy
# --------------------------------------------------
def run_clang_tidy(file_path):
    cmd = [
        "clang-tidy",
        file_path,
        "-p",
        "build",
        "-checks=readability-function-cognitive-complexity",
        "-config={CheckOptions: [{key: readability-function-cognitive-complexity.Threshold, value: \"0\"}]}",
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    return result.stdout


# --------------------------------------------------
# parse clang-tidy output
# --------------------------------------------------
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


# --------------------------------------------------
# main
# --------------------------------------------------
def main():
    changed = load_changed_functions(sys.argv[1])

    print(f"[INFO] changed functions = {len(changed)}", file=sys.stderr)

    # changed files (basename化)
    changed_files = {
        normalize_path(fn["file"])
        for fn in changed
    }

    compile_sources = load_compile_db_sources()

    # clang-tidy対象
    targets = [
        f for f in changed_files
        if f in compile_sources
    ]

    print(f"[INFO] clang-tidy targets = {len(targets)}", file=sys.stderr)

    all_results = []

    # フルパス復元（compile DBから）
    with open(COMPILE_DB, encoding="utf-8") as f:
        db = json.load(f)

    file_map = {}
    for entry in db:
        file_map[normalize_path(entry["file"])] = entry["file"]

    # clang-tidy実行
    for f in targets:
        real_path = file_map.get(f)
        if not real_path:
            continue

        print(f"[TIDY] {real_path}", file=sys.stderr)

        output = run_clang_tidy(real_path)
        all_results.extend(parse(output, real_path))

    # --------------------------------------------------
    # match changed functions
    # --------------------------------------------------
    output = []

    for fn in changed:
        file = fn["file"]
        name = fn["function"]

        for r in all_results:
            if normalize_path(r["file"]) != normalize_path(file):
                continue

            if r["function"] == name:
                output.append(
                    {
                        **fn,
                        "complexity": r["complexity"],
                    }
                )

    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()

