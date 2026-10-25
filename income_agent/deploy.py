from __future__ import annotations

import subprocess
from pathlib import Path


class DeployError(RuntimeError):
    pass


def _run(cmd: list[str], cwd: Path) -> str:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, errors="replace")
    except FileNotFoundError as e:
        raise DeployError(f"{cmd[0]} is not installed") from e
    if proc.returncode != 0:
        raise DeployError(f"{' '.join(cmd[:3])} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def _git(repo: Path, *args: str) -> str:
    return _run(["git", *args], repo)


def _repo_root(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    try:
        return Path(_git(path, "rev-parse", "--show-toplevel"))
    except DeployError as e:
        raise DeployError(
            f"{path} is not inside a git repository (run `income-agent init-site` first)") from e


def _require_remote(repo: Path) -> None:
    if not _git(repo, "remote"):
        raise DeployError(f"{repo} has no git remote; add one with `git remote add origin <url>`")


def git_publish(site_dir: Path, files: list[Path], message: str, push: bool = True) -> str:
    """Commits `files` on the current branch and pushes, which triggers the
    hosting provider's build (GitHub Pages Actions, Cloudflare Pages, Netlify, ...)."""
    repo = _repo_root(site_dir)
    paths = [str(f.resolve()) for f in files]
    _git(repo, "add", "--", *paths)
    if not _git(repo, "status", "--porcelain", "--", *paths):
        return "変更がないためコミットをスキップしました。"
    _git(repo, "commit", "-m", message, "--", *paths)
    if not push:
        return "コミットしました（push は無効）。"
    _require_remote(repo)
    _git(repo, "push", "origin", "HEAD")
    return "コミットして push しました。数分後にサイトへ反映されます。"


def github_pull_request(site_dir: Path, files: list[Path], branch: str, title: str,
                        body: str) -> str:
    """Commits `files` on a new branch, pushes it and opens a GitHub pull request,
    so that merging the PR is the approval. Uses the `gh` CLI (preinstalled on
    GitHub Actions runners; authenticated through GH_TOKEN)."""
    repo = _repo_root(site_dir)
    _require_remote(repo)
    base = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
    paths = [str(f.resolve()) for f in files]

    _git(repo, "switch", "-c", branch)
    try:
        _git(repo, "add", "--", *paths)
        _git(repo, "commit", "-m", title, "--", *paths)
        _git(repo, "push", "-u", "origin", branch)
    finally:
        # Uncommitted changes elsewhere (e.g. the agent's database) travel back with us.
        _git(repo, "switch", base)

    try:
        url = _run(["gh", "pr", "create", "--base", base, "--head", branch,
                    "--title", title, "--body", body], repo)
    except DeployError as e:
        if "not permitted to create" in str(e):
            raise DeployError(
                "GitHub Actions is not allowed to open pull requests. Enable "
                "Settings → Actions → General → 'Allow GitHub Actions to create and approve "
                f"pull requests'. The branch {branch} was pushed; you can open the PR by hand."
            ) from e
        raise
    return f"プルリクエストを作成しました: {url}（マージすると公開されます）"
