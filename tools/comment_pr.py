import json
import os
import subprocess
import sys

with open(sys.argv[1]) as f:
    functions = json.load(f)

body = "## Changed Functions\n\n"

if not functions:
    body += "No changed functions detected.\n"
else:
    for fn in functions:
        body += (
            f"- `{fn['function']}` "
            f"({fn['file']}:{fn['start_line']})\n"
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

