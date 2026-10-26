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

RULES = """- 具体例・手順・コピーして使えるプロンプトやテンプレート・チェックリストを入れて実用的に
- 不確かな数値や事実、料金、機能をでっち上げない。変わりやすい料金・仕様は「最新情報は公式サイトで確認」と添える
- 医療・法律・投資の断定的助言はしない
- 誇大広告や「必ず儲かる」等の表現は禁止"""

OUTLINE_SYSTEM = f"""TASK: outline_article
あなたはSEOに強いブログ編集者です。記事の構成案を作ってください。
- 読者が検索した悩みに順番に答える流れにする
- 本文の見出し（h2）は4〜6個。「まとめ」は含めない
- 各見出しに、書くべき具体的な内容（手順、例、テンプレート、注意点など）を2〜4個挙げる
{RULES}
必ず次のJSONのみを返してください:
{{"intro": "導入で伝えること", "sections": [{{"heading": "見出し", "points": ["書く内容", ...]}}, ...]}}"""

SECTION_SYSTEM = f"""TASK: write_section
あなたは読者目線で正確な記事を書くライターです。記事のうち、指定された1つのパートだけをMarkdownで書いてください。
- 見出し（## ）は書かず、本文だけを出力する。必要なら ### の小見出しを使ってよい
- 指定された文字数以上を必ず書く。一般論で水増しせず、具体例・手順・テンプレートで厚みを出す
- プロンプトやテンプレートはコードブロックで示す
{RULES}"""

REVIEW_SYSTEM = """TASK: review_article
あなたは検索上位を狙うメディアの編集長です。記事を次の5項目でそれぞれ1〜5点で採点してください（4点=そのまま公開できる水準）。
- usefulness: 読者の悩みを具体的に解決しているか（手順・例・テンプレート）
- accuracy: 事実の正確さ。でっち上げの数値・機能・料金があれば1〜2点
- depth: 一般論だけの薄い内容になっていないか
- readability: 構成・読みやすさ
- compliance: 誇大表現・断定的な医療/法律/投資助言・規約違反・「執筆ルール」への違反がないか（あれば1点）
注意:
- 料金や細かい仕様など変わりやすい情報は、記載がなくても減点しない（「公式サイトで確認」の案内があれば十分）
- issues には、どの見出しをどう直すべきかを具体的に書く（4点以上の項目については書かなくてよい）
必ず次のJSONのみを返してください:
{"scores": {"usefulness": 1-5, "accuracy": 1-5, "depth": 1-5, "readability": 1-5, "compliance": 1-5},
 "issues": ["「見出し名」: 改善点", ...], "description": "検索結果に表示する説明文（全角120字以内）"}"""

REVISE_SECTION_SYSTEM = f"""TASK: revise_section
あなたは編集長の指摘を反映して記事を改善するライターです。指定された1つのパートを書き直してください。
- このパートに関係する指摘をすべて解消する。関係ない指摘は無視してよい
- 見出し（## ）は書かず、本文だけを出力する
- 元の内容より短くしない。指定された文字数以上を書く
{RULES}"""

