#!/usr/bin/env python3
"""mainにマージされたPRを特定のブランチへcherry-pickする。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import shlex
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class CommandError(RuntimeError):
    """外部コマンドが失敗した場合に発生する。"""


class GitHubApiError(RuntimeError):
    """GitHub APIリクエストが失敗した場合に発生する。"""


def run_command(
    command: Sequence[str],
    cwd: Path,
    check: bool = True,
    input_text: Optional[str] = None,
) -> str:
    """コマンドを実行し、標準出力を返す。"""
    command_text = shlex.join(list(command))

    print(
        "コマンドを実行中: cwd={} command={}".format(cwd, command_text),
        file=sys.stderr,
        flush=True,
    )

    result = subprocess.run(
        list(command),
        cwd=str(cwd),
        input=input_text,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    stdout = result.stdout.strip()
    stderr = result.stderr.strip()

    if result.returncode != 0:
        print(
            "コマンドが失敗しました: cwd={} returncode={} command={}".format(
                cwd,
                result.returncode,
                command_text,
            ),
            file=sys.stderr,
            flush=True,
        )
        print(
            "コマンド標準出力:\n{}".format(stdout or "<empty>"),
            file=sys.stderr,
            flush=True,
        )
        print(
            "コマンド標準エラー出力:\n{}".format(stderr or "<empty>"),
            file=sys.stderr,
            flush=True,
        )

        if check:
            raise CommandError(
                "コマンドが失敗しました ({}): {}\n標準出力:\n{}\n標準エラー出力:\n{}".format(
                    result.returncode,
                    command_text,
                    stdout or "<empty>",
                    stderr or "<empty>",
                )
            )

    return stdout


def git_remote_branch_exists(repository: Path, branch: str) -> bool:
    """originにcherry-pick先のブランチが存在するかを返す。"""
    # 対象ブランチはリリース担当者が手動作成するため、存在しない版を
    # Actionsが誤って作成しないよう、push前にリモートだけを確認する。
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


def already_applied(repository: Path, remote_ref: str, source_commit: str) -> bool:
    """Actionsの再実行や複数ラベル処理で同じ変更を二重反映しないための冪等性判定を行う。"""
    # `cherry-pick -x`が残す元コミットの記録を直近200件のログから検索し、既反映判定に利用する。
    log = run_command(
        ["git", "log", "-n", "200", remote_ref, "--format=%B"],
        repository,
    ).lower()
    trailer = "cherry picked from commit {}".format(source_commit).lower()
    if trailer in log:
        return True

    return False


def branch_candidates(version: str) -> List[str]:
    """Versionラベルに対応するブランチ候補を返す。"""
    # 通常出図と緊急出図は同じVersionラベルとブランチ体系で管理する。
    # 実際に存在する候補がちょうど1つであることはresolve_target_branchで検証する。
    return [
        "verify-e2e/v{}".format(version),
        "pre-production/v{}".format(version),
    ]


def resolve_target_branch(repository: Path, version: str) -> str:
    """Versionラベルに対応する、存在するブランチを1つだけ特定する。"""
    # 同じ版の評価用ブランチと出荷候補ブランチを同時に扱うと、意図しない版へ
    # pushする危険がある。0個も2個以上も運用違反として止める。
    candidates = [
        branch for branch in branch_candidates(version)
        if git_remote_branch_exists(repository, branch)
    ]
    if not candidates:
        raise RuntimeError(
            "Version/{}に対応する対象ブランチが存在しません (候補: {})".format(
                version, ", ".join(branch_candidates(version))
            )
        )
    if len(candidates) != 1:
        raise RuntimeError(
            "Version/{}に対応する対象ブランチが複数存在します: {}".format(
                version, ", ".join(candidates)
            )
        )
    return candidates[0]


class GitHubClient:
    """Python標準ライブラリだけを使用する小規模なGitHub REST APIクライアント。"""

    def __init__(self, api_url: str, repository: str, token: str) -> None:
        self.api_url = api_url.rstrip("/")
        self.repository = repository
        self.token = token

    def request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Any:
        """GitHub REST APIへJSONリクエストを送信する。"""
        # Actions runnerに追加パッケージをインストールせず、PAT Secretで
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
                "GitHub API {} {}が失敗しました: {}".format(method, path, detail)
            ) from error

        if not response_body:
            return None
        return json.loads(response_body)

    def get_pull_request(self, number: int) -> Dict[str, Any]:
        """プルリクエストを取得する。"""
        result = self.request("GET", "/pulls/{}".format(number))
        if not isinstance(result, dict):
            raise GitHubApiError("プルリクエストのレスポンスがオブジェクトではありません")
        return result

    def get_commit(self, commit: str) -> Dict[str, Any]:
        """コミットを取得する。"""
        result = self.request("GET", "/commits/{}".format(commit))
        if not isinstance(result, dict):
            raise GitHubApiError("コミットのレスポンスがオブジェクトではありません")
        return result

    def post_comment(self, number: int, body: str) -> None:
        """プルリクエストに新しいコメントを投稿する。"""
        self.request("POST", "/issues/{}/comments".format(number), {"body": body})


def parse_version_labels(pull_request: Dict[str, Any]) -> List[str]:
    """Version/X.Y形式のラベルからVersion/プレフィックスを除いた値をソートして返す。"""
    # 1つのPRが複数バージョンを対象にする場合があるため、全ラベルを処理する。
    # setで重複を除き、sortで実行順を再現可能にする。
    versions = []
    for label in pull_request.get("labels", []):
        name = label.get("name", "")
        # Versionラベル以外を誤って対象にしないため、形式を厳密に限定する。
        version_label_pattern = re.compile(r"^Version/([0-9]+\.[0-9]+)$")
        match = version_label_pattern.fullmatch(name)
        if match:
            versions.append(match.group(1))
    return sorted(set(versions))


def validate_pull_request(
    pull_request: Dict[str, Any],
    merge_commit: Dict[str, Any],
) -> None:
    """自動cherry-pickに必要な共通条件を検証する。"""
    # 手動実行では任意のPR番号を入力できるため、自動トリガーと同じ条件を
    # スクリプト側でも検証し、誤ったPRの出荷ブランチ反映を防止する。
    if pull_request.get("base", {}).get("ref") != "main":
        raise RuntimeError("PRのベースブランチがmainではありません")
    if not pull_request.get("merged_at"):
        raise RuntimeError("PRがマージされていません")
    if not pull_request.get("merge_commit_sha"):
        raise RuntimeError("PRにマージコミットSHAがありません")

    head_sha = pull_request.get("head", {}).get("sha")
    merge_sha = pull_request.get("merge_commit_sha")
    parents = merge_commit.get("parents", [])
    if head_sha == merge_sha:
        raise RuntimeError("PRがSquashマージされていません")
    if len(parents) != 1:
        raise RuntimeError("マージコミットが単一親のSquashコミットではありません")


def cherry_pick(
    repository: Path,
    source_commit: str,
    version: str,
    target_branch: str,
) -> Dict[str, str]:
    """指定したコミットを指定したバージョンの既存ブランチへcherry-pickする。"""
    # 1つのVersionラベルの処理を独立させる。呼び出し側は例外を結果に変換し、
    # ある版の失敗で別の版の処理まで止めない。
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
        # 既反映ならpushせず、再実行が安全に完了できる状態として扱う。
        return {
            "status": "SKIP",
            "version": version,
            "branch": target_branch,
            "source": source_commit,
            "commit": "",
            "reason": "すでにcherry-pick済みです",
        }

    # メインのcheckoutを直接切り替えず、一時worktreeで変更を隔離する。
    # 複数ラベルを順番に処理しても、次の処理へ作業ツリーの状態を持ち越さない。
    worktree = Path(tempfile.mkdtemp(prefix="auto-cherry-pick-"))
    try:
        run_command(
            ["git", "worktree", "add", "--detach", str(worktree), remote_ref],
            repository,
        )
        try:
            run_command(["git", "cherry-pick", "-x", source_commit], worktree)
        except CommandError as exc:
            # 既に同じ変更が入っている/空コミットになっているケース
            msg = str(exc).lower()
            if "nothing to commit" in msg or "the previous cherry-pick is now empty" in msg:
                run_command(["git", "cherry-pick", "--abort"], worktree, check=False)
                return {
                    "status": "SKIP",
                    "version": version,
                    "branch": target_branch,
                    "source": source_commit,
                    "commit": "",
                    "reason": "すでにcherry-pick済みか、cherry-pickする対象がありません",
                }

            # conflictのときは実際のエラーを残す
            print("競合が発生したため、{}を{}へcherry-pickできませんでした:".format(source_commit, target_branch), file=sys.stderr)
            run_command(["git", "status", "--short"], worktree, check=False)
            raise
        run_command(
            ["git", "push", "origin", "HEAD:refs/heads/{}".format(target_branch)],
            worktree,
        )
        # push後のSHAをPRコメントに残し、出荷対象と反映結果を追跡できるようにする。
        pushed_commit = run_command(["git", "rev-parse", "HEAD"], worktree)
        return {
            "status": "SUCCESS",
            "version": version,
            "branch": target_branch,
            "source": source_commit,
            "commit": pushed_commit,
            "reason": "cherry-pickとpushが完了しました",
        }
    except Exception:
        # 競合時に未完了のcherry-pick状態を残すとcleanupに失敗するため、
        # 作業ツリーを必ず通常状態へ戻してから次のVersion処理へ進む。
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
    """1件の結果に対する新しいPRコメントを作成する。"""
    # コメントは毎回新規投稿する仕様のため、後からActionsの実行履歴を追跡できる。
    status = result["status"]
    if status == "SUCCESS":
        return (
            "## 🍒自動cherry-pick結果: 成功✅\n\n"
            "- versionラベル: `Version/{version}`\n"
            "- cherry-pick先ブランチ: `{branch}`\n"
            "- cherry-pick元コミット: {source}\n"
            "- pushしたコミット: {commit}"
        ).format(**result)
    if status == "SKIP":
        return (
            "## 🍒自動cherry-pick結果: スキップ⏭\n\n"
            "- versionラベル: `Version/{version}`\n"
            "- cherry-pick先ブランチ: `{branch}`\n"
            "- cherry-pick元コミット: {source}\n"
            "- 理由: {reason}"
        ).format(**result)
    if status == "WARNING":
        return (
            "## 🍒自動cherry-pick結果: 警告⚠\n\n"
            "- PR番号: `{pr_number}`\n"
            "- 理由: {reason}"
        ).format(**result)
    return (
        "## 🍒自動cherry-pick結果: 失敗❌\n\n"
        "- versionラベル: `Version/{version}`\n"
        "- cherry-pick先ブランチ: `{branch}`\n"
        "- cherry-pick元コミット: {source}\n"
        "- 理由: {reason}\n"
        "- **必ず手動でのcherry-pickを実施してください**"
    ).format(**result)


def post_result_comment(
    client: GitHubClient,
    pr_number: int,
    result: Dict[str, str],
) -> bool:
    """結果コメントを投稿し、成功したかどうかを返す。"""
    # コメント投稿自体の失敗も監査情報の欠落なので、呼び出し側でActionsを失敗にする。
    try:
        client.post_comment(pr_number, comment_for_result(result))
        return True
    except Exception as error:
        print("結果コメントの投稿に失敗しました: {}".format(error), file=sys.stderr)
        return False


def parse_arguments() -> argparse.Namespace:
    """コマンドライン引数を解析する。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("--pr-number", type=int, required=True)
    return parser.parse_args()


