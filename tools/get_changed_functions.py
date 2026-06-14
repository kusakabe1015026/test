import json
import os
import subprocess
import sys
from clang.cindex import CursorKind, Index
from clang.cindex import CompilationDatabase
import pathlib


base_sha = sys.argv[1]
head_sha = sys.argv[2]

WORKSPACE = pathlib.Path(os.environ["GITHUB_WORKSPACE"]).resolve()

# ★ 追加：解析対象ディレクトリ（repo rootからの相対）
# 例: "src include"
TARGET_DIRS = os.environ.get("TARGET_DIRS", "").split()
TARGET_DIRS = [d.strip().strip("/") for d in TARGET_DIRS if d.strip()]


def is_target_file(repo_path: str) -> bool:
    if not TARGET_DIRS:
        return True

    return any(
        repo_path == d or repo_path.startswith(d + "/")
        for d in TARGET_DIRS
    )


def to_repo_path(path: str) -> str:
    p = pathlib.Path(path)

    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
        p = pathlib.Path(path)

    try:
        p = p.resolve()
        return str(p.relative_to(WORKSPACE)).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


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


def collect_functions(filename):
    print(f"parsing {filename}", file=sys.stderr)

    cdb = CompilationDatabase.fromDirectory("build")
    commands = cdb.getCompileCommands(filename)

    for cmd in commands:
        args = list(cmd.arguments)[1:] #コンパイラ名を除く
        index = Index.create()
        tu = index.parse(filename, args=args)
        break

    for diag in tu.diagnostics:
        print(diag, file=sys.stderr)

    functions = []

    def walk(node):
        if node.location.file is None:
            for child in node.get_children():
                walk(child)
            return

        node_file = to_repo_path(node.location.file.name)

        if node_file == filename:
            if node.kind in (
                CursorKind.FUNCTION_DECL,
                CursorKind.CXX_METHOD,
                CursorKind.CONSTRUCTOR,
                CursorKind.DESTRUCTOR,
            ):
                print(
                    f"{node.kind} {node.spelling}",
                    file=sys.stderr,
                )
                functions.append(
                    {
                        "name": node.spelling,
                        "start": node.extent.start.line,
                        "end": node.extent.end.line,
                    }
                )

        for child in node.get_children():
            walk(child)

    walk(tu.cursor)

    print(f"{filename}: {len(functions)} functions", file=sys.stderr)
    return functions


changed_lines = get_changed_lines()

changed_functions = []

for filename, lines in changed_lines.items():

    # ★ここが本体：ディレクトリフィルタ
    if not is_target_file(filename):
        continue

    if not filename.endswith(
        (".c", ".cc", ".cpp", ".cxx", ".h", ".hpp")
    ):
        continue

    print(
        f"checking file={filename} changed_lines={sorted(lines)}",
        file=sys.stderr,
    )

    try:
        functions = collect_functions(filename)
    except Exception as e:
        print(f"ERROR parsing {filename}: {e}", file=sys.stderr)
        continue

    for fn in functions:
        matched = any(
            fn["start"] <= line <= fn["end"]
            for line in lines
        )

        if matched:
            changed_functions.append(
                {
                    "file": filename,
                    "function": fn["name"],
                    "start_line": fn["start"],
                    "end_line": fn["end"],
                }
            )

print(json.dumps(changed_functions, indent=2))

