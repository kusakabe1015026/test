import json
import os
import subprocess
import sys
import pathlib
from clang.cindex import CursorKind, Index


base_sha = sys.argv[1]
head_sha = sys.argv[2]

WORKSPACE = pathlib.Path(os.environ["GITHUB_WORKSPACE"]).resolve()

TARGET_DIRS = os.environ.get("TARGET_DIRS", "").split()
TARGET_DIRS = [d.strip().strip("/") for d in TARGET_DIRS if d.strip()]


def is_target_file(repo_path: str) -> bool:
    if not TARGET_DIRS:
        return True
    return any(repo_path == d or repo_path.startswith(d + "/") for d in TARGET_DIRS)


def to_repo_path(path: str) -> str:
    p = pathlib.Path(path)

    try:
        return str(p.resolve().relative_to(WORKSPACE)).replace("\\", "/")
    except Exception:
        return str(p).replace("\\", "/")


# ----------------------------
# git diff -> changed lines
# ----------------------------
def get_changed_lines():
    diff = subprocess.check_output(
        ["git", "diff", "--unified=0", base_sha, head_sha],
        text=True,
    )

    result = {}
    current_file = None

    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = to_repo_path(line[6:])
            result.setdefault(current_file, set())
            continue

        if not line.startswith("@@"):
            continue

        if current_file is None:
            continue

        part = line.split(" ")[2]
        start_count = part[1:]

        if "," in start_count:
            start, count = map(int, start_count.split(","))
        else:
            start = int(start_count)
            count = 1

        for n in range(start, start + count):
            result[current_file].add(n)

    print("changed_lines=", result, file=sys.stderr)
    return result


# ----------------------------
# clang AST -> functions
# ----------------------------
def collect_functions(filename):
    print(f"parsing {filename}", file=sys.stderr)

    index = Index.create()
    tu = index.parse(filename, args=["-std=c++17"])

    functions = []

    def walk(node):
        if node.kind in (
            CursorKind.FUNCTION_DECL,
            CursorKind.CXX_METHOD,
            CursorKind.CONSTRUCTOR,
            CursorKind.DESTRUCTOR,
        ):
            loc = node.location

            if loc.file is not None:
                node_file = to_repo_path(loc.file.name)

                if node_file == filename:
                    functions.append(
                        {
                            "name": node.spelling,
                            "line": loc.line,
                        }
                    )

        for child in node.get_children():
            walk(child)

    walk(tu.cursor)

    print(f"{filename}: {len(functions)} functions", file=sys.stderr)
    return functions


# ----------------------------
# main
# ----------------------------
changed_lines = get_changed_lines()
changed_functions = []

for filename, lines in changed_lines.items():

    if not is_target_file(filename):
        continue

    if not filename.endswith((".c", ".cc", ".cpp", ".cxx", ".h", ".hpp")):
        continue

    print(f"checking file={filename} changed_lines={sorted(lines)}", file=sys.stderr)

    try:
        functions = collect_functions(filename)
    except Exception as e:
        print(f"ERROR parsing {filename}: {e}", file=sys.stderr)
        continue

    for fn in functions:
        # ★ 重要：関数開始行が変更行に含まれるか
        if fn["line"] in lines:
            changed_functions.append(
                {
                    "file": filename,
                    "function": fn["name"],
                    "start_line": fn["line"],
                }
            )

print(json.dumps(changed_functions, indent=2))

