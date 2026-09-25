#!/usr/bin/env python3
"""Cherry-pick a merged PR into the version-specific release branch."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


# Version ラベル以外を誤って出荷対象にしないため、形式を厳密に限定する。
VERSION_LABEL_PATTERN = re.compile(r"^Version/([0-9]+\.[0-9]+)$")
# `cherry-pick -x` が残す元コミットの記録を、既反映判定に利用する。
CHERRY_PICK_TRAILER_TEMPLATE = "cherry picked from commit {}"


class CommandError(RuntimeError):
    """Raised when an external command fails."""


class GitHubApiError(RuntimeError):
    """Raised when a GitHub API request fails."""


def run_command(
    command: Sequence[str],
    cwd: Path,
    check: bool = True,
    input_text: Optional[str] = None,
) -> str:
    """Run a command and return its standard output."""
    # Git 操作を一箇所に集約し、失敗時に stderr を Actions のログへ残す。
    result = subprocess.run(
        list(command),
        cwd=str(cwd),
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise CommandError(
            "command failed ({}): {}\n{}".format(
                result.returncode, " ".join(command), detail
            )
        )
    return result.stdout.strip()


def git_remote_branch_exists(repository: Path, branch: str) -> bool:
    """Return whether a branch exists on origin."""
    # 対象ブランチはリリース担当者が手動作成するため、存在しない版を
    # Actions が誤って作成しないよう、push 前にリモートだけを確認する。
    result = subprocess.run(
        [
            "git",
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            "refs/heads/{}".format(branch),
        ],
        cwd=str(repository),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def patch_id(repository: Path, commit: str) -> Optional[str]:
    """Calculate the stable patch-id for a commit."""
    # SHA が異なっても同じ変更内容なら検出できるよう、コミット番号ではなく
    # 差分から patch-id を作る。手動 cherry-pick で -x 記録がない場合の補助である。
    show = subprocess.run(
        ["git", "show", "--format=", "--no-ext-diff", commit],
        cwd=str(repository),
        text=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if show.returncode != 0:
        raise CommandError(show.stderr.decode(errors="replace").strip())

    result = subprocess.run(
        ["git", "patch-id", "--stable"],
        cwd=str(repository),
        input=show.stdout,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if result.returncode != 0:
        raise CommandError(result.stderr.decode(errors="replace").strip())

    line = result.stdout.decode(errors="replace").strip().splitlines()
    if not line:
        return None
    return line[0].split()[0]


def already_applied(repository: Path, remote_ref: str, source_commit: str) -> bool:
    """Check the cherry-pick trailer and then compare patch-ids."""
    # Actions の再実行や複数ラベル処理で同じ変更を二重反映しないための冪等性判定。
    # まず高速で確実な -x 記録を調べ、見つからないときだけ全履歴の差分を比較する。
    log = run_command(
        ["git", "log", remote_ref, "--format=%B"],
        repository,
    ).lower()
    trailer = CHERRY_PICK_TRAILER_TEMPLATE.format(source_commit).lower()
    if trailer in log:
        return True

    source_patch_id = patch_id(repository, source_commit)
    if source_patch_id is None:
        return False

    commits = run_command(
        ["git", "rev-list", "--no-merges", remote_ref],
        repository,
    ).splitlines()
    for commit in commits:
        if patch_id(repository, commit) == source_patch_id:
            return True
    return False


def branch_candidates(version: str) -> List[str]:
    """Return release-stage branch candidates for a version."""
    # 通常出図と緊急出図は同じ Version ラベルとブランチ体系で管理する。
    # 実際に存在する候補がちょうど1つであることは resolve_target_branch で検証する。
    return [
        "verify-e2e/v{}".format(version),
        "pre-production/v{}".format(version),
    ]


def resolve_target_branch(repository: Path, version: str) -> str:
    """Resolve exactly one existing branch for a Version label."""
    # 同じ版の評価用ブランチと出荷候補ブランチを同時に扱うと、意図しない版へ
    # push する危険がある。0個も2個以上も運用違反として止める。
    candidates = [
        branch for branch in branch_candidates(version)
        if git_remote_branch_exists(repository, branch)
    ]
    if not candidates:
        raise RuntimeError(
            "no target branch exists for Version/{} (candidates: {})".format(
                version, ", ".join(branch_candidates(version))
            )
        )
    if len(candidates) != 1:
        raise RuntimeError(
            "multiple target branches exist for Version/{}: {}".format(
                version, ", ".join(candidates)
            )
        )
    return candidates[0]


class GitHubClient:
    """Small GitHub REST API client using only the Python standard library."""

    def __init__(self, api_url: str, repository: str, token: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.repository = repository
        self.token = token

    def request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        """Send a JSON request to the GitHub REST API."""
        # Actions runner に追加パッケージをインストールせず、PAT Secret で
        # PR情報の取得と結果コメントを行うため、標準ライブラリだけを使う。
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            "{}/repos/{}{}".format(self.api_url, self.repository, path),
            data=body,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": "Bearer {}".format(self.token),
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request) as response:
                response_body = response.read().decode("utf-8")
        except (HTTPError, URLError) as error:
            detail = getattr(error, "reason", str(error))
            if isinstance(error, HTTPError):
                detail = error.read().decode("utf-8", errors="replace")
            raise GitHubApiError(
                "GitHub API {} {} failed: {}".format(method, path, detail)
            ) from error

        if not response_body:
            return None
        return json.loads(response_body)

    def get_pull_request(self, number: int) -> Dict[str, Any]:
        """Get a pull request."""
        result = self.request("GET", "/pulls/{}".format(number))
        if not isinstance(result, dict):
            raise GitHubApiError("pull request response was not an object")
        return result

    def get_commit(self, commit: str) -> Dict[str, Any]:
        """Get a commit."""
        result = self.request("GET", "/commits/{}".format(commit))
        if not isinstance(result, dict):
            raise GitHubApiError("commit response was not an object")
        return result

    def post_comment(self, number: int, body: str) -> None:
        """Post a new issue comment for a pull request."""
        self.request("POST", "/issues/{}/comments".format(number), {"body": body})


def parse_version_labels(pull_request: Dict[str, Any]) -> List[str]:
    """Return sorted Version/X.Y labels without the Version/ prefix."""
    # 1つの PR が複数バージョンを対象にする場合があるため、全ラベルを処理する。
    # set で重複を除き、sort で実行順を再現可能にする。
    versions = []
    for label in pull_request.get("labels", []):
        name = label.get("name", "")
        match = VERSION_LABEL_PATTERN.fullmatch(name)
        if match:
            versions.append(match.group(1))
    return sorted(set(versions))


def validate_pull_request(
    pull_request: Dict[str, Any],
    merge_commit: Dict[str, Any],
) -> None:
    """Validate the common conditions for automatic processing."""
    # 手動実行では任意の PR 番号を入力できるため、自動トリガーと同じ条件を
    # スクリプト側でも検証し、誤った PR の出荷ブランチ反映を防止する。
    if pull_request.get("base", {}).get("ref") != "main":
        raise RuntimeError("the PR base branch is not main")
    if not pull_request.get("merged_at"):
        raise RuntimeError("the PR is not merged")
    if not pull_request.get("merge_commit_sha"):
        raise RuntimeError("the PR has no merge commit SHA")

    head_sha = pull_request.get("head", {}).get("sha")
    merge_sha = pull_request.get("merge_commit_sha")
    parents = merge_commit.get("parents", [])
    if head_sha == merge_sha:
        raise RuntimeError("the PR was not squash merged")
    if len(parents) != 1:
        raise RuntimeError("the merge commit is not a single-parent squash commit")


def process_version(
    repository: Path,
    source_commit: str,
    version: str,
) -> Dict[str, str]:
    """Cherry-pick one version into its existing target branch."""
    # 1つの Version ラベルの処理を独立させる。呼び出し側は例外を結果に変換し、
    # ある版の失敗で別の版の処理まで止めない。
    target_branch = resolve_target_branch(repository, version)
    remote_ref = "refs/remotes/origin/{}".format(target_branch)
    run_command(
        [
            "git",
            "fetch",
            "--no-tags",
            "origin",
            "refs/heads/{}:{}".format(target_branch, remote_ref),
        ],
        repository,
    )
    run_command(["git", "fetch", "--no-tags", "origin", source_commit], repository)

    if already_applied(repository, remote_ref, source_commit):
        # 既反映なら push せず、再実行が安全に完了できる状態として扱う。
        return {
            "status": "SKIP",
            "version": version,
            "branch": target_branch,
            "source": source_commit,
            "commit": "",
            "reason": "the source commit is already applied",
        }

    # メインの checkout を直接切り替えず、一時 worktree で変更を隔離する。
    # 複数ラベルを順番に処理しても、次の処理へ作業ツリーの状態を持ち越さない。
    worktree = Path(tempfile.mkdtemp(prefix="auto-cherry-pick-"))
    try:
        run_command(
            ["git", "worktree", "add", "--detach", str(worktree), remote_ref],
            repository,
        )
        run_command(["git", "cherry-pick", "-x", source_commit], worktree)
        run_command(
            ["git", "push", "origin", "HEAD:refs/heads/{}".format(target_branch)],
            worktree,
        )
        # push 後の SHA を PR コメントに残し、出荷対象と反映結果を追跡できるようにする。
        pushed_commit = run_command(["git", "rev-parse", "HEAD"], worktree)
        return {
            "status": "SUCCESS",
            "version": version,
            "branch": target_branch,
            "source": source_commit,
            "commit": pushed_commit,
            "reason": "cherry-pick and push completed",
        }
    except Exception:
        # 競合時に未完了の cherry-pick 状態を残すと cleanup に失敗するため、
        # 作業ツリーを必ず通常状態へ戻してから次の Version 処理へ進む。
        run_command(["git", "cherry-pick", "--abort"], worktree, check=False)
        raise
    finally:
        run_command(
            ["git", "worktree", "remove", "--force", str(worktree)],
            repository,
            check=False,
        )
        shutil.rmtree(worktree, ignore_errors=True)


def comment_for_result(result: Dict[str, str]) -> str:
    """Build a new PR comment for one result."""
    # コメントは毎回新規投稿する仕様のため、後から Actions の実行履歴を追跡できる。
    status = result["status"]
    if status == "SUCCESS":
        return (
            "## Automatic cherry-pick: SUCCESS\n\n"
            "- Version label: `Version/{version}`\n"
            "- Target branch: `{branch}`\n"
            "- Source squash commit: `{source}`\n"
            "- Pushed commit: `{commit}`"
        ).format(**result)
    if status == "SKIP":
        return (
            "## Automatic cherry-pick: SKIP\n\n"
            "- Version label: `Version/{version}`\n"
            "- Target branch: `{branch}`\n"
            "- Source commit: `{source}`\n"
            "- Reason: {reason}"
        ).format(**result)
    if status == "WARNING":
        return (
            "## Automatic cherry-pick: WARNING\n\n"
            "- PR number: `{pr_number}`\n"
            "- Reason: {reason}"
        ).format(**result)
    return (
        "## Automatic cherry-pick: ERROR\n\n"
        "- Version label: `Version/{version}`\n"
        "- Target branch: `{branch}`\n"
        "- Source commit: `{source}`\n"
        "- Reason: {reason}\n"
        "- Manual intervention is required."
    ).format(**result)


def post_result_comment(
    client: GitHubClient,
    pr_number: int,
    result: Dict[str, str],
) -> bool:
    """Post a result comment and return whether it succeeded."""
    # コメント投稿自体の失敗も監査情報の欠落なので、呼び出し側で Actions を失敗にする。
    try:
        client.post_comment(pr_number, comment_for_result(result))
        return True
    except Exception as error:
        print("failed to post result comment: {}".format(error), file=sys.stderr)
        return False


def parse_arguments() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr-number", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    """Run the automatic cherry-pick process."""
    # workflow はイベント処理だけを担当し、実際の対象判定と Git 操作はこの入口から
    # 同じコードパスで実行する。自動実行と手動実行の挙動を一致させるためである。
    arguments = parse_arguments()
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    api_url = os.environ.get("GH_API_URL", "https://api.github.com")
    repository_name = os.environ.get("GH_REPOSITORY")
    repository_path = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd()))

    if not token or not repository_name:
        print("GH_TOKEN and GH_REPOSITORY are required", file=sys.stderr)
        return 1

    client = GitHubClient(api_url, repository_name, token)
    try:
        # merge commit と PR の情報を API から再取得し、workflow のイベント情報だけに
        # 依存しない。手動実行でも同じ検証を適用する。
        pull_request = client.get_pull_request(arguments.pr_number)
        merge_sha = pull_request.get("merge_commit_sha")
        if not merge_sha:
            raise RuntimeError("the PR has no merge commit SHA")
        merge_commit = client.get_commit(merge_sha)
        validate_pull_request(pull_request, merge_commit)
    except Exception as error:
        print("PR validation failed: {}".format(error), file=sys.stderr)
        return 1

    versions = parse_version_labels(pull_request)
    if not versions:
        warning = {
            "status": "WARNING",
            "pr_number": str(arguments.pr_number),
            "version": "N/A",
            "branch": "N/A",
            "source": merge_sha,
            "reason": "no Version/X.Y label was found",
        }
        if not post_result_comment(client, arguments.pr_number, warning):
            print("failed to post missing-label warning", file=sys.stderr)
        return 0

    # 1件の Version で失敗しても、同じ PR の他バージョンを処理して結果を集計する。
    failed = False
    for version in versions:
        try:
            result = process_version(repository_path, merge_sha, version)
        except Exception as error:
            failed = True
            result = {
                "status": "ERROR",
                "version": version,
                "branch": "N/A",
                "source": merge_sha,
                "reason": str(error),
            }
        if not post_result_comment(client, arguments.pr_number, result):
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
