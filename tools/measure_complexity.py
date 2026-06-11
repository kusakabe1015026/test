import json
import pathlib
import re
import subprocess

import os

print("PWD =", os.getcwd())
print("compile_commands.json exists =", os.path.exists("compile_commands.json"))

def load_sources():
    with open("compile_commands.json", encoding="utf-8") as f:
        db = json.load(f)

    return sorted(
        {
            entry["file"]
            for entry in db
            if pathlib.Path(entry["file"]).suffix.lower()
            in (".c", ".cc", ".cpp", ".cxx", ".c++")
        }
    )


def run_clang_tidy(file_path):
    result = subprocess.run(
        [
            "clang-tidy",
            file_path,
            "-p",
            ".",
            "-checks=readability-function-cognitive-complexity",
            "-config={CheckOptions: [{key: readability-function-cognitive-complexity.Threshold, value: \"0\"}]}",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    print("stdout=", result.stdout)

    return result.stdout


def parse_complexity(output):
    return [
        {
            "function": m.group(1),
            "complexity": int(m.group(2)),
        }
        for m in re.finditer(
            r"function '([^']+)' has cognitive complexity of (\d+)",
            output,
        )
    ]


def collect_complexity():
    results = []

    sources = load_sources()

    for src in sources:
        for item in parse_complexity(run_clang_tidy(src)):
            item["file"] = src
            results.append(item)

    print("=== RESULTS DEBUG ===")
    print(results)
    print("COUNT =", len(results))

    return results

