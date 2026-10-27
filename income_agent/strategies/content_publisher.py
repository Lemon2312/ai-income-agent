from __future__ import annotations

import hashlib
import json
import re
import shutil
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from income_agent.arxiv import Paper, fetch_full_text, fetch_papers, normalize_id, search_papers
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
- 「カテゴリ候補」があれば、記事に最も合うものを1つ選び、候補の表記のまま category に入れる
{RULES}
必ず次のJSONのみを返してください:
{{"intro": "導入で伝えること", "category": "カテゴリ名", "sections": [{{"heading": "見出し", "points": ["書く内容", ...]}}, ...]}}"""

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

PICK_PAPER_SYSTEM = """TASK: pick_paper
あなたは、最先端の研究を一般の人にやさしく紹介するブログの編集者です。
候補の論文から、専門知識のない読者向けの解説記事に向いているものを選び、向いている順に並べてください。
- 何が新しくできるようになったのかを、一般の人にも説明できる成果を優先する
- 実験での実証、新しいハードウェアや手法、実用に近づく成果など、話題性があるものを優先する
- 純粋に数学的な理論や、前提知識が多すぎて説明しきれないものは選ばない
- 向いていないものは入れなくてよい
必ず次のJSONのみを返してください: {"ids": ["論文ID", ...]}"""

PAPER_SECTIONS = ["ひとことで言うと", "なぜこの研究が必要？", "何をしたの？", "何がすごいの？",
                  "まだできないこと・課題", "用語ミニ辞典"]

PAPER_RULES = """- 論文の成果・数値・主張は、下の「論文の要旨」と「論文本文の抜粋」に書かれているものだけを使う。書かれていない結果や数値を作らない
- 理解を助けるための一般的な補足やたとえ話は書いてよいが、論文の内容と区別できるように書く（「たとえるなら」「一般に」など）
- 成果を誇張しない。論文自身が述べている限界や、まだできないことも書く
- 専門用語には、出てきたその場でやさしい説明を添える
- 論文の文章を長く引用・翻訳しない。自分の言葉で説明する"""

PAPER_OUTLINE_SYSTEM = f"""TASK: outline_paper
あなたは、研究論文を専門知識のない読者にやさしく解説するブログの編集者です。渡された論文の解説記事を企画してください。
- title: 読者が「読んでみたい」と思える日本語の記事タイトル（論文の成果が伝わるもの。誇張しない）
- intro: 記事の冒頭で読者の興味を引くために伝えること
- points: 次の見出しごとに、書くべき具体的な内容を2〜4個。内容は論文に書かれていることにもとづく
  見出し: {"、".join(PAPER_SECTIONS)}
