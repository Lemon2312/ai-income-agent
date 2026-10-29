from __future__ import annotations

import hashlib
import json
import re
import shutil
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from income_agent.arxiv import (Paper, days_old, fetch_full_text, fetch_papers, ids_in_text, normalize_id,
                                 search_papers)
from income_agent.feeds import parse_feed
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
あなたは、最新の研究ニュースを一般の人にやさしく紹介するブログの編集者です。
候補の論文（どれも最近公開されたもの）から、いま話題になりそうで、専門知識のない読者向けの解説記事に向いているものを選び、向いている順に並べてください。
- 「話題のサイン」（ニュースで取り上げられた、有名な論文誌に掲載・採択、大規模な共同研究など）がある論文を優先する
- 「最近のニュースの見出し」に関係する論文を優先する
- 記録の更新、初めての実証、新しいハードウェアなど、何が新しくできるようになったのかを一般の人にも説明できる成果を優先する
- 小さな改良にとどまるもの、純粋に数学的な理論、前提知識が多すぎて説明しきれないものは選ばない
- 向いていないものは入れなくてよい
必ず次のJSONのみを返してください: {"ids": ["論文ID", ...]}"""

TOP_VENUES = re.compile(r"\b(Nature|Science|Physical Review Letters|PRL|PRX|Phys\. ?Rev\. ?Lett|Nature Physics|"
                        r"Nature Communications|Science Advances|npj Quantum)\b", re.I)
EXPERIMENTAL = re.compile(r"\b(experiment\w*|demonstrat\w*|realiz\w*|implement\w*|processor|hardware|device)\b", re.I)

PAPER_SECTIONS = ["ひとことで言うと", "なぜこの研究が必要？", "何をしたの？", "何がすごいの？",
                  "まだできないこと・課題", "用語ミニ辞典"]

PAPER_RULES = """- 論文の成果・数値・主張は、下の「論文の要旨」と「論文本文の抜粋」に書かれているものだけを使う。書かれていない結果や数値を作らない
- 数値を書くときは、何の数値か（どの条件・どの装置・どの単位か）を論文どおり正確に書く。別の条件の数値と取り違えない
- 理解を助けるための一般的な補足やたとえ話は書いてよいが、論文の内容と区別できるように書く（「たとえるなら」「一般に」など）
- 成果を誇張しない。論文自身が述べている限界や、まだできないことも書く
- 専門用語には、出てきたその場でやさしい説明を添える
- 1段落は2〜3文まで。長い説明を続けず、図・表・箇条書き・会話の部品で見せる
- 前のパートで説明したこと（量子ビットとは何か、など）を繰り返さない。成果の数値の詳しい説明は「何がすごいの？」にまとめ、ほかのパートで何度も書かない
- 数式の記法（LaTeX の $…$、\\times、^{-3} など）は使わない。数値は「6.4×10⁻³」「0.64%」のように普通の文字で書く
- {{< >}} の部品は、前後に空行を入れて独立した行に書く。閉じるときは必ず「>}}」で終える
- 論文の文章を長く引用・翻訳しない。自分の言葉で説明する"""

# How each part is shown. The {{< >}} parts are Hugo shortcodes in the site's layouts/shortcodes.
PAPER_STYLES: dict[str | None, str] = {
    None: "1〜2段落で読者の興味を引く。最後に、タネリス（このブログのマスコット）がこの記事で分かることを一言で紹介する:\n"
          "{{< tanerisu >}}ひとこと{{< /tanerisu >}}",
    "ひとことで言うと": "次の形の要約ボックスだけを書く（ボックスの外に文章は書かない）:\n"
                       "{{< summary >}}\nこの研究の成果を1〜2文で（数値は入れても1つまで）\n\n- ポイント1\n- ポイント2\n- ポイント3\n{{< /summary >}}",
    "なぜこの研究が必要？": "身近なたとえ話を1つ使う。途中に、読者が抱きそうな疑問を1つ書き、すぐあとでタネリスが答える:\n"
                         "{{< ask >}}読者の疑問{{< /ask >}}\n{{< tanerisu >}}答え{{< /tanerisu >}}",
    "何をしたの？": "研究でしたことを3〜5ステップの図にする（各ステップは20字以内）:\n"
                  '{{< flow "ステップ1" "ステップ2" "ステップ3" >}}\n'
                  "図のあとに、各ステップを1〜2文ずつ説明する。数値の詳しい説明は「何がすごいの？」で書くので、ここでは書かない。これまでの方法と比べられるなら、Markdownの表（項目／これまで／今回）を使ってよい",
    "何がすごいの？": "論文の要旨・本文の抜粋にそのまま書かれている数値を2〜4個選び、数字カードにする:\n"
                    '{{< stats >}}\n{{< stat value="2.4倍" label="何の数値か（条件も正確に）" >}}\n{{< /stats >}}\n'
                    "value は数値から始める（例: 2.4倍、0.143%、101個）。論文にない数値や、自分で計算した数値は使わない。"
                    "使える数値がなければ数字カードは使わず、これまでとの違いを表で示す。カードのあとで、それぞれの数値の意味を説明する",
    "まだできないこと・課題": "課題を箇条書き（2〜4個）で示し、それぞれ1〜2文で説明する。最後に、タネリスが今後に期待することを一言:\n"
                          '{{< tanerisu mood="puzzled" >}}ひとこと{{< /tanerisu >}}',
    "用語ミニ辞典": "この記事に出てきた専門用語を3〜5個、次の形だけで書く（ほかの文章は書かない）:\n"
                  '{{< term name="用語" >}}中学生にもわかる1〜2文の説明{{< /term >}}',
}

# Shortcodes the site provides, and whether they wrap content. Anything else the LLM writes
# (or a broken one) is removed, because an unknown or unclosed shortcode fails the whole Hugo build.
SHORTCODES = {"summary": True, "stats": True, "ask": True, "tanerisu": True, "term": True,
              "stat": False, "flow": False}
_SHORTCODE = re.compile(r"\{\{[<%](.*?)[>%]\}\}")
# "{{< flow "a" "b" }}": the closing ">" is the most common slip; Hugo fails the build on it.
_MISSING_CLOSER = re.compile(r"\{\{([<%])([^{}\n]*?[^>%\s])\s*\}\}")
_STRAY_DELIMITER = re.compile(r"\{\{[<%]|[>%]\}\}")
_BLOCK_OPEN = re.compile(r"[ \t]*(\{\{< (?:summary|stats|ask|tanerisu|term|flow)\b)")
_BLOCK_CLOSE = re.compile(r"(\{\{< /(?:summary|stats|ask|tanerisu|term) >\}\}|\{\{< flow [^\n]*?>\}\})[ \t]*")

_SUPERSCRIPT = str.maketrans("0123456789-+", "⁰¹²³⁴⁵⁶⁷⁸⁹⁻⁺")
_SUBSCRIPT = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
_TEX_SYMBOLS = {r"\times": "×", r"\pm": "±", r"\approx": "≈", r"\sim": "〜", r"\cdot": "·", r"\leq": "≤",
                r"\geq": "≥", r"\le": "≤", r"\ge": "≥", r"\%": "%", r"\mu": "μ", r"\pi": "π", r"\alpha": "α",
                r"\beta": "β", r"\gamma": "γ", r"\Lambda": "Λ", r"\lambda": "λ", r"\psi": "ψ", r"\to": "→"}


def plain_math(text: str) -> str:
    """Turns the simple LaTeX the LLM writes for numbers ($(6.4\\pm0.2)\\times10^{-3}$) into plain
    characters (6.4±0.2)×10⁻³, because the site doesn't render math."""
    def convert(expr: str) -> str:
        expr = re.sub(r"\\(?:text|mathrm|mathbf)\{([^{}]*)\}", r"\1", expr)
        for tex, char in _TEX_SYMBOLS.items():
            expr = re.sub(re.escape(tex) + r"(?![A-Za-z])", char, expr)
        expr = re.sub(r"\^\{([-+0-9]+)\}|\^([-+]?[0-9])", lambda m: (m[1] or m[2]).translate(_SUPERSCRIPT), expr)
        expr = re.sub(r"_\{([0-9]+)\}|_([0-9])", lambda m: (m[1] or m[2]).translate(_SUBSCRIPT), expr)
        return expr.replace("{", "").replace("}", "").replace("\\", "")

    text = re.sub(r"\$([^$\n]{1,120})\$", lambda m: convert(m[1]), text)
    text = re.sub(r"\\\((.{1,120}?)\\\)", lambda m: convert(m[1]), text)
    text = re.sub(r"(\d)\^\{([-+0-9]+)\}|(\d)\^([-+]?[0-9]+)",
                  lambda m: (m[1] or m[3]) + (m[2] or m[4]).translate(_SUPERSCRIPT), text)
    for tex in (r"\times", r"\pm"):
        text = re.sub(re.escape(tex) + r"(?![A-Za-z])", _TEX_SYMBOLS[tex], text)
    return text
