import json
import os
import subprocess
import sys


REPO = os.environ["GITHUB_REPOSITORY"]
SHA = os.environ["GITHUB_SHA"]
PR_NUMBER = os.environ["PR_NUMBER"]
WORKSPACE = os.environ["GITHUB_WORKSPACE"]

def to_repo_path(path):
    path = os.path.abspath(path)
    workspace = os.path.abspath(WORKSPACE)

    if path.startswith(workspace):
        return os.path.relpath(path, workspace)

    return path


def make_link(file, line):
    file = to_repo_path(file)
    return f"https://github.com/{REPO}/blob/{SHA}/{file}#L{line}"


with open(sys.argv[1]) as f:
    functions = json.load(f)


body = "## Cognitive Complexity Report\n\n"

if not functions:
    body += "No changed functions detected.\n"
else:
    for fn in functions:
        url = make_link(fn["file"], fn["line"])

        body += (
            f"- [`{fn['function']}`]({url}) "
            f"({fn['file']}:{fn['line']}) "
            f"complexity={fn['complexity']}\n"
        )


env = os.environ.copy()
env["GH_TOKEN"] = os.environ["GH_TOKEN"]

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
    env=env,
)

