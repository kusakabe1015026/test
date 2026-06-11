
rt json
import os
import subprocess
import sys


REPO = os.environ["GITHUB_REPOSITORY"]
SHA = os.environ["GITHUB_SHA"]
PR_NUMBER = os.environ["PR_NUMBER"]


def get_repo_root():
    return subprocess.check_output(
        ["git", "rev-parse", "--show-toplevel"],
        text=True
    ).strip()


def to_repo_path(path):
    repo_root = get_repo_root()

    path = str(path)

    if path.startswith(repo_root):
        return path[len(repo_root):].lstrip("/")

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
    env={"GH_TOKEN": os.environ["GH_TOKEN"]},
)

