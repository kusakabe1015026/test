import json
import pathlib
import subprocess
import sys
import re
import os


COMPILE_DB = pathlib.Path("build/compile_commands.json")
OUTPUT_FILE = "complexity.json"


def normalize(p):
    return os.path.basename(str(p))


def load_changed(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_compile_db():
    with open(COMPILE_DB, encoding="utf-8") as f:
        db = json.load(f)

    file_map = {}
    for e in db:
        file_map[normalize(e["file"])] = e["file"]

    return file_map


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


pattern = re.compile(
    r"function '([^']+)' has cognitive complexity of (\d+)"
)


def parse(output, file_path):
    out = []
    for m in pattern.finditer(output):
        out.append({
            "file": file_path,
            "function": m.group(1),
            "complexity": int(m.group(2)),
        })
    return out


def main():
    changed = load_changed(sys.argv[1])

    changed_files = {normalize(f["file"]) for f in changed}

    file_map = load_compile_db()

    results = []

    for f in changed_files:
        if f not in file_map:
            continue

        real = file_map[f]

        print(f"[TIDY] {real}", file=sys.stderr)

        out = run_clang_tidy(real)
        results.extend(parse(out, real))

    # ★重要：ここで必ずファイル出力
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    print(f"[INFO] wrote {OUTPUT_FILE} ({len(results)} entries)", file=sys.stderr)


if __name__ == "__main__":
    main()

