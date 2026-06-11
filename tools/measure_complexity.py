import json
import re
import subprocess
import sys

with open(sys.argv[1]) as f:
    changed_functions = json.load(f)

files = sorted(
    {
        item["file"]
        for item in changed_functions
    }
)

results = []

for filename in files:
    proc = subprocess.run(
        [
            "clang-tidy",
            filename,
            "-checks=-*,readability-function-cognitive-complexity",
            "-config={CheckOptions:[{key: readability-function-cognitive-complexity.Threshold,value: 0}]}",
        ],
        capture_output=True,
        text=True,
    )

    output = proc.stdout + proc.stderr

    for line in output.splitlines():
        m = re.search(
            r"function '([^']+)' has cognitive complexity of (\d+)",
            line,
        )

        if not m:
            continue

        results.append(
            {
                "function": m.group(1),
                "complexity": int(m.group(2)),
                "file": filename,
            }
        )

changed_names = {
    (item["file"], item["function"])
    for item in changed_functions
}

filtered = [
    item
    for item in results
    if (item["file"], item["function"]) in changed_names
]

print(json.dumps(filtered, indent=2))

