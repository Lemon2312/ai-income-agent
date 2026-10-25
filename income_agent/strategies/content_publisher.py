from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from income_agent.llm import BudgetExceeded, LLMError
from income_agent.storage import DONE, Action
from income_agent.strategies.base import Strategy

IDEATE_SYSTEM = """TASK: ideate_topics
あなたはSEOに強いブログ編集者です。
指定ジャンルで、検索需要があり、読者の悩みを具体的に解決でき、商品やサービスの紹介につなげやすい記事タイトルを考えてください。
既存タイトルと重複・類似するものは避けてください。
必ず次のJSONのみを返してください: {"topics": ["タイトル1", "タイトル2", ...]}"""

WRITE_SYSTEM = """TASK: write_article
あなたは読者目線で正確な記事を書くライターです。Markdownで記事本文を書いてください。
- 先頭は「# タイトル」、その後に導入、## 見出しで3〜6セクション、最後に「## まとめ」
- 具体例・手順・チェックリストを入れて実用的に
- 不確かな数値や事実をでっち上げない。医療・法律・投資の断定的助言はしない
- 誇大広告や「必ず儲かる」等の表現は禁止"""


def _slug(title: str) -> str:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest = hashlib.sha1(title.encode()).hexdigest()[:8]
    ascii_part = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    return f"{date}-{ascii_part + '-' if ascii_part else ''}{digest}"


def insert_affiliate_links(markdown: str, links: list[dict[str, str]]) -> tuple[str, int]:
    """Links the first body-text occurrence of each keyword. Headings are left alone."""
    lines = markdown.split("\n")
    inserted = 0
    for link in links:
        keyword, url = link.get("keyword", ""), link.get("url", "")
        if not keyword or not url:
            continue
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#") or keyword not in line:
                continue
            if f"[{keyword}]" in line:
                break
            lines[i] = line.replace(keyword, f"[{keyword}]({url})", 1)
            inserted += 1
            break
    return "\n".join(lines), inserted


class ContentPublisher(Strategy):
    name = "content_publisher"
    description = "ジャンルに沿った記事を企画・執筆し、アフィリエイトリンク付きで公開準備する"

    @property
    def drafts_dir(self) -> Path:
        return self.ctx.data_dir / "drafts"

    @property
    def publish_dir(self) -> Path:
        return Path(self.cfg.get("publish_dir", self.ctx.data_dir / "site" / "posts"))

    def run(self) -> list[int]:
        count = int(self.cfg.get("articles_per_run", 1))
        if count <= 0:
            return []
        topics = self._next_topics(count)
        created: list[int] = []
        for title in topics:
            try:
                created.append(self._write(title))
            except BudgetExceeded:
                raise
            except LLMError as e:
                self.log.warning("could not write %r: %s", title, e)
        return created

    def _next_topics(self, count: int) -> list[str]:
        existing = set(self.ctx.store.titles(self.name, "article"))
        planned = [t for t in self.cfg.get("topics", []) if t not in existing]
        if len(planned) >= count:
            return planned[:count]

        niche = self.cfg.get("niche", "")
        audience = self.cfg.get("audience", "")
        result = self.ctx.llm.complete_json(
            IDEATE_SYSTEM,
            f"ジャンル: {niche}\n想定読者: {audience}\n必要数: {count * 3}\n"
            f"既存タイトル:\n{json.dumps(sorted(existing), ensure_ascii=False)}",
            strategy=self.name,
        )
        ideas = [str(t).strip() for t in result.get("topics", []) if str(t).strip()]
        fresh = [t for t in ideas if t not in existing and t not in planned]
        return (planned + fresh)[:count]

    def _write(self, title: str) -> int:
        niche = self.cfg.get("niche", "")
        audience = self.cfg.get("audience", "")
        language = self.cfg.get("language", "ja")
        min_chars = int(self.cfg.get("min_chars", 2500))
        body = self.ctx.llm.complete(
            WRITE_SYSTEM,
            f"タイトル: {title}\nジャンル: {niche}\n想定読者: {audience}\n"
            f"言語: {language}\n目安文字数: {min_chars}字以上",
            strategy=self.name, max_tokens=4000,
        ).text.strip()

        body, link_count = insert_affiliate_links(body, self.cfg.get("affiliate_links", []))
        if link_count:
            # Japanese law (景品表示法 / ステマ規制, 2023-10) requires ads to be disclosed.
            disclosure = self.cfg.get("affiliate_disclosure", "※本記事はアフィリエイト広告（PR）を含みます。")
            body = f"{disclosure}\n\n{body}"

        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        front_matter = (
            "---\n"
            f"title: {json.dumps(title, ensure_ascii=False)}\n"
            f"date: {now}\n"
            f"tags: {json.dumps(self.cfg.get('tags', []), ensure_ascii=False)}\n"
            "---\n\n"
        )
        content = front_matter + re.sub(r"\A# .*\n+", "", body)

        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        path = self.drafts_dir / f"{_slug(title)}.md"
        path.write_text(content, encoding="utf-8")

        payload = {"draft_path": str(path), "affiliate_links": link_count,
                   "chars": len(content)}
        if self.cfg.get("require_approval", True):
            return self.ctx.store.add_action(self.name, "article", title, payload)

        action_id = self.ctx.store.add_action(self.name, "article", title, payload, status=DONE)
        self.log.info(self._publish(path))
        return action_id

    def _publish(self, draft: Path) -> str:
        self.publish_dir.mkdir(parents=True, exist_ok=True)
        dest = self.publish_dir / draft.name
        shutil.copy2(draft, dest)
        return f"公開ディレクトリにコピーしました: {dest}"

    def on_approve(self, action: Action) -> str:
        draft = Path(action.payload["draft_path"])
        if not draft.exists():
            raise FileNotFoundError(f"draft not found: {draft}")
        return self._publish(draft) + "\n静的サイト（Hugo / Jekyll / Astro 等）をビルド・デプロイすると公開されます。"
