import json
import pathlib
import subprocess
import sys
import re


# --------------------------------------------------
# load changed functions
# --------------------------------------------------
def load_changed_functions(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------
# load compile database sources (for validation only)
# --------------------------------------------------
def load_compile_db_sources():
    with open("build/compile_commands.json", encoding="utf-8") as f:
        db = json.load(f)

    return {
        entry["file"]
        for entry in db
    }


# --------------------------------------------------
# run clang-tidy on single file
# --------------------------------------------------
def run_clang_tidy(file_path):
    cmd = [
        "clang-tidy",
        file_path,
        "-p",
        ".",
        "-checks=readability-function-cognitive-complexity",
        "-config={CheckOptions: [{key: readability-function-cognitive-complexity.Threshold, value: \"0\"}]}",
    ]

    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    return result.stdout


# --------------------------------------------------
# parse clang-tidy output
# --------------------------------------------------
pattern = re.compile(
    r"function '([^']+)' has cognitive complexity of (\d+)"
)


def parse(output, file_path):
    results = []

    for m in pattern.finditer(output):
        results.append(
            {
                "file": file_path,
                "function": m.group(1),
                "complexity": int(m.group(2)),
            }
        )

    return results


# --------------------------------------------------
# main logic
# --------------------------------------------------
def main():
    changed = load_changed_functions(sys.argv[1])

    if not changed:
        print("[]")
        return

    # 対象ファイルだけ抽出
    changed_files = {
        fn["file"] for fn in changed
    }

    print(f"[INFO] changed files = {len(changed_files)}", file=sys.stderr)

    compile_sources = load_compile_db_sources()

    # compile DBに存在するものだけ実行
    targets = [
        f for f in changed_files
        if f in compile_sources
    ]

    print(f"[INFO] clang-tidy targets = {len(targets)}", file=sys.stderr)

    all_results = []

    # ファイル単位で解析（ここが最適化ポイント）
    for f in targets:
        print(f"[TIDY] {f}", file=sys.stderr)

        output = run_clang_tidy(f)

        all_results.extend(parse(output, f))

    # changed_functions と突合（関数名ベース）
    filtered = []

    for fn in changed:
        file = fn["file"]
        name = fn["function"]

        for r in all_results:
            if r["file"] != file:
                continue

            if r["function"] == name:
                filtered.append(
                    {
                        **fn,
                        "complexity": r["complexity"],
                    }
                )

    # output to stdout
    print(json.dumps(filtered, indent=2))


if __name__ == "__main__":
    main()

