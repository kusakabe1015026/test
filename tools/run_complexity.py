import subprocess
import sys

subprocess.run(
    [sys.executable, "tools/get_changed_functions.py"],
    check=True,
)

subprocess.run(
    [sys.executable, "tools/measure_complexity.py", "changed_functions.json"],
    check=True,
)

subprocess.run(
    [sys.executable, "tools/comment_pr.py", "complexity.json"],
    check=True,
)