- 「カテゴリ候補」があれば、記事に最も合うものを1つ選び、候補の表記のまま category に入れる
{PAPER_RULES}
必ず次のJSONのみを返してください:
{{"title": "記事タイトル", "intro": "導入で伝えること", "category": "カテゴリ名", "points": {{"見出し": ["書く内容", ...], ...}}}}"""


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
        + f"- 文字数: {payload.get('chars', '-')}字\n"
        + (f"- 元の論文: {payload['paper_url']}（{payload.get('paper_title', '')}）\n"
           "  公開前に、記事の内容が元の論文と合っているかを確かめてください。\n" if payload.get("paper_url") else "")
        + "\n"
        f"編集長の最後の指摘:\n{issues}\n"
    )


class ContentPublisher(Strategy):
    name = "content_publisher"
    description = "ジャンルに沿った記事を企画・執筆・品質チェックし、アフィリエイトリンク付きで公開する"
    _source = ""  # the paper being explained, appended to every prompt while writing about it

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
        if self.cfg.get("source", "topics") == "arxiv":
            jobs = [(None, paper) for paper in self._next_papers(count)]
        else:
            jobs = [(title, None) for title in self._next_topics(count)]
        for title, paper in jobs:
            try:
                action_id = self._produce(title, paper)
            except BudgetExceeded:
                raise
            except LLMError as e:
                self.log.warning("could not write %r: %s", title or paper.id, e)
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

    def _paper_done(self, paper_id: str) -> bool:
        if self.ctx.store.is_seen(self.name, f"arxiv:{paper_id}"):
            return True
        attempts = int(self.cfg.get("max_attempts_per_topic", 2))
        rejected = self.ctx.store.list_actions(REJECTED, limit=1000)
        return sum(1 for a in rejected if a.kind == "article_rejected"
                   and a.payload.get("arxiv_id") == paper_id) >= attempts

    def _next_papers(self, count: int) -> list[Paper]:
        """Papers listed in `papers` come first, in order. When none are left,
        the newest papers matching `arxiv_query` are offered to the LLM to pick from."""
        wanted = [normalize_id(str(i)) for i in self.cfg.get("papers", [])]
        todo = [i for i in dict.fromkeys(wanted) if not self._paper_done(i)]
        # Fetch a few more than needed so a mistyped id can't block the ones after it.
        papers = fetch_papers(self.ctx.fetch, todo[:count + 5])
        missing = set(todo[:count + 5]) - {p.id for p in papers}
        if missing:
            self.log.warning("not found on arXiv: %s", ", ".join(sorted(missing)))
        papers = papers[:count]
        query = self.cfg.get("arxiv_query", "")
        if len(papers) >= count or not query:
            return papers[:count]

        taken = {p.id for p in papers}
        candidates = [p for p in search_papers(self.ctx.fetch, query, int(self.cfg.get("arxiv_candidates", 40)))
                      if p.id not in taken and not self._paper_done(p.id)]
        if not candidates:
            return papers
        listing = "\n\n".join(f"ID: {p.id}\nタイトル: {p.title}\n要旨: {p.abstract[:600]}" for p in candidates)
        try:
            result = self.ctx.llm.complete_json(
                PICK_PAPER_SYSTEM,
                f"ジャンル: {self.cfg.get('niche', '')}\n想定読者: {self.cfg.get('audience', '')}\n"
                f"必要数: {count - len(papers)}\n\n# 候補\n{listing}",
                strategy=self.name,
            )
        except LLMError as e:
            self.log.warning("could not pick a paper: %s", e)
            return papers
        by_id = {p.id: p for p in candidates}
        picked = [by_id[i] for i in dict.fromkeys(normalize_id(str(x)) for x in result.get("ids", [])) if i in by_id]
        return (papers + picked)[:count]

    def _paper_source(self, paper: Paper) -> str:
        text = fetch_full_text(self.ctx.fetch, paper) if self.cfg.get("read_full_text", True) else ""
        authors = ", ".join(paper.authors[:5]) + (" ほか" if len(paper.authors) > 5 else "")
        return ("\n\n# 解説する論文\n"
                f"論文タイトル: {paper.title}\n著者: {authors}\n公開日: {paper.published}\n"
                f"URL: {paper.url}\n\n# 論文の要旨\n{paper.abstract}\n"
                + (f"\n# 論文本文の抜粋\n{text}\n" if text else "")
                + f"\n# 論文解説のルール（必ず守る）\n{PAPER_RULES}\n"
                "\n# 採点するときの注意\n論文の要旨・本文の抜粋にない成果や数値が記事に書かれていたら、accuracy は1〜2点にする")

    def _paper_outline(self, paper: Paper) -> tuple[str, list[dict[str, Any]], str | None]:
        categories = self._categories()
        brief = self._brief(paper.title)
        if categories:
            brief += "\n# カテゴリ候補\n" + "\n".join(f"- {c}" for c in categories)
        plan = self.ctx.llm.complete_json(PAPER_OUTLINE_SYSTEM, brief, strategy=self.name)
        title = str(plan.get("title", "")).strip() or paper.title
        category = str(plan.get("category", "")).strip()
        if categories and category not in categories:
            category = str(self.cfg.get("default_category", categories[0]))
        points = plan.get("points") if isinstance(plan.get("points"), dict) else {}
        min_chars = int(self.cfg.get("min_chars", 2500))
        per_section = max(400, int((min_chars - 300) / len(PAPER_SECTIONS) * 1.15))
        parts: list[dict[str, Any]] = [
            {"heading": None, "points": [str(plan.get("intro", "この研究のどこがおもしろいのか"))], "target": 250}
        ]
        for heading in PAPER_SECTIONS:
            items = points.get(heading) or [f"論文の内容にもとづいて「{heading}」を説明する"]
            parts.append({"heading": heading, "points": [str(x) for x in items], "target": per_section})
        return title, parts, category or None

    @staticmethod
    def _paper_footer(paper: Paper) -> str:
        authors = ", ".join(paper.authors[:3]) + (" ほか" if len(paper.authors) > 3 else "")
        return ("## 元の論文\n\n"
                f"- タイトル: {paper.title}\n"
                + (f"- 著者: {authors}\n" if authors else "")
                + (f"- 公開日: {paper.published}\n" if paper.published else "")
                + f"- リンク: [arXiv:{paper.id}]({paper.url})\n\n"
                "この記事は、論文の内容をかみくだいて解説したものです。正確な内容や詳しいデータは、元の論文をご確認ください。")

    def _guidelines(self) -> str:
        rules = [str(g) for g in self.cfg.get("guidelines", [])]
        return "\n# このブログの執筆ルール（必ず守る）\n" + "\n".join(f"- {r}" for r in rules) if rules else ""

    def _brief(self, title: str) -> str:
        brief = (f"タイトル: {title}\nジャンル: {self.cfg.get('niche', '')}\n"
                 f"想定読者: {self.cfg.get('audience', '')}\n言語: {self.cfg.get('language', 'ja')}"
                 + self._guidelines())
        return brief + self._source if self._source else brief

    def _categories(self) -> list[str]:
        """Category names from the config, or else from the site's data/categories.toml
        (the same file the theme uses for category colours and icons)."""
        if "categories" in self.cfg:
            return [str(c) for c in self.cfg["categories"]]
        data = self.site_dir / "data" / "categories.toml" if self.site_dir else None
        if not data or not data.exists():
            return []
        cats = tomllib.loads(data.read_text(encoding="utf-8"))
        return sorted(cats, key=lambda name: cats[name].get("weight", 0) if isinstance(cats[name], dict) else 0)

    def _outline(self, title: str) -> tuple[list[dict[str, Any]], str | None]:
        """Plans the article as intro + 4-6 sections + summary. Writing each part in
        its own call keeps small models from stopping long before the target length."""
        categories = self._categories()
        brief = self._brief(title)
        if categories:
            brief += "\n# カテゴリ候補\n" + "\n".join(f"- {c}" for c in categories)
        plan = self.ctx.llm.complete_json(OUTLINE_SYSTEM, brief, strategy=self.name)
        category = str(plan.get("category", "")).strip()
        if categories and category not in categories:
            category = str(self.cfg.get("default_category", categories[0]))
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
        return parts, category or None

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

    def _produce(self, title: str | None, paper: Paper | None = None) -> int | None:
        if paper is None:
            return self._produce_article(str(title))
        self._source = self._paper_source(paper)
        try:
            return self._produce_article(paper.title, paper)
        finally:
            self._source = ""

    def _produce_article(self, title: str, paper: Paper | None = None) -> int | None:
        min_quality = float(self.cfg.get("min_quality", 0.8))
        max_revisions = int(self.cfg.get("max_revisions", 2))

        if paper:
            title, parts, category = self._paper_outline(paper)
        else:
            parts, category = self._outline(title)
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

        if paper:
            body = body.rstrip() + "\n\n" + self._paper_footer(paper) + "\n"
        slug = f"{datetime.now(timezone.utc):%Y-%m-%d}-arxiv-{re.sub(r'[^0-9a-z]+', '-', paper.id.lower())}" if paper else None
        path = self._save_draft(title, body, review["description"], category, slug=slug)
        payload: dict[str, Any] = {"draft_path": str(path), "quality": review["score"],
                                   "quality_history": history, "scores": review["scores"],
                                   "issues": review["issues"], "chars": _body_chars(body)}
        if paper:
            payload.update(arxiv_id=paper.id, paper_url=paper.url, paper_title=paper.title)

        if review["score"] < min_quality:
            self.log.info("rejected %r (quality %.2f < %.2f)", title, review["score"], min_quality)
            self.ctx.store.add_action(self.name, "article_rejected", title, payload, status=REJECTED)
            return None

        self.log.info("quality %.2f: %s", review["score"], title)
        if paper:
            self.ctx.store.mark_seen(self.name, f"arxiv:{paper.id}")
        if self.cfg.get("require_approval", True):
            return self.ctx.store.add_action(self.name, "article", title, payload)

        action_id = self.ctx.store.add_action(self.name, "article", title, payload, status=DONE)
        self.log.info(self._publish(path, title, payload))
        return action_id

    def _save_draft(self, title: str, body: str, description: str, category: str | None = None,
                    slug: str | None = None) -> Path:
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
            + (f"categories: {json.dumps([category], ensure_ascii=False)}\n" if category else "")
            +             f"tags: {json.dumps(self.cfg.get('tags', []), ensure_ascii=False)}\n"
            "---\n\n"
        )
        self.drafts_dir.mkdir(parents=True, exist_ok=True)
        path = self.drafts_dir / f"{slug or _slug(title)}.md"
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
