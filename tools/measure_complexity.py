import json
import pathlib
import subprocess
import sys
import re
import os


COMPILE_DB = pathlib.Path("build/compile_commands.json")
OUTPUT_FILE = "complexity.json"


# ----------------------------
# util
# ----------------------------
def norm(p):
    return os.path.basename(str(p))


# ----------------------------
# load changed functions
# ----------------------------
def load_changed(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ----------------------------
# load compile DB
# ----------------------------
def load_compile_db():
    with open(COMPILE_DB, encoding="utf-8") as f:
        db = json.load(f)

    file_map = {}
    for e in db:
        file_map[norm(e["file"])] = e["file"]

    return file_map


# ----------------------------
# clang-tidy run
# ----------------------------
def run_clang_tidy(file_path):
    cmd = [
        "clang-tidy",
        file_path,
        "-p",
        "build",
        "-checks=readability-function-cognitive-complexity",
        "-config={CheckOptions: [{key: readability-function-cognitive-complexity.Threshold, value: \"0\"}]}",
    ]

    r = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    return r.stdout


# ----------------------------
# parse clang-tidy output
# IMPORTANT: extract line number
# ----------------------------
pattern = re.compile(
    r"(.+?):(\d+):\d+: warning: function '([^']+)' has cognitive complexity of (\d+)"
)


def parse(output):
    results = []

    for m in pattern.finditer(output):
        results.append({
            "file": m.group(1),
            "line": int(m.group(2)),
            "function": m.group(3),
            "complexity": int(m.group(4)),
        })

    return results


# ----------------------------
# check if changed function
# ----------------------------
def is_changed(r, changed_map):
    file = norm(r["file"])

    if file not in changed_map:
        return False

    for start, end, _ in changed_map[file]:
        if start <= r["line"] <= end:
            return True

    return False


# ----------------------------
# main
# ----------------------------
def main():
    changed = load_changed(sys.argv[1])

    print(f"[INFO] changed functions = {len(changed)}", file=sys.stderr)

    # file -> [(start,end,function)]
    changed_map = {}

    changed_files = set()

    for fn in changed:
        file = norm(fn["file"])
        changed_files.add(file)

        changed_map.setdefault(file, []).append(
            (fn["start_line"], fn["end_line"], fn["function"])
        )

    file_map = load_compile_db()

    targets = [
        f for f in changed_files
        if f in file_map
    ]

    print(f"[INFO] clang-tidy targets = {len(targets)}", file=sys.stderr)

    results = []

    for f in targets:
        real = file_map[f]

        print(f"[TIDY] {real}", file=sys.stderr)

        out = run_clang_tidy(real)
        results.extend(parse(out))
        print(out, file=sys.stderr)

    # ----------------------------
    # FINAL FILTER (ここが本体)
    # ----------------------------
    filtered = [
        r for r in results
        if is_changed(r, changed_map)
    ]

    # ----------------------------
    # write output
    # ----------------------------
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(filtered, f, indent=2)

    print(f"[INFO] wrote {OUTPUT_FILE}: {len(filtered)} entries", file=sys.stderr)


if __name__ == "__main__":
    main()

