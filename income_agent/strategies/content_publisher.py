from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from income_agent.deploy import DeployError, git_publish, github_pull_request
from income_agent.llm import BudgetExceeded, LLMError
from income_agent.storage import DONE, PENDING, REJECTED, Action, day_start_iso
from income_agent.strategies.base import Strategy

IDEATE_SYSTEM = """TASK: ideate_topics
あなたはSEOに強いブログ編集者です。
指定ジャンルで、検索需要があり、読者の悩みを具体的に解決でき、商品やサービスの紹介につなげやすい記事タイトルを考えてください。
- 1記事1テーマ。「〇〇のやり方」「〇〇 比較」「〇〇 テンプレート」など検索されやすい具体的な切り口にする
- 既存タイトルと重複・類似するものは避ける
必ず次のJSONのみを返してください: {"topics": ["タイトル1", "タイトル2", ...]}"""

WRITE_SYSTEM = """TASK: write_article
あなたは読者目線で正確な記事を書くライターです。Markdownで記事本文を書いてください。
- 先頭は「# タイトル」、その後に導入、## 見出しで3〜6セクション、最後に「## まとめ」
- 具体例・手順・コピーして使えるプロンプトやテンプレート・チェックリストを入れて実用的に
- 不確かな数値や事実、料金、機能をでっち上げない。断定できない情報は「公式サイトで最新情報を確認」と添える
- 医療・法律・投資の断定的助言はしない
- 誇大広告や「必ず儲かる」等の表現は禁止"""

REVIEW_SYSTEM = """TASK: review_article
あなたは検索上位を狙うメディアの厳しい編集長です。記事を0.0〜1.0で採点してください。
採点基準:
- 読者の悩みを具体的に解決しているか（手順・例・テンプレートの有無）
- 正確性（でっち上げの数値・機能・料金、古い情報の疑いは大きく減点）
- 薄さ・ありきたりさ（どこにでもある一般論だけなら0.5未満）
- 読みやすさ・構成
- 誇大表現、規約違反、断定的な医療・法律・投資助言（あれば0.0）
0.8以上は「そのまま公開してよい」水準です。
必ず次のJSONのみを返してください:
{"score": <0.0-1.0>, "issues": ["改善すべき点", ...], "description": "検索結果に表示する説明文（全角120字以内）"}"""

REVISE_SYSTEM = """TASK: revise_article
あなたは編集者の指摘を反映して記事を書き直すライターです。
指摘をすべて解消した記事の全文をMarkdownで出力してください（先頭は「# タイトル」）。
事実が不確かな箇所は削るか「公式サイトで最新情報を確認」と添えてください。"""


def _slug(title: str) -> str:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    digest = hashlib.sha1(title.encode()).hexdigest()[:8]
    ascii_part = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    # Japanese titles leave only fragments like "ai-5"; those make worse URLs than none.
    return f"{date}-{ascii_part}-{digest}" if len(ascii_part) >= 12 else f"{date}-{digest}"


def insert_affiliate_links(markdown: str, links: list[dict[str, str]]) -> tuple[str, int]:
    """Links the first body-text occurrence of each keyword. Headings are left alone.
    The "sponsored" link title is turned into rel="sponsored nofollow" by the site's
    render hook, as Google requires for paid links."""
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
            lines[i] = line.replace(keyword, f'[{keyword}]({url} "sponsored")', 1)
            inserted += 1
            break
    return "\n".join(lines), inserted


def _body_chars(markdown: str) -> int:
    return len(re.sub(r"\s", "", markdown))


def _pr_body(title: str, payload: dict[str, Any]) -> str:
    history = " → ".join(f"{s:.2f}" for s in payload.get("quality_history", []))
    issues = "\n".join(f"- {i}" for i in payload.get("issues", [])) or "- なし"
    return (
        f"AIエージェントが書いた記事「{title}」です。\n\n"
        "**マージすると公開されます。** 公開したくない場合はこのプルリクエストを閉じてください。\n"
        "内容を直したい場合は、「Files changed」タブから記事ファイルを編集してからマージできます。\n\n"
        f"- 品質スコア（AI編集長）: {history}\n\n"
        f"編集長の最後の指摘:\n{issues}\n"
    )