_NAMED = re.compile(r'(\w+)="([^"]*)"')


def _shortcode_params(name: str, args: str) -> str | None:
    """Canonical parameter text for a shortcode, or None when it can't be rendered."""
    args = re.sub(r"[“”„＂]", '"', args).strip().rstrip("/").strip()
    if name == "flow":
        steps = re.findall(r'"([^"]*)"', args)
        if len(steps) < 2 or re.sub(r'\s*"[^"]*"\s*', "", args):
            return None
        return " ".join(f'"{s.strip()}"' for s in steps)
    if re.sub(_NAMED, "", args).strip():
        return None
    named = dict(_NAMED.findall(args))
    required = {"stat": ("value", "label"), "term": ("name",)}.get(name, ())
    if any(not named.get(k, "").strip() for k in required):
        return None
    allowed = {"stat": ("value", "label"), "term": ("name",), "tanerisu": ("mood", "side")}.get(name, ())
    if name == "tanerisu" and named.get("mood") not in (None, "", "happy", "puzzled"):
        named.pop("mood")
    return " ".join(f'{k}="{named[k].strip()}"' for k in allowed if named.get(k, "").strip())


def clean_shortcodes(markdown: str) -> str:
    """Keeps only well-formed uses of the site's shortcodes. Unknown, malformed or
    unbalanced tags are dropped and the text inside them is kept. Block components are
    put on lines of their own so they never end up inside a paragraph."""
    markdown = _MISSING_CLOSER.sub(lambda m: f"{{{{{m[1]}{m[2]} {'>' if m[1] == '<' else '%'}}}}}", markdown)
    tokens = []  # (start, end, replacement or None, name, kind)
    for m in _SHORTCODE.finditer(markdown):
        inner = re.match(r"\s*(/?)\s*([\w-]+)(.*)$", m.group(1), flags=re.S)
        if not inner or inner.group(2) not in SHORTCODES:
            tokens.append([m.start(), m.end(), None, "", "bad"])
            continue
        closing, name, args = inner.groups()
        if closing:
            tokens.append([m.start(), m.end(), f"{{{{< /{name} >}}}}", name, "close"])
            continue
        params = _shortcode_params(name, args)
        text = None if params is None else f"{{{{< {name}{' ' + params if params else ''} >}}}}"
        tokens.append([m.start(), m.end(), text, name, "open" if SHORTCODES[name] else "single"])
    stack: list[list] = []
    for tok in tokens:
        if tok[4] == "open":
            stack.append(tok)
        elif tok[4] == "close":
            if any(t[3] == tok[3] for t in stack):
                while stack[-1][3] != tok[3]:
                    stack.pop()[2] = None
                opener = stack.pop()
                if opener[2] is None:
                    tok[2] = None
            else:
                tok[2] = None
    for tok in stack:
        tok[2] = None
    out, pos = [], 0
    for start, end, text, *_ in tokens:
        out.append(_STRAY_DELIMITER.sub("", markdown[pos:start]))
        out.append(text or "")
        pos = end
    out.append(_STRAY_DELIMITER.sub("", markdown[pos:]))
    text = _BLOCK_OPEN.sub(r"\n\n\1", "".join(out))
    text = _BLOCK_CLOSE.sub(r"\1\n\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()

PAPER_SECTION_SYSTEM = """TASK: write_section
あなたは、研究論文を専門知識のない読者にやさしく、楽しく解説するライターです。記事のうち、指定された1つのパートだけをMarkdownで書いてください。
- 見出し（## ）は書かず、本文だけを出力する
- 「表現のしかた」に書かれた {{< >}} の部品（要約ボックス・図・数字カード・会話・用語カード）を、書かれた形のとおりに使う。部品の名前や書き方を変えない
- 文章は短い段落で、話しかけるように書く。読者がワクワクするように、何が新しいのかをはっきり伝える"""

PAPER_REVISE_SYSTEM = """TASK: revise_section
あなたは編集長の指摘を反映して記事を改善するライターです。指定された1つのパートを書き直してください。
- このパートに関係する指摘をすべて解消する。関係ない指摘は無視してよい
- 見出し（## ）は書かず、本文だけを出力する
- 「表現のしかた」に書かれた {{< >}} の部品は、書かれた形のとおりに残す"""

PAPER_REVIEW_SYSTEM = """TASK: review_article
あなたは、研究をやさしく紹介するメディアの編集長です。論文解説の記事を次の5項目でそれぞれ1〜5点で採点してください（4点=そのまま公開できる水準）。
- usefulness: 専門知識のない読者が、この研究で何がわかったのか・何がすごいのかを理解できるか
- accuracy: 記事の成果・数値が、論文の要旨・本文の抜粋と合っているか。論文にない数値や、条件の取り違えがあれば1〜2点
- depth: 一般論の繰り返しではなく、この論文ならではの内容が書かれているか
- readability: 短い段落・図・数字カード・会話などで読みやすく、同じ説明の繰り返しがないか
- compliance: 誇張・断定や「執筆ルール」への違反がないか（あれば1点）
注意:
- 記事の中の {{< … >}} は、要約ボックス・図・数字カード・会話・用語カードを表示する部品なので、中の文章を本文として評価する
- issues には、どの見出しをどう直すべきかを具体的に書く（4点以上の項目については書かなくてよい）
必ず次のJSONのみを返してください:
{"scores": {"usefulness": 1-5, "accuracy": 1-5, "depth": 1-5, "readability": 1-5, "compliance": 1-5},
 "issues": ["「見出し名」: 改善点", ...], "description": "検索結果に表示する説明文（全角120字以内）"}"""

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
        both count, so an unattended approval queue doesn't pile up either. Articles
        made in manual runs are not counted, so trying things by hand never uses up
        the schedule's quota."""
        limit = int(self.cfg.get("max_articles_per_day", 2))
        since = day_start_iso(self.ctx.timezone)
        used = sum(1 for a in self.ctx.store.list_actions(None, limit=1000)
                   if a.strategy == self.name and a.kind == "article" and not a.payload.get("manual")
                   and (a.status == PENDING or (a.status == DONE and (a.decided_at or a.created_at) >= since)))
        return max(0, limit - used)

    def run(self) -> list[int]:
        per_run = int(self.cfg.get("articles_per_run", 1))
        # Manual runs ignore the daily limit; the LLM budget still applies.
        count = per_run if self.ctx.manual else min(per_run, self.remaining_today())
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
        """Papers listed in `papers` come first, in order. When none are left, recent
        papers matching `arxiv_query` are ranked by signs of attention and the LLM picks."""
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
        needed = count - len(papers)

        headlines, news_ids = self._news()
        taken = {p.id for p in papers}
        found = search_papers(self.ctx.fetch, query, int(self.cfg.get("arxiv_candidates", 60)),
                              days=int(self.cfg.get("recent_days", 14)))
        # Papers that news articles link to are worth a look even if the search missed them.
        extra = [i for i in news_ids if i not in {p.id for p in found}][:10]
        if extra:
            max_age = int(self.cfg.get("news_max_days", 90))
            found += [p for p in fetch_papers(self.ctx.fetch, extra) if days_old(p) <= max_age]
        candidates = [p for p in found if p.id not in taken and not self._paper_done(p.id)]
        if not candidates:
            return papers
        ranked = sorted(candidates, key=lambda p: (self._hot_signals(p, news_ids)[0], p.published), reverse=True)
        ranked = ranked[:int(self.cfg.get("pick_from", 15))]

        def describe(p: Paper) -> str:
            signals = self._hot_signals(p, news_ids)[1]
            return (f"ID: {p.id}\n公開日: {p.published}\nタイトル: {p.title}\n"
                    + (f"話題のサイン: {'、'.join(signals)}\n" if signals else "")
                    + f"要旨: {p.abstract[:600]}")

        news = "\n".join(f"- {h}" for h in headlines[:15])
        try:
            result = self.ctx.llm.complete_json(
                PICK_PAPER_SYSTEM,
                f"ジャンル: {self.cfg.get('niche', '')}\n想定読者: {self.cfg.get('audience', '')}\n"
                f"必要数: {needed}\n"
                + (f"\n# 最近のニュースの見出し（参考）\n{news}\n" if news else "")
                + "\n# 候補\n" + "\n\n".join(describe(p) for p in ranked),
                strategy=self.name,
            )
            by_id = {p.id: p for p in ranked}
            picked = [by_id[i] for i in dict.fromkeys(normalize_id(str(x)) for x in result.get("ids", []))
                      if i in by_id]
        except LLMError as e:
            self.log.warning("could not pick a paper: %s", e)
            picked = []
        # If the LLM picks nothing usable, fall back to the paper with the most signs of attention.
        return (papers + (picked or ranked[:needed]))[:count]

    def _news(self) -> tuple[list[str], list[str]]:
        """Headlines and linked arXiv ids from the `news_feeds` RSS feeds."""
        headlines: list[str] = []
        ids: list[str] = []
        for url in self.cfg.get("news_feeds", []):
            try:
                data = self.ctx.fetch(url)
                headlines += [item.title for item in parse_feed(data) if item.title]
                ids += ids_in_text(data)
            except Exception as e:  # noqa: BLE001 - news only helps the ranking
                self.log.warning("could not read news feed %s: %s", url, e)
        return headlines, list(dict.fromkeys(ids))

    @staticmethod
    def _hot_signals(paper: Paper, news_ids: list[str]) -> tuple[int, list[str]]:
        """A rough attention score with the reasons, shown to the LLM that picks."""
        score, reasons = 0, []
        if paper.id in news_ids:
            score += 6
            reasons.append("ニュース記事で紹介されている")
        venue = TOP_VENUES.search(f"{paper.journal_ref} {paper.comment}")
        if venue:
            score += 3
            reasons.append(f"論文誌の情報: {(paper.journal_ref or paper.comment)[:80]}")
        if len(paper.authors) >= 20:
            score += 2
            reasons.append(f"大規模な共同研究（著者{len(paper.authors)}人）")
        elif len(paper.authors) >= 8:
            score += 1
        if EXPERIMENTAL.search(paper.abstract):
            score += 1
            reasons.append("実験・実証の成果")
        return score, reasons

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
        per_section = max(300, int((min_chars - 200) / len(PAPER_SECTIONS) * 1.15))
        parts: list[dict[str, Any]] = [
            {"heading": None, "points": [str(plan.get("intro", "この研究のどこがおもしろいのか"))], "target": 200,
             "style": PAPER_STYLES[None]}
        ]
        for heading in PAPER_SECTIONS:
            items = points.get(heading) or [f"論文の内容にもとづいて「{heading}」を説明する"]
            parts.append({"heading": heading, "points": [str(x) for x in items], "target": per_section,
                          "style": PAPER_STYLES[heading]})
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
        earlier = []
        for p in parts:
            if p["heading"] == part["heading"]:
                break
            if p.get("body"):
                earlier.append(f"## {p['heading'] or '導入'}\n{p['body'][:400]}")
        return (f"{self._brief(title)}\n\n# 記事の見出し構成\n{toc}\n\n"
                + ("# ここまでに書いた内容（同じ説明を繰り返さない）\n" + "\n\n".join(earlier) + "\n\n" if earlier else "")
                + f"# 今回書くパート\n{name}\n\n# このパートで書く内容\n{points}\n\n"
                + (f"# 表現のしかた（必ず守る）\n{part['style']}\n\n" if part.get("style") else "")
                + f"# 文字数\n{part['target']}字以上")

    def _write_parts(self, title: str, parts: list[dict[str, Any]]) -> None:
        for part in parts:
            part["body"] = self.ctx.llm.complete(
                PAPER_SECTION_SYSTEM if self._source else SECTION_SYSTEM,
                self._part_prompt(title, parts, part), strategy=self.name,
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
                PAPER_REVISE_SYSTEM if self._source else REVISE_SECTION_SYSTEM,
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
            body = clean_shortcodes(plain_math(re.sub(r"\A##\s+.*\n+", "", part["body"].strip())))
            out.append(body if part["heading"] is None else f"## {part['heading']}\n\n{body}")
        return "\n\n".join(out) + "\n"

    def _review(self, title: str, body: str) -> dict[str, Any]:
        min_chars = int(self.cfg.get("min_chars", 2500))
        chars = _body_chars(body)
        review = self.ctx.llm.complete_json(
            PAPER_REVIEW_SYSTEM if self._source else REVIEW_SYSTEM, f"{self._brief(title)}\n文字数: {chars}字\n\n# 記事\n{body}",
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
        if self.ctx.manual:
            payload["manual"] = True
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
