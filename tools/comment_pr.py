import json
import os
import subprocess
import sys


REPO = os.environ["GITHUB_REPOSITORY"]
SHA = os.environ["GITHUB_SHA"]  # or head.shaでもOK
PR_NUMBER = os.environ["PR_NUMBER"]


with open(sys.argv[1]) as f:
    functions = json.load(f)


def make_link(file, line):
    return f"https://github.com/{REPO}/blob/{SHA}/{file}#L{line}"


body = "## Cognitive Complexity Report\n\n"

if not functions:
    body += "No changed functions detected.\n"
else:
    for fn in functions:
        url = make_link(fn["file"], fn["start_line"])

        body += (
            f"- [`{fn['function']}`]({url}) "
            f"(`{fn['file']}:{fn['start_line']}`) "
            f"complexity={fn.get('complexity','?')}\n"
        )


subprocess.run(
    [
        "gh",
        "pr",
        "comment",
        PR_NUMBER,
        "--body",
        body,
    ],
    check=True,
)

