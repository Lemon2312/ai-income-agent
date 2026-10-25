from __future__ import annotations

import subprocess
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

# Hidden files are stored without the leading dot so packaging tools don't drop them.
RENAMES = {"github": ".github", "gitignore": ".gitignore"}
TEXT_SUFFIXES = {".toml", ".md", ".html", ".yml", ".css", ""}


def _copy(src: Traversable, dest: Path, values: dict[str, str], skip: set[str]) -> None:
    for entry in src.iterdir():
        if entry.name in skip:
            continue
        target = dest / RENAMES.get(entry.name, entry.name)
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy(entry, target, values, skip)
            continue
        if Path(entry.name).suffix in TEXT_SUFFIXES:
            text = entry.read_text(encoding="utf-8")
            for key, value in values.items():
                text = text.replace("{{" + key + "}}", value)
            target.write_text(text, encoding="utf-8")
        else:
            target.write_bytes(entry.read_bytes())


def init_site(dest: Path, title: str, description: str, author: str, git: bool = True,
              workflow: bool = True) -> list[str]:
    """Creates a Hugo blog wired for GitHub Pages and returns follow-up instructions.
    `workflow=False` omits the Pages workflow, for sites living inside a repository
    that deploys them from its own root-level workflow."""
    if dest.exists() and any(dest.iterdir()):
        raise FileExistsError(f"{dest} already exists and is not empty")
    dest.mkdir(parents=True, exist_ok=True)
    template = resources.files("income_agent").joinpath("site_template")
    _copy(template, dest, {"TITLE": title, "DESCRIPTION": description, "AUTHOR": author},
          skip=set() if workflow else {"github"})

    if git:
        subprocess.run(["git", "init", "-q", "-b", "main", str(dest)], check=True)
        subprocess.run(["git", "-C", str(dest), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(dest), "commit", "-q", "-m", "Initial blog"], check=True)

    return [
        "GitHub で空のリポジトリを作成（例: my-blog）",
        f"git -C {dest} remote add origin https://github.com/<ユーザー名>/<リポジトリ名>.git",
        f"git -C {dest} push -u origin main",
        "GitHub のリポジトリ設定 → Pages → Source を「GitHub Actions」に変更",
        "config.toml の content_publisher に deploy = \"git\" が設定されていることを確認",
    ]
