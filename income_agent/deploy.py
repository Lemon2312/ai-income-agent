from __future__ import annotations

import subprocess
from pathlib import Path


class DeployError(RuntimeError):
    pass


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise DeployError(f"git {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def git_publish(repo: Path, files: list[Path], message: str, push: bool = True) -> str:
    """Commits `files` in the site repository and pushes, which triggers the
    hosting provider's build (GitHub Pages Actions, Cloudflare Pages, Netlify, ...)."""
    if not (repo / ".git").exists():
        raise DeployError(f"{repo} is not a git repository (run `income-agent init-site` first)")
    _git(repo, "add", "--", *[str(f.resolve().relative_to(repo.resolve())) for f in files])
    if not _git(repo, "status", "--porcelain", "--", *[str(f.resolve()) for f in files]):
        return "変更がないためコミットをスキップしました。"
    _git(repo, "commit", "-m", message)
    if not push:
        return "コミットしました（push は無効）。"
    if not _git(repo, "remote"):
        raise DeployError(f"{repo} has no git remote; add one with `git remote add origin <url>`")
    _git(repo, "push", "origin", "HEAD")
    return "コミットして push しました。数分後にサイトへ反映されます。"
