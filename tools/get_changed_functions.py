import json
import subprocess
import sys

from clang.cindex import CursorKind
from clang.cindex import Index

base_sha = sys.argv[1]
head_sha = sys.argv[2]


def get_changed_lines():
    diff = subprocess.check_output(
        [
            "git",
            "diff",
            "--unified=0",
            base_sha,
            head_sha,
        ],
        text=True,
    )

    result = {}

    current_file = None

    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            current_file = line[6:]
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

    return result


def collect_functions(filename):
    index = Index.create()

    tu = index.parse(filename)

    functions = []

    def walk(node):
        if node.location.file is None:
            return

        if node.location.file.name != filename:
            return

        if node.kind in (
            CursorKind.FUNCTION_DECL,
            CursorKind.CXX_METHOD,
            CursorKind.CONSTRUCTOR,
            CursorKind.DESTRUCTOR,
        ):
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

    return functions


changed_lines = get_changed_lines()

changed_functions = []

for filename, lines in changed_lines.items():
    if not (
        filename.endswith(".c")
        or filename.endswith(".cc")
        or filename.endswith(".cpp")
        or filename.endswith(".cxx")
        or filename.endswith(".h")
        or filename.endswith(".hpp")
    ):
        continue

    try:
        functions = collect_functions(filename)
    except Exception:
        continue

    for fn in functions:
        if any(
            fn["start"] <= line <= fn["end"]
            for line in lines
        ):
            changed_functions.append(
                {
                    "file": filename,
                    "function": fn["name"],
                    "start_line": fn["start"],
                    "end_line": fn["end"],
                }
            )

print(json.dumps(changed_functions, indent=2))