class ContentPublisher(Strategy):
    name = "content_publisher"
    description = "ジャンルに沿った記事を企画・執筆・品質チェックし、アフィリエイトリンク付きで公開する"

    @property
    def drafts_dir(self) -> Path:
        return self.ctx.data_dir / "drafts"

    @property
    def site_dir(self) -> Path | None:
        site = self.cfg.get("site_dir")
        return Path(site) if site else None

    @property
    def publish_dir(self) -> Path:
        if "publish_dir" in self.cfg:
            return Path(self.cfg["publish_dir"])
        if self.site_dir:
            return self.site_dir / "content" / "posts"
        return self.ctx.data_dir / "site" / "posts"

    def remaining_today(self) -> int:
        """Articles that may still be generated today: published + waiting for review
        both count, so an unattended approval queue doesn't pile up either."""
        limit = int(self.cfg.get("max_articles_per_day", 2))
        since = day_start_iso(self.ctx.timezone)
        published = self.ctx.store.count_actions(self.name, "article", DONE, since=since)
        pending = self.ctx.store.count_actions(self.name, "article", PENDING)
        return max(0, limit - published - pending)

    def run(self) -> list[int]:
        count = min(int(self.cfg.get("articles_per_run", 1)), self.remaining_today())
        if count <= 0:
            self.log.info("daily article limit reached; skipping")
            return []
        created: list[int] = []
        for title in self._next_topics(count):
            try:
                action_id = self._produce(title)
            except BudgetExceeded:
                raise
            except LLMError as e:
                self.log.warning("could not write %r: %s", title, e)
                continue
            if action_id is not None:
                created.append(action_id)
        return created

    def _next_topics(self, count: int) -> list[str]:
        existing = set(self.ctx.store.titles(self.name, "article"))
        existing |= set(self.ctx.store.titles(self.name, "article_rejected", include_rejected=True))
        planned = [t for t in self.cfg.get("topics", []) if t not in existing]
        if len(planned) >= count:
            return planned[:count]

        result = self.ctx.llm.complete_json(
            IDEATE_SYSTEM,
            f"ジャンル: {self.cfg.get('niche', '')}\n想定読者: {self.cfg.get('audience', '')}\n"
            f"必要数: {count * 3}\n"
            f"既存タイトル:\n{json.dumps(sorted(existing), ensure_ascii=False)}",
            strategy=self.name,
        )
        ideas = [str(t).strip() for t in result.get("topics", []) if str(t).strip()]
        fresh = [t for t in ideas if t not in existing and t not in planned]
        return (planned + fresh)[:count]

    def _brief(self, title: str) -> str:
        return (f"タイトル: {title}\nジャンル: {self.cfg.get('niche', '')}\n"
                f"想定読者: {self.cfg.get('audience', '')}\n言語: {self.cfg.get('language', 'ja')}\n"
                f"目安文字数: {int(self.cfg.get('min_chars', 2500))}字以上")

    def _review(self, title: str, body: str) -> dict[str, Any]:
        min_chars = int(self.cfg.get("min_chars", 2500))
        chars = _body_chars(body)
        review = self.ctx.llm.complete_json(
            REVIEW_SYSTEM, f"{self._brief(title)}\n実際の文字数: {chars}\n\n# 記事\n{body}",
            strategy=self.name,
        )
        score = float(review.get("score", 0))
        issues = [str(i) for i in review.get("issues", [])]
        if chars < min_chars * 0.7:
            score = min(score, float(self.cfg.get("min_quality", 0.8)) - 0.01)
            issues.append(f"文字数が不足しています（{chars}字 / 目安{min_chars}字）。具体例を増やしてください。")
        return {"score": score, "issues": issues, "description": str(review.get("description", ""))}

    def _produce(self, title: str) -> int | None:
        min_quality = float(self.cfg.get("min_quality", 0.8))
        max_revisions = int(self.cfg.get("max_revisions", 2))

        body = self.ctx.llm.complete(WRITE_SYSTEM, self._brief(title), strategy=self.name,
                                     max_tokens=4000).text.strip()
        review = self._review(title, body)
        history = [review["score"]]
        for _ in range(max_revisions):
            if review["score"] >= min_quality:
                break
            issues = "\n".join(f"- {i}" for i in review["issues"]) or "- 全体的に具体性を高める"
            body = self.ctx.llm.complete(
                REVISE_SYSTEM, f"{self._brief(title)}\n\n# 編集者の指摘\n{issues}\n\n# 記事\n{body}",
                strategy=self.name, max_tokens=4000,
            ).text.strip()
            review = self._review(title, body)
            history.append(review["score"])

        path = self._save_draft(title, body, review["description"])
        payload: dict[str, Any] = {"draft_path": str(path), "quality": review["score"],
                                   "quality_history": history, "issues": review["issues"]}

        if review["score"] < min_quality:
            self.log.info("rejected %r (quality %.2f < %.2f)", title, review["score"], min_quality)
            self.ctx.store.add_action(self.name, "article_rejected", title, payload, status=REJECTED)
            return None

        self.log.info("quality %.2f: %s", review["score"], title)
        if self.cfg.get("require_approval", True):
            return self.ctx.store.add_action(self.name, "article", title, payload)

        action_id = self.ctx.store.add_action(self.name, "article", title, payload, status=DONE)
        self.log.info(self._publish(path, title, payload))
        return action_id

    def _save_draft(self, title: str, body: str, description: str) -> Path:
        body = re.sub(r"\A# .*\n+", "", body.strip())
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
            f"description: {json.dumps(description, ensure_ascii=False)}\n"
            f"tags: {json.dumps(self.cfg.get('tags', []), ensure_ascii=False)}\n"
            "---\n\n"
        )
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        path = self.drafts_dir / f"{_slug(title)}.md"
        path.write_text(front_matter + body + "\n", encoding="utf-8")
        return path

    def _publish(self, draft: Path, title: str, payload: dict[str, Any] | None = None) -> str:
        self.publish_dir.mkdir(parents=True, exist_ok=True)
        dest = self.publish_dir / draft.name
        shutil.copy2(draft, dest)
        message = f"公開ディレクトリにコピーしました: {dest}"
        mode = self.cfg.get("deploy", "none")
        repo = self.site_dir or self.publish_dir
        try:
            if mode == "git":
                result = git_publish(repo, [dest], f"Add post: {title}",
                                     push=self.cfg.get("git_push", True))
            elif mode == "github-pr":
                branch = f"post/{draft.stem}-{datetime.now(timezone.utc):%H%M%S}"
                result = github_pull_request(repo, [dest], branch,
                                             f"記事: {title}", _pr_body(title, payload or {}))
            elif mode == "none":
                return message
            else:
                raise DeployError(f"unknown deploy mode: {mode!r}")
        except DeployError as e:
            if self.cfg.get("fail_on_deploy_error", False):
                raise
            # With "git" the post stays committed locally and goes out with the next push.
            self.log.error("deploy failed: %s", e)
            return message + f"\nデプロイに失敗しました: {e}"
        return message + "\n" + result

    def on_approve(self, action: Action) -> str:
        draft = Path(action.payload["draft_path"])
        if not draft.exists():
            raise FileNotFoundError(f"draft not found: {draft}")
        return self._publish(draft, action.title, action.payload)
