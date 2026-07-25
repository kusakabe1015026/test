import json
import os
import subprocess
import sys
import pathlib


REPO = os.environ.get("GITHUB_REPOSITORY")
SHA = os.environ.get("GITHUB_SHA")
PR_NUMBER = os.environ.get("PR_NUMBER")
WORKSPACE = pathlib.Path(
    os.environ.get("GITHUB_WORKSPACE", ".")
).resolve()


def to_repo_path(path):
    p = pathlib.Path(path).resolve()
    try:
        return str(p.relative_to(WORKSPACE)).replace("\\", "/")
    except Exception:
        return str(p).replace("\\", "/")


def make_link(file, line):
    if not (REPO and SHA):
        return None

    file = to_repo_path(file)
    return f"https://github.com/{REPO}/blob/{SHA}/{file}#L{line}"


with open(sys.argv[1]) as f:
    functions = json.load(f)


body = "## Cognitive Complexity Report\n\n"

if not functions:
    body += "No changed functions detected.\n"
else:
    for fn in functions:
        file_path = to_repo_path(fn["file"])
        url = make_link(fn["file"], fn["line"])

        if url:
            body += (
                f"- [`{fn['function']}`]({url}) "
                f"({file_path}:{fn['line']}) "
                f"complexity={fn['complexity']}\n"
            )
        else:
            body += (
                f"- {fn['function']} "
                f"({file_path}:{fn['line']}) "
                f"complexity={fn['complexity']}\n"
            )


if REPO and SHA and PR_NUMBER:
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
else:
    print(body)