SCORE_KEYS = ("usefulness", "accuracy", "depth", "readability", "compliance")


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
    labels = {"usefulness": "実用性", "accuracy": "正確性", "depth": "深さ",
              "readability": "読みやすさ", "compliance": "表現の適切さ"}
    scores = " / ".join(f"{labels.get(k, k)} {v}" for k, v in payload.get("scores", {}).items())
    return (
        f"AIエージェントが書いた記事「{title}」です。\n\n"
        "**マージすると公開されます。** 公開したくない場合はこのプルリクエストを閉じてください。\n"
        "内容を直したい場合は、「Files changed」タブから記事ファイルを編集してからマージできます。\n\n"
        f"- 品質スコア（AI編集長）: {history}\n"
        + (f"- 項目別（5点満点）: {scores}\n" if scores else "")
        + f"- 文字数: {payload.get('chars', '-')}字\n\n"
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
        attempts = int(self.cfg.get("max_attempts_per_topic", 2))
        rejected = self.ctx.store.titles(self.name, "article_rejected", include_rejected=True)
        existing |= {t for t in set(rejected) if rejected.count(t) >= attempts}
        planned = [t for t in self.cfg.get("topics", []) if t not in existing]
        if len(planned) >= count:
            return planned[:count]

        result = self.ctx.llm.complete_json(
            IDEATE_SYSTEM,
            f"ジャンル: {self.cfg.get('niche', '')}\n想定読者: {self.cfg.get('audience', '')}\n"
            f"必要数: {count * 3}\n"
            f"既存タイトル:\n{json.dumps(sorted(existing), ensure_ascii=False)}"
            + self._guidelines(),
            strategy=self.name,
        )
        ideas = [str(t).strip() for t in result.get("topics", []) if str(t).strip()]
        fresh = [t for t in ideas if t not in existing and t not in planned]
        return (planned + fresh)[:count]

    def _guidelines(self) -> str:
        rules = [str(g) for g in self.cfg.get("guidelines", [])]
        text = "\n# このブログの執筆ルール（必ず守る）\n" + "\n".join(f"- {r}" for r in rules) if rules else ""
        tips = [str(t) for t in self.cfg.get("author_tips", [])]
        if tips:
            author = self.cfg.get("author", "運営者")
            text += (f"\n# 運営者（{author}）が実際に使っているコツ\n"
                     "記事のテーマに本当に関係する場合だけ、1記事に1〜2個まで、"
                     f"「{author}が実際にやっている方法」として自然に紹介してよい。無関係なら使わない。\n"
                     + "\n".join(f"- {t}" for t in tips))
        return text

    def _brief(self, title: str) -> str:
        return (f"タイトル: {title}\nジャンル: {self.cfg.get('niche', '')}\n"
                f"想定読者: {self.cfg.get('audience', '')}\n言語: {self.cfg.get('language', 'ja')}"
                + self._guidelines())

    def _outline(self, title: str) -> list[dict[str, Any]]:
        """Plans the article as intro + 4-6 sections + summary. Writing each part in
        its own call keeps small models from stopping long before the target length."""
        plan = self.ctx.llm.complete_json(OUTLINE_SYSTEM, self._brief(title), strategy=self.name)
        sections = [s for s in plan.get("sections", []) if isinstance(s, dict) and s.get("heading")]
        if not sections:
            raise LLMError("outline has no sections")
        sections = sections[:6]
        min_chars = int(self.cfg.get("min_chars", 2500))
        per_section = max(500, int((min_chars - 600) / len(sections) * 1.15))
        parts: list[dict[str, Any]] = [
            {"heading": None, "points": [str(plan.get("intro", "読者の悩みへの共感と、この記事で分かること"))],
             "target": 300}
        ]
        parts += [{"heading": str(s["heading"]).strip(), "points": [str(x) for x in s.get("points", [])],
                   "target": per_section} for s in sections]
        parts.append({"heading": "まとめ", "points": ["要点の振り返り", "読者が今日からできる次の一歩"],
                      "target": 300})
        return parts

    def _part_prompt(self, title: str, parts: list[dict[str, Any]], part: dict[str, Any]) -> str:
        toc = "\n".join(f"- {p['heading']}" for p in parts if p["heading"])
        name = part["heading"] or "導入（最初の見出しの前に置く文章）"
        points = "\n".join(f"- {x}" for x in part["points"])
        return (f"{self._brief(title)}\n\n# 記事の見出し構成\n{toc}\n\n"
                f"# 今回書くパート\n{name}\n\n# このパートで書く内容\n{points}\n\n"
                f"# 文字数\n{part['target']}字以上")

    def _write_parts(self, title: str, parts: list[dict[str, Any]]) -> None:
        for part in parts:
            part["body"] = self.ctx.llm.complete(
                SECTION_SYSTEM, self._part_prompt(title, parts, part), strategy=self.name,
                max_tokens=min(4000, part["target"] * 2 + 500),
            ).text.strip()

    def _revise_parts(self, title: str, parts: list[dict[str, Any]], issues: list[str]) -> None:
        notes = "\n".join(f"- {i}" for i in issues) or "- 全体的に具体例を増やし、内容を深める"
        for part in parts:
            if part["heading"] is None:
                continue
            current = _body_chars(part["body"])
            target = max(part["target"], current)
            revised = self.ctx.llm.complete(
                REVISE_SECTION_SYSTEM,
                f"{self._part_prompt(title, parts, {**part, 'target': target})}\n\n"
                f"# 編集長の指摘（記事全体）\n{notes}\n\n# 現在の本文\n{part['body']}",
                strategy=self.name, max_tokens=min(4000, target * 2 + 500),
            ).text.strip()
            if _body_chars(revised) >= current * 0.8:
                part["body"] = revised

    @staticmethod
    def _assemble(title: str, parts: list[dict[str, Any]]) -> str:
        out = [f"# {title}"]
        for part in parts:
            body = re.sub(r"\A##\s+.*\n+", "", part["body"].strip())
            out.append(body if part["heading"] is None else f"## {part['heading']}\n\n{body}")
        return "\n\n".join(out) + "\n"

    def _review(self, title: str, body: str) -> dict[str, Any]:
        min_chars = int(self.cfg.get("min_chars", 2500))
        chars = _body_chars(body)
        review = self.ctx.llm.complete_json(
            REVIEW_SYSTEM, f"{self._brief(title)}\n文字数: {chars}字\n\n# 記事\n{body}",
            strategy=self.name,
        )
        scores = review.get("scores")
        if isinstance(scores, dict) and scores:
            values = {k: min(5.0, max(1.0, float(scores.get(k, 1)))) for k in SCORE_KEYS}
            score = 0.0 if values["compliance"] <= 2 else sum(values.values()) / (5 * len(values))
        else:
            score = float(review.get("score", 0))
        issues = [str(i) for i in review.get("issues", [])]
        if chars < min_chars * 0.7:
            score = min(score, float(self.cfg.get("min_quality", 0.8)) - 0.01)
            issues.append(f"文字数が不足しています（{chars}字 / 目安{min_chars}字）。具体例を増やしてください。")
        return {"score": round(score, 3), "issues": issues,
                "scores": scores if isinstance(scores, dict) else {},
                "description": str(review.get("description", ""))}

    def _produce(self, title: str) -> int | None:
        min_quality = float(self.cfg.get("min_quality", 0.8))
        max_revisions = int(self.cfg.get("max_revisions", 2))

        parts = self._outline(title)
        self._write_parts(title, parts)
        body = self._assemble(title, parts)
        review = self._review(title, body)
        history = [review["score"]]
        for _ in range(max_revisions):
            if review["score"] >= min_quality:
                break
            self._revise_parts(title, parts, review["issues"])
            body = self._assemble(title, parts)
            review = self._review(title, body)
            history.append(review["score"])

        path = self._save_draft(title, body, review["description"])
        payload: dict[str, Any] = {"draft_path": str(path), "quality": review["score"],
                                   "quality_history": history, "scores": review["scores"],
                                   "issues": review["issues"], "chars": _body_chars(body)}

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
