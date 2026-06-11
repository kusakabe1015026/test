import json
import os
import subprocess
import sys


path = sys.argv[1]

if not os.path.exists(path):
    print("complexity.json not found")
    sys.exit(0)

with open(path) as f:
    functions = json.load(f)

body = "## Cognitive Complexity Report\n\n"

if not functions:
    body += "No warnings.\n"
else:
    for fn in functions:
        body += (
            f"- `{fn['function']}` "
            f"({fn['file']}) complexity={fn['complexity']}\n"
        )

subprocess.run(
    [
        "gh",
        "pr",
        "comment",
        os.environ["PR_NUMBER"],
        "--body",
        body,
    ],
    check=True,
)

