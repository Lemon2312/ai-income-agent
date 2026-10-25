from __future__ import annotations

import re

from income_agent.llm import BudgetExceeded, LLMError
from income_agent.storage import Action
from income_agent.strategies.base import Strategy
from income_agent.feeds import FeedItem, parse_feed

SCORE_SYSTEM = """TASK: score_job
あなたはフリーランスの案件選定アシスタントです。
案件がプロフィールの人物にとってどれだけ「受注できて、かつ割に合う」かを0.0〜1.0で評価してください。
スキル適合度、報酬の妥当性、詐欺・違法・規約違反の疑い（あれば0.0）を考慮します。
必ず次のJSONのみを返してください: {"score": <0.0-1.0>, "reason": "<日本語で1〜2文>"}"""

PROPOSAL_SYSTEM = """TASK: draft_proposal
あなたは受注率の高い提案文を書くフリーランサーです。
案件内容とプロフィールに基づき、具体的で誠実な提案文を書いてください。
- 冒頭で依頼者の課題を言い換えて理解を示す
- 関連する実績・スキルを具体的に挙げる（プロフィールにないことは書かない）
- 進め方・期間の目安・確認したい質問を1〜2個
- 300〜600字程度、案件の言語に合わせる（英語の案件なら英語）
提案文の本文のみを出力してください。"""


def _contains(text: str, keyword: str) -> bool:
    # Whole-word match for ASCII so "ai" doesn't hit "maintain"; CJK has no word boundaries.
    if keyword.isascii():
        return re.search(rf"(?<![a-z0-9]){re.escape(keyword)}(?![a-z0-9])", text) is not None
    return keyword in text


class FreelanceScout(Strategy):
    name = "freelance_scout"
    description = "求人/案件フィードを巡回し、合う案件を採点して提案文を下書きする"

    def run(self) -> list[int]:
        feeds: list[str] = self.cfg.get("feeds", [])
        keywords = [k.lower() for k in self.cfg.get("keywords", [])]
        exclude = [k.lower() for k in self.cfg.get("exclude_keywords", [])]
        min_score = float(self.cfg.get("min_score", 0.7))
        max_items = int(self.cfg.get("max_items_per_run", 20))
        profile = self.cfg.get("profile", "").strip()

        created: list[int] = []
        evaluated = 0
        for url in feeds:
            try:
                items = parse_feed(self.ctx.fetch(url))
            except Exception as e:  # one broken feed must not stop the others
                self.log.warning("feed %s failed: %s", url, e)
                continue

            for item in items:
                if evaluated >= max_items:
                    return created
                if self.ctx.store.is_seen(self.name, item.id):
                    continue
                if not self._prefilter(item, keywords, exclude):
                    self.ctx.store.mark_seen(self.name, item.id)
                    continue

                evaluated += 1
                try:
                    action_id = self._evaluate(item, profile, min_score)
                except BudgetExceeded:
                    raise
                except (LLMError, ValueError, KeyError) as e:
                    self.log.warning("could not evaluate %r: %s", item.title, e)
                    continue
                self.ctx.store.mark_seen(self.name, item.id)
                if action_id is not None:
                    created.append(action_id)
        return created

    @staticmethod
    def _prefilter(item: FeedItem, keywords: list[str], exclude: list[str]) -> bool:
        text = f"{item.title}\n{item.summary}".lower()
        if any(_contains(text, k) for k in exclude):
            return False
        return not keywords or any(_contains(text, k) for k in keywords)

    def _evaluate(self, item: FeedItem, profile: str, min_score: float) -> int | None:
        job = f"タイトル: {item.title}\nURL: {item.link}\n\n{item.summary[:4000]}"
        result = self.ctx.llm.complete_json(
            SCORE_SYSTEM, f"# プロフィール\n{profile}\n\n# 案件\n{job}", strategy=self.name,
        )
        score = float(result["score"])
        reason = str(result.get("reason", ""))
        self.log.info("%.2f  %s", score, item.title)
        if score < min_score:
            return None

        proposal = self.ctx.llm.complete(
            PROPOSAL_SYSTEM, f"# プロフィール\n{profile}\n\n# 案件\n{job}",
            strategy=self.name, max_tokens=1200,
        ).text.strip()

        return self.ctx.store.add_action(
            self.name, "proposal", item.title,
            {"url": item.link, "score": score, "reason": reason,
             "summary": item.summary[:1500], "proposal": proposal},
        )

    def on_approve(self, action: Action) -> str:
        url = action.payload.get("url", "")
        return (f"提案文を確認して、ご自身のアカウントで応募してください: {url}\n"
                "受注できたら `income-agent revenue add <金額> --strategy freelance_scout` で記録できます。")