def main() -> int:
    """自動cherry-pick処理を実行する。"""
    # workflowはイベント処理だけを担当し、実際の対象判定とGit操作はこの入口から
    # 同じコードパスで実行する。自動実行と手動実行の挙動を一致させるためである。
    arguments = parse_arguments()
    token = os.environ.get("GH_TOKEN")
    api_url = os.environ.get("GH_API_URL", "https://api.github.com")
    repository_name = os.environ.get("GH_REPOSITORY")
    repository_path = Path(os.environ.get("GITHUB_WORKSPACE", Path.cwd()))

    if not token or not repository_name:
        print("GH_TOKENとGH_REPOSITORYが必要です", file=sys.stderr)
        return 1

    client = GitHubClient(api_url, repository_name, token)
    try:
        # merge commitとPRの情報をAPIから再取得し、workflowのイベント情報だけに依存しない。
        # 手動実行でも同じ検証を適用する。
        pull_request = client.get_pull_request(arguments.pr_number)
        merge_sha = pull_request.get("merge_commit_sha")
        if not merge_sha:
            raise RuntimeError("PRにマージコミットSHAがありません")
        merge_commit = client.get_commit(merge_sha)
        validate_pull_request(pull_request, merge_commit)
    except Exception as error:
        print("PRの検証に失敗しました: {}".format(error), file=sys.stderr)
        return 1

    versions = parse_version_labels(pull_request)
    if not versions:
        warning = {
            "status": "WARNING",
            "pr_number": str(arguments.pr_number),
            "version": "N/A",
            "branch": "N/A",
            "source": merge_sha,
            "reason": "Versionラベルが見つかりませんでした",
        }
        if not post_result_comment(client, arguments.pr_number, warning):
            print("Versionラベルがない旨の警告コメントの投稿に失敗しました", file=sys.stderr)
        return 0

    # あるのVersionで失敗しても、同じPRの他Versionを処理を継続して結果を集計する。
    failed = False
    for version in versions:
        try:
            target_branch = resolve_target_branch(repository_path, version)
            result = cherry_pick(repository_path, merge_sha, version, target_branch)
        except Exception as error:
            failed = True
            result = {
                "status": "ERROR",
                "version": version,
                "branch": target_branch,
                "source": merge_sha,
                "reason": str(error),
            }
        if not post_result_comment(client, arguments.pr_number, result):
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
