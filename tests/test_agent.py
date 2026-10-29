from __future__ import annotations

import re
import urllib.error
import urllib.parse
from datetime import date, timedelta
import subprocess
import tomllib
from importlib import resources
from pathlib import Path

import pytest

from income_agent.agent import Agent
from income_agent import arxiv
from income_agent.arxiv import fetch_full_text, fetch_papers, ids_in_text, normalize_id, parse_papers
from income_agent.cli import main
from income_agent.config import load_config
from income_agent.feeds import parse_feed
from income_agent.llm import LLMResponse, MockLLM, parse_json
from income_agent.site import init_site
from income_agent.storage import DONE, PENDING, REJECTED
from income_agent.strategies.content_publisher import clean_shortcodes, insert_affiliate_links, plain_math
from income_agent.strategies.freelance_scout import _contains

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>jobs</title>
<item><title>Python automation script for invoices</title><link>https://jobs.example/1</link>
<guid>job-1</guid><description>&lt;p&gt;Need a &lt;b&gt;Python&lt;/b&gt; developer&lt;/p&gt;</description></item>
<item><title>Logo design</title><link>https://jobs.example/2</link><guid>job-2</guid>
<description>Graphic designer wanted</description></item>
<item><title>Data scraping (unpaid trial)</title><link>https://jobs.example/3</link><guid>job-3</guid>
<description>python scraping</description></item>
</channel></rss>"""

ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>t</title>
<entry><id>urn:a</id><title>Atom job</title><link rel="alternate" href="https://a.example/x"/>
<summary>hello</summary></entry></feed>"""

TEST_DEFAULTS = {"require_approval": "true", "deploy": '"none"'}


@pytest.fixture(autouse=True)
def git_identity(monkeypatch):
    for kind in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{kind}_NAME", "test")
        monkeypatch.setenv(f"GIT_{kind}_EMAIL", "test@example.com")


def make_config(tmp_path: Path, scout: bool = True, **overrides: str) -> Path:
    template = resources.files("income_agent").joinpath("config.example.toml")
    with resources.as_file(template) as src:
        text = Path(src).read_text(encoding="utf-8")
    text = text.replace('provider = "auto"', 'provider = "mock"')
    if not scout:
        text = text.replace("[strategies.freelance_scout]\nenabled = true",
                            "[strategies.freelance_scout]\nenabled = false")
    for key, value in {**TEST_DEFAULTS, **overrides}.items():
        text, n = re.subn(rf"^{key} = .*$", f"{key} = {value}", text, count=1, flags=re.M)
        assert n == 1, key
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def make_agent(tmp_path: Path, llm=None, **overrides: str) -> Agent:
    cfg = load_config(make_config(tmp_path, **overrides))
    return Agent(cfg, llm=llm or MockLLM(), fetch=lambda url: RSS)


def articles(agent: Agent, status: str = PENDING):
    return [a for a in agent.store.list_actions(status) if a.kind == "article"]


def posts_dir(tmp_path: Path) -> Path:
    return tmp_path / "site" / "content" / "posts"


class ScriptedReviewLLM(MockLLM):
    """Mock whose editor scores follow a fixed script, to exercise the quality gate."""

    def __init__(self, scores: list[float]):
        self.scores = list(scores)
        self.revisions = 0

    def complete(self, system, user, *, json_mode=False, max_tokens=1500):
        if "TASK: review_article" in system:
            score = self.scores.pop(0) if len(self.scores) > 1 else self.scores[0]
            return LLMResponse(f'{{"score": {score}, "issues": ["具体例が足りない"], "description": "d"}}')
        if "TASK: revise_section" in system:
            self.revisions += 1
            assert "具体例が足りない" in user
        return super().complete(system, user, json_mode=json_mode, max_tokens=max_tokens)


class PricedLLM(MockLLM):
    model = "priced"

    def complete(self, system, user, *, json_mode=False, max_tokens=1500):
        resp = super().complete(system, user, json_mode=json_mode, max_tokens=max_tokens)
        return LLMResponse(resp.text, input_tokens=1_000_000, output_tokens=0)


# --- feeds & helpers ---------------------------------------------------------

def test_parse_rss_and_atom():
    items = parse_feed(RSS)
    assert [i.id for i in items] == ["job-1", "job-2", "job-3"]
    assert items[0].summary == "Need a Python developer"
    atom = parse_feed(ATOM)
    assert atom[0].link == "https://a.example/x" and atom[0].title == "Atom job"


def test_parse_json_handles_fences():
    assert parse_json('```json\n{"score": 0.5}\n```') == {"score": 0.5}
    assert parse_json('sure: {"a": 1} done') == {"a": 1}


def test_keyword_matching_uses_word_boundaries():
    assert _contains("build an ai agent", "ai")
    assert not _contains("maintain the platform", "ai")
    assert _contains("業務の自動化をお願いします", "自動化")


def test_affiliate_links_are_marked_sponsored():
    md = "# Notion入門\n\nNotionは便利です。Notionで管理。\n\n## Zapier\n\nZapierも。"
    out, n = insert_affiliate_links(md, [
        {"keyword": "Notion", "url": "https://aff/n"},
        {"keyword": "Zapier", "url": "https://aff/z"},
    ])
    assert n == 2
    assert "# Notion入門" in out  # headings untouched
    assert '[Notion](https://aff/n "sponsored")は便利です。Notionで管理。' in out
    assert '## Zapier\n\n[Zapier](https://aff/z "sponsored")も。' in out


# --- pipeline ----------------------------------------------------------------

def test_full_cycle_queues_proposals_and_articles(tmp_path):
    agent = make_agent(tmp_path)
    result = agent.run_once()
    assert not result.errors

    # job-1 matches keywords; job-2 has no keyword; job-3 hits exclude ("unpaid"). Both feeds
    # return the same items so the second feed must be deduplicated.
    proposals = [a for a in agent.store.list_actions(PENDING) if a.kind == "proposal"]
    assert [p.title for p in proposals] == ["Python automation script for invoices"]
    assert "[mock]" in proposals[0].payload["proposal"]

    pending = articles(agent)
    assert [a.title for a in pending] == ["フリーランスの請求書作成をAIで5分に短縮する方法"]
    draft = Path(pending[0].payload["draft_path"]).read_text(encoding="utf-8")
    assert draft.startswith("---\ntitle:") and "description:" in draft
    assert pending[0].payload["quality"] == pytest.approx(0.84)

    agent.run_once()
    assert len([a for a in agent.store.list_actions(PENDING) if a.kind == "proposal"]) == 1
    titles = [a.title for a in articles(agent)]
    assert len(titles) == 2 and len(set(titles)) == 2


def test_approve_article_publishes_and_reject(tmp_path):
    agent = make_agent(tmp_path)
    agent.run_once()
    article = articles(agent)[0]
    proposal = next(a for a in agent.store.list_actions(PENDING) if a.kind == "proposal")

    agent.approve(article.id)
    assert (posts_dir(tmp_path) / Path(article.payload["draft_path"]).name).exists()
    assert agent.store.get_action(article.id).status == DONE

    agent.reject(proposal.id)
    assert agent.store.get_action(proposal.id).status == REJECTED
    with pytest.raises(ValueError):
        agent.approve(proposal.id)


def test_auto_publish_respects_daily_limit(tmp_path):
    agent = make_agent(tmp_path, scout=False, require_approval="false", max_articles_per_day="2")
    for _ in range(4):
        agent.run_once()
    assert not articles(agent)
    assert len(articles(agent, DONE)) == 2
    assert len(list(posts_dir(tmp_path).glob("*.md"))) == 2


def test_manual_runs_ignore_and_do_not_use_the_daily_limit(tmp_path):
    cfg = load_config(make_config(tmp_path, scout=False, require_approval="false", max_articles_per_day="1"))
    scheduled = Agent(cfg, llm=MockLLM(), fetch=lambda url: RSS)
    manual = Agent(cfg, llm=MockLLM(), fetch=lambda url: RSS, store=scheduled.store, manual=True)
    scheduled.run_once()
    scheduled.run_once()
    assert len(articles(scheduled, DONE)) == 1  # the schedule stops at the limit
    manual.run_once()
    manual.run_once()
    assert len(articles(scheduled, DONE)) == 3  # a manual run still writes one each time
    assert sum(1 for a in articles(scheduled, DONE) if a.payload.get("manual")) == 2
    scheduled.store.conn.execute("DELETE FROM actions WHERE payload NOT LIKE '%manual%'")
    scheduled.store.conn.commit()
    scheduled.run_once()  # manual articles don't use up the schedule's quota
    assert len(articles(scheduled, DONE)) == 3


def test_quality_gate_revises_until_good_enough(tmp_path):
    llm = ScriptedReviewLLM([0.5, 0.6, 0.9])
    agent = make_agent(tmp_path, llm=llm, scout=False)
    agent.run_once()
    article = articles(agent)[0]
    assert llm.revisions == 2 * 5  # two rounds over 4 sections + summary (intro is kept)
    assert article.payload["quality_history"] == [0.5, 0.6, 0.9]


def test_quality_gate_discards_bad_articles(tmp_path):
    agent = make_agent(tmp_path, llm=ScriptedReviewLLM([0.3]), scout=False,
                       require_approval="false")
    agent.run_once()
    assert not articles(agent, DONE)
    rejected = [a for a in agent.store.list_actions(REJECTED) if a.kind == "article_rejected"]
    assert len(rejected) == 1 and rejected[0].payload["quality_history"] == [0.3, 0.3, 0.3]
    assert not posts_dir(tmp_path).exists()

    # A rejected topic gets one more attempt (max_attempts_per_topic = 2), then is dropped.
    agent.run_once()
    agent.run_once()
    titles = [a.title for a in reversed(agent.store.list_actions(REJECTED))]
    assert titles[0] == titles[1] != titles[2]


class TerseWriterLLM(MockLLM):
    def complete(self, system, user, *, json_mode=False, max_tokens=1500):
        if "TASK: write_section" in system:
            return LLMResponse("短い本文です。")
        return super().complete(system, user, json_mode=json_mode, max_tokens=max_tokens)


def test_short_articles_fail_quality(tmp_path):
    agent = make_agent(tmp_path, llm=TerseWriterLLM(), scout=False, max_revisions="0")
    agent.run_once()
    assert not articles(agent)
    rejected = agent.store.list_actions(REJECTED)[0]
    assert any("文字数" in issue for issue in rejected.payload["issues"])


def test_budget_stops_cycle_and_costs_are_recorded(tmp_path):
    agent = make_agent(tmp_path, llm=PricedLLM())
    agent.config.agent.daily_llm_budget_usd = 0.2  # each call costs $0.15
    result = agent.run_once()
    assert result.budget_exhausted
    assert agent.llm.spent_today_usd() == pytest.approx(0.30)
    assert agent.store.total("cost") == pytest.approx(0.30 * 150)


def test_article_is_written_section_by_section(tmp_path):
    agent = make_agent(tmp_path, scout=False)
    agent.run_once()
    article = articles(agent)[0]
    text = Path(article.payload["draft_path"]).read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+)$", text, flags=re.M)
    assert headings == [f"[mock] 見出し{i}" for i in range(1, 5)] + ["まとめ"]
    assert article.payload["chars"] >= 3000
    assert article.payload["scores"]["accuracy"] == 5


class RecordingLLM(MockLLM):
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def complete(self, system, user, *, json_mode=False, max_tokens=1500):
        self.calls.append((system, user))
        return super().complete(system, user, json_mode=json_mode, max_tokens=max_tokens)


def test_guidelines_reach_every_stage(tmp_path):
    cfg_path = make_config(tmp_path, scout=False)
    text = re.sub(r"^topics = \[.*?\]\n", "topics = []\n", cfg_path.read_text(encoding="utf-8"),
                  flags=re.S | re.M)
    text = text.replace(
        "[strategies.content_publisher]\n",
        '[strategies.content_publisher]\nguidelines = ["レポートの代筆につながる方法は紹介しない"]\n')
    cfg_path.write_text(text, encoding="utf-8")
    llm = RecordingLLM()
    Agent(load_config(cfg_path), llm=llm, fetch=lambda url: RSS).run_once()

    tasks = {re.search(r"TASK: (\w+)", s).group(1) for s, u in llm.calls
             if "レポートの代筆につながる方法は紹介しない" in u}
    assert {"ideate_topics", "outline_article", "write_section", "review_article"} <= tasks


def test_repo_agent_config_explains_quantum_papers():
    cfg = load_config(Path(__file__).resolve().parent.parent / "agent.toml")
    publisher = cfg.strategies["content_publisher"]
    assert publisher["source"] == "arxiv"
    assert "量子コンピュータ" in publisher["niche"]
    assert "quant-ph" in publisher["arxiv_query"]
    assert publisher["recent_days"] <= 30 and publisher["news_feeds"]
    assert publisher["max_articles_per_day"] == 1
    assert any("書かれていない" in g for g in publisher["guidelines"])
    assert any("誇張しない" in g for g in publisher["guidelines"])
    categories = tomllib.loads((Path(__file__).resolve().parent.parent / "site" / "data" / "categories.toml")
                               .read_text(encoding="utf-8"))
    assert "量子コンピュータ" in categories


class FixedScoresLLM(MockLLM):
    def __init__(self, scores):
        self.scores = scores

    def complete(self, system, user, *, json_mode=False, max_tokens=1500):
        if "TASK: review_article" in system:
            import json
            return LLMResponse(json.dumps({"scores": self.scores, "issues": ["x"], "description": "d"}))
        return super().complete(system, user, json_mode=json_mode, max_tokens=max_tokens)


def test_rubric_scoring_and_compliance_veto(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    good = make_agent(tmp_path / "a", llm=FixedScoresLLM(
        {"usefulness": 4, "accuracy": 4, "depth": 4, "readability": 4, "compliance": 4}), scout=False)
    good.run_once()
    assert articles(good)[0].payload["quality"] == pytest.approx(0.8)

    bad = make_agent(tmp_path / "b", llm=FixedScoresLLM(
        {"usefulness": 5, "accuracy": 5, "depth": 5, "readability": 5, "compliance": 1}), scout=False,
        max_revisions="0")
    bad.run_once()
    assert not articles(bad)
    assert bad.store.list_actions(REJECTED)[0].payload["quality"] == 0.0


class ShrinkingReviserLLM(ScriptedReviewLLM):
    def complete(self, system, user, *, json_mode=False, max_tokens=1500):
        if "TASK: revise_section" in system:
            return LLMResponse("短い")
        return super().complete(system, user, json_mode=json_mode, max_tokens=max_tokens)


def test_revisions_that_shrink_a_section_are_discarded(tmp_path):
    agent = make_agent(tmp_path, llm=ShrinkingReviserLLM([0.5, 0.9]), scout=False)
    agent.run_once()
    text = Path(articles(agent)[0].payload["draft_path"]).read_text(encoding="utf-8")
    assert "短い" not in text


class CategoryLLM(MockLLM):
    def __init__(self, category):
        self.category = category
        self.candidates = ""

    def complete(self, system, user, *, json_mode=False, max_tokens=1500):
        resp = super().complete(system, user, json_mode=json_mode, max_tokens=max_tokens)
        if "TASK: outline_article" in system:
            import json
            self.candidates = user
            plan = json.loads(resp.text)
            return LLMResponse(json.dumps({**plan, "category": self.category}, ensure_ascii=False))
        return resp


def test_articles_get_a_category_from_the_site(tmp_path):
    init_site(tmp_path / "site", "t", "d", "a", git=False)
    llm = CategoryLLM("活用術")
    agent = make_agent(tmp_path, llm=llm, scout=False)
    agent.run_once()
    assert "# カテゴリ候補\n- はじめての方へ\n- 活用術\n- ツール比較" in llm.candidates
    text = Path(articles(agent)[0].payload["draft_path"]).read_text(encoding="utf-8")
    assert 'categories: ["活用術"]\n' in text.split("---")[1]


def test_unknown_category_falls_back_to_the_first(tmp_path):
    init_site(tmp_path / "site", "t", "d", "a", git=False)
    agent = make_agent(tmp_path, llm=CategoryLLM("存在しないカテゴリ"), scout=False)
    agent.run_once()
    text = Path(articles(agent)[0].payload["draft_path"]).read_text(encoding="utf-8")
    assert 'categories: ["はじめての方へ"]' in text


def test_articles_without_site_categories_have_none(tmp_path):
    agent = make_agent(tmp_path, scout=False)
    agent.run_once()
    text = Path(articles(agent)[0].payload["draft_path"]).read_text(encoding="utf-8")
    assert "categories:" not in text


def test_repo_site_theme_matches_template():
    root = Path(__file__).resolve().parent.parent
    template = root / "income_agent" / "site_template"
    for sub in ("layouts", "static"):
        for src in (template / sub).rglob("*"):
            if src.is_file():
                assert src.read_bytes() == (root / "site" / src.relative_to(template)).read_bytes(), src
    categories = (root / "site" / "data" / "categories.toml").read_text(encoding="utf-8")
    posts = root / "site" / "content" / "posts"
    for post in [*posts.glob("2*.md"), *posts.glob("2*/index.md")]:
        match = re.search(r'^categories: \["(.+?)"\]', post.read_text(encoding="utf-8"), flags=re.M)
        assert match and f'["{match.group(1)}"]' in categories, post


# --- site & deploy -----------------------------------------------------------

def test_init_site_creates_hugo_blog(tmp_path):
    site = tmp_path / "site"
    steps = init_site(site, "テストブログ", "説明文", "太郎")
    assert steps
    assert 'title = "テストブログ"' in (site / "hugo.toml").read_text(encoding="utf-8")
    assert (site / ".github" / "workflows" / "hugo.yml").exists()
    assert (site / ".gitignore").exists()
    assert "太郎" in (site / "content" / "privacy.md").read_text(encoding="utf-8")
    assert (site / "layouts" / "_default" / "_markup" / "render-link.html").exists()
    for mascot_file in ("layouts/partials/mascot.html", "layouts/shortcodes/tanerisu.html",
                        "layouts/404.html", "static/favicon.svg"):
        assert (site / mascot_file).exists(), mascot_file
    params = tomllib.loads((site / "hugo.toml").read_text(encoding="utf-8"))["params"]
    assert params["mascotName"] == "タネリス" and params["mascotMessages"] and params["mascotOutro"]
    log = subprocess.run(["git", "-C", str(site), "log", "--oneline"], capture_output=True, text=True)
    assert "Initial blog" in log.stdout
    with pytest.raises(FileExistsError):
        init_site(site, "t", "d", "a")


def test_auto_publish_pushes_to_site_repo(tmp_path):
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    site = tmp_path / "site"
    init_site(site, "t", "d", "a")
    subprocess.run(["git", "-C", str(site), "remote", "add", "origin", str(remote)], check=True)
    subprocess.run(["git", "-C", str(site), "push", "-q", "-u", "origin", "main"], check=True)

    agent = make_agent(tmp_path, scout=False, require_approval="false", deploy='"git"')
    agent.run_once()

    log = subprocess.run(["git", "-C", str(remote), "log", "--format=%s", "main"],
                         capture_output=True, text=True, check=True).stdout
    assert log.splitlines()[0] == "Add post: フリーランスの請求書作成をAIで5分に短縮する方法"


def test_deploy_failure_does_not_crash_cycle(tmp_path):
    agent = make_agent(tmp_path, scout=False, require_approval="false", deploy='"git"')
    result = agent.run_once()  # site_dir was never initialised as a git repo
    assert not result.errors
    assert len(articles(agent, DONE)) == 1


def _repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """A repository (like this one) whose blog lives in a site/ subdirectory."""
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    init_site(tmp_path / "site", "t", "d", "a", git=False, workflow=False)
    for cmd in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "init"],
                ["remote", "add", "origin", str(remote)], ["push", "-q", "-u", "origin", "main"]):
        subprocess.run(["git", "-C", str(tmp_path), *cmd], check=True)
    return tmp_path, remote


def test_git_deploy_works_when_site_is_a_subdirectory(tmp_path):
    _, remote = _repo_with_remote(tmp_path)
    agent = make_agent(tmp_path, scout=False, require_approval="false", deploy='"git"')
    assert not agent.run_once().errors
    files = subprocess.run(["git", "-C", str(remote), "show", "--name-only", "--format=", "main"],
                           capture_output=True, text=True, check=True).stdout.split()
    assert len(files) == 1 and files[0].startswith("site/content/posts/")


def test_github_pr_deploy_opens_pull_request(tmp_path, monkeypatch):
    repo, remote = _repo_with_remote(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh_log = tmp_path / "gh.log"
    (bin_dir / "gh").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "$@" > {gh_log}\necho https://github.com/o/r/pull/7\n')
    (bin_dir / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{__import__('os').environ['PATH']}")

    agent = make_agent(tmp_path, scout=False, require_approval="false", deploy='"github-pr"',
                       fail_on_deploy_error="true")
    assert not agent.run_once().errors

    args = gh_log.read_text(encoding="utf-8").splitlines()
    assert args[:3] == ["pr", "create", "--base"] and args[3] == "main"
    branch = args[args.index("--head") + 1]
    assert branch.startswith("post/")
    assert "マージすると公開されます" in "\n".join(args[args.index("--body") + 1:])

    # The post lives only on the PR branch; main and the working tree stay untouched.
    current = subprocess.run(["git", "-C", str(repo), "branch", "--show-current"],
                             capture_output=True, text=True).stdout.strip()
    assert current == "main"
    assert not list(posts_dir(tmp_path).glob("2*.md"))
    branches = subprocess.run(["git", "-C", str(remote), "branch", "--list"],
                              capture_output=True, text=True).stdout
    assert branch in branches


def test_deploy_errors_fail_the_run_when_requested(tmp_path):
    agent = make_agent(tmp_path, scout=False, require_approval="false", deploy='"github-pr"',
                       fail_on_deploy_error="true")
    assert "content_publisher" in agent.run_once().errors


def test_repo_agent_config_requires_api_key(monkeypatch, capsys):
    repo_config = Path(__file__).resolve().parent.parent / "agent.toml"
    cfg = load_config(repo_config)
    assert cfg.llm.provider == "openai"
    assert cfg.strategies["content_publisher"]["deploy"] == "github-pr"
    assert Path(cfg.strategies["content_publisher"]["site_dir"]).joinpath("hugo.toml").exists()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        main(["-c", str(repo_config), "report"])
    assert "OPENAI_API_KEY" in str(exc.value)


# --- CLI ---------------------------------------------------------------------

def test_cli_end_to_end(tmp_path, capsys):
    c = str(make_config(tmp_path, scout=False))
    assert main(["-c", c, "run", "--once"]) == 0
    assert main(["-c", c, "queue"]) == 0
    assert "quality=0.84" in capsys.readouterr().out
    assert main(["-c", c, "show", "1"]) == 0
    assert main(["-c", c, "approve", "1"]) == 0
    assert main(["-c", c, "revenue", "add", "12000", "--strategy", "content_publisher"]) == 0
    assert main(["-c", c, "report"]) == 0
    assert "12,000" in capsys.readouterr().out


def test_cli_init_and_init_site(tmp_path, capsys):
    dest = tmp_path / "config.toml"
    assert main(["-c", str(dest), "init"]) == 0
    assert main(["-c", str(dest), "init"]) == 1
    assert main(["-c", str(dest), "init-site"]) == 0
    hugo = (tmp_path / "site" / "hugo.toml").read_text(encoding="utf-8")
    assert 'title = "フリーランスのAI時短ラボ"' in hugo


# --- arXiv papers ------------------------------------------------------------

def arxiv_entry(paper_id: str, title: str, abstract: str, extra: str = "", authors: int = 2,
                published: str = "2024-08-24") -> str:
    names = "".join(f"<author><name>Author {i}</name></author>" for i in range(authors - 2))
    return f"""<entry><id>http://arxiv.org/abs/{paper_id}v2</id>
<published>{published}T17:00:00Z</published>{extra}{names}
<title>{title}
  (long title)</title>
<summary>  {abstract}
</summary>
<author><name>Alice Example</name></author><author><name>Bob Example</name></author>
<link href="http://arxiv.org/abs/{paper_id}v2" rel="alternate" type="text/html"/>
</entry>"""


def arxiv_feed(*entries: str) -> bytes:
    return ('<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:arxiv="http://arxiv.org/schemas/atom">'
            + "".join(entries) + "</feed>").encode()


LISTED = arxiv_entry("2408.13687", "Quantum error correction below the surface code threshold",
                     "We report logical error rates that fall as the code grows.")
SEARCHED = [arxiv_entry("2501.00001", "A new qubit design", "We build a qubit that keeps its state longer."),
            arxiv_entry("2501.00002", "Bounds on abstract codes", "We prove an inequality.")]
ERROR_ENTRY = """<entry><id>http://arxiv.org/api/errors#incorrect_id_format_for_bad</id>
<title>Error</title><summary>incorrect id format for bad</summary></entry>"""


class ArxivFetch:
    def __init__(self, html: bytes | None = b"<html><body><p>Introduction text.</p>"
                                            b"<h2>References</h2><p>[1] Someone</p></body></html>"):
        self.html = html
        self.urls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if "id_list=" in url:
            return arxiv_feed(*([LISTED] if "2408.13687" in url else []), ERROR_ENTRY)
        if "search_query=" in url:
            return arxiv_feed(*SEARCHED)
        if "/html/" in url:
            if self.html is None:
                raise OSError("no HTML version")
            return self.html
        return RSS


def paper_agent(tmp_path: Path, llm=None, fetch=None, papers: str = '["2408.13687"]',
                query: str = '"cat:quant-ph AND abs:qubit"') -> Agent:
    cfg_path = make_config(tmp_path, scout=False)
    text = cfg_path.read_text(encoding="utf-8").replace(
        "[strategies.content_publisher]\n",
        f'[strategies.content_publisher]\nsource = "arxiv"\npapers = {papers}\narxiv_query = {query}\n')
    cfg_path.write_text(text, encoding="utf-8")
    return Agent(load_config(cfg_path), llm=llm or MockLLM(), fetch=fetch or ArxivFetch())


def test_parse_arxiv_papers_and_ids():
    papers = parse_papers(arxiv_feed(LISTED, ERROR_ENTRY))
    assert len(papers) == 1
    paper = papers[0]
    assert paper.id == "2408.13687" and paper.url == "https://arxiv.org/abs/2408.13687"
    assert paper.title == "Quantum error correction below the surface code threshold (long title)"
    assert paper.abstract.startswith("We report") and paper.published == "2024-08-24"
    assert paper.authors == ["Alice Example", "Bob Example"]
    for raw in ("2408.13687", "arXiv:2408.13687v3", "https://arxiv.org/abs/2408.13687v1",
                "https://arxiv.org/pdf/2408.13687.pdf"):
        assert normalize_id(raw) == "2408.13687", raw
    assert normalize_id("quant-ph/9508027v2") == "quant-ph/9508027"


def test_full_text_drops_references_and_keeps_both_ends():
    paper = parse_papers(arxiv_feed(LISTED))[0]
    assert fetch_full_text(ArxivFetch(), paper) == "Introduction text."
    long_html = ("<p>" + "A" * 9000 + "</p><p>" + "Z" * 9000 + "</p>").encode()
    text = fetch_full_text(ArxivFetch(long_html), paper, limit=3000)
    assert text.startswith("A") and text.endswith("Z") and "中略" in text and len(text) < 3100
    assert fetch_full_text(ArxivFetch(None), paper) == ""


def test_listed_paper_becomes_an_article_with_its_source(tmp_path):
    llm = RecordingLLM()
    fetch = ArxivFetch()
    agent = paper_agent(tmp_path, llm=llm, fetch=fetch)
    agent.run_once()
    article = articles(agent)[0]
    assert article.title == "[mock] 論文をやさしく解説"
    assert article.payload["arxiv_id"] == "2408.13687"
    draft = Path(article.payload["draft_path"])
    assert "arxiv-2408-13687" in draft.name
    text = draft.read_text(encoding="utf-8")
    headings = re.findall(r"^## (.+)$", text, flags=re.M)
    assert headings == ["ひとことで言うと", "なぜこの研究が必要？", "何をしたの？", "しくみをもう少しくわしく",
                        "何がすごいの？", "まだできないこと・課題", "これからどうなる？", "クイズでチェック",
                        "用語ミニ辞典", "元の論文"]
    assert "[arXiv:2408.13687](https://arxiv.org/abs/2408.13687)" in text
    # Every writing and review prompt carries the abstract and the full-text excerpt.
    for system, user in llm.calls:
        if re.search(r"TASK: (outline_paper|write_section|review_article)", system):
            assert "We report logical error rates" in user and "Introduction text." in user
    assert not any("TASK: pick_paper" in s for s, u in llm.calls)

    # The same paper is never written twice; the next run picks from the search instead.
    agent.store.set_status(article.id, DONE)
    agent.run_once()
    assert any("search_query=" in u for u in fetch.urls)
    assert [a.payload["arxiv_id"] for a in articles(agent)] == ["2501.00001"]


def test_papers_are_picked_from_search_when_list_is_empty(tmp_path):
    llm = RecordingLLM()
    agent = paper_agent(tmp_path, llm=llm, papers="[]")
    agent.run_once()
    picks = [u for s, u in llm.calls if "TASK: pick_paper" in s]
    assert len(picks) == 1 and "A new qubit design" in picks[0] and "Bounds on abstract codes" in picks[0]
    assert articles(agent)[0].payload["arxiv_id"] == "2501.00001"


def test_paper_is_retried_until_attempts_run_out(tmp_path):
    agent = paper_agent(tmp_path, llm=FixedScoresLLM({"usefulness": 2, "accuracy": 2, "depth": 2,
                                                      "readability": 2, "compliance": 4}), query='""')
    for _ in range(3):
        agent.run_once()
    rejected = [a for a in agent.store.list_actions(REJECTED) if a.kind == "article_rejected"]
    assert [a.payload["arxiv_id"] for a in rejected] == ["2408.13687", "2408.13687"]


# --- visual components & hot papers ----------------------------------------

def test_clean_shortcodes_keeps_valid_parts_and_drops_broken_ones():
    text = (
        "{{< summary >}}要約\n- 点{{< /summary >}}\n"
        '{{< stats >}}{{< stat value="2.4倍" label="寿命" >}}{{< stat value="5" >}}{{< /stats >}}\n'
        "{{< flow “測る” “直す” >}}\n"
        '{{< flow "1つだけ" >}}\n'
        "{{< ask >}}閉じ忘れ\n"
        '{{< tanerisu mood="angry" >}}こんにちは{{< /tanerisu >}}\n'
        '{{< term name="量子ビット" >}}説明{{< /term >}}{{< /term >}}\n'
        "{{< youtube abc >}}{{% notice %}}注意{{% /notice %}}"
    )
    cleaned = clean_shortcodes(text)
    assert "{{< summary >}}要約\n- 点{{< /summary >}}" in cleaned
    assert '{{< stat value="2.4倍" label="寿命" >}}' in cleaned and 'value="5"' not in cleaned
    assert '{{< flow "測る" "直す" >}}' in cleaned and "1つだけ" not in cleaned
    assert "{{< ask >}}" not in cleaned and "閉じ忘れ" in cleaned
    assert "{{< tanerisu >}}こんにちは{{< /tanerisu >}}" in cleaned
    assert cleaned.count("{{< /term >}}") == 1
    assert "youtube" not in cleaned and "notice" not in cleaned and "注意" in cleaned


def test_paper_parts_ask_for_components_and_see_earlier_parts(tmp_path):
    llm = RecordingLLM()
    paper_agent(tmp_path, llm=llm).run_once()
    writes = [u for s, u in llm.calls if "TASK: write_section" in s]
    assert len(writes) == 10
    targets = {re.search(r"# 今回書くパート\n(.+)", u).group(1): int(re.search(r"# 文字数\n(\d+)", u).group(1))
               for u in writes}
    assert targets["しくみをもう少しくわしく"] > targets["何をしたの？"] > targets["用語ミニ辞典"]
    by_part = {re.search(r"# 今回書くパート\n(.+)", u).group(1): u for u in writes}
    assert "{{< flow" in by_part["何をしたの？"] and "{{< stat " in by_part["何がすごいの？"]
    assert "{{< term" in by_part["用語ミニ辞典"] and "{{< summary >}}" in by_part["ひとことで言うと"]
    assert "{{< flip" in by_part["なぜこの研究が必要？"] and "{{< bars" in by_part["何がすごいの？"]
    assert "{{< quiz" in by_part["クイズでチェック"]
    assert "ここまでに書いた内容" not in writes[0]
    assert "## ひとことで言うと" in by_part["何をしたの？"]  # earlier parts are shown to avoid repeats
    assert any("TASK: review_article" in s and "論文解説の記事" in s for s, u in llm.calls)


NEWS_RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>news</title>
<item><title>Physicists build a longer-lived qubit</title><link>https://news.example/1</link>
<description>See https://arxiv.org/abs/2501.00009 for the paper.</description></item>
</channel></rss>"""


def days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


class HotFetch(ArxivFetch):
    def __call__(self, url: str) -> bytes:
        self.urls.append(url)
        if url.startswith("https://news.example"):
            return NEWS_RSS
        if "search_query=" in url:
            return arxiv_feed(
                arxiv_entry("2501.00001", "A small tweak", "We prove a bound.", published=days_ago(8)),
                arxiv_entry("2501.00002", "Logical qubits on a big processor", "We demonstrate an experiment.",
                            extra="<arxiv:comment>Accepted in Nature</arxiv:comment>", authors=40,
                            published=days_ago(7)))
        if "id_list=2501.00009" in url:
            return arxiv_feed(arxiv_entry("2501.00009", "A longer-lived qubit", "We build a device.",
                                          published=days_ago(27)))
        return super().__call__(url)


def test_hot_recent_papers_are_ranked_before_the_llm_picks(tmp_path):
    llm = RecordingLLM()
    fetch = HotFetch()
    cfg_path = make_config(tmp_path, scout=False)
    text = cfg_path.read_text(encoding="utf-8").replace(
        "[strategies.content_publisher]\n",
        '[strategies.content_publisher]\nsource = "arxiv"\npapers = []\narxiv_query = "cat:quant-ph"\n'
        'recent_days = 14\nnews_feeds = ["https://news.example/rss"]\n')
    cfg_path.write_text(text, encoding="utf-8")
    agent = Agent(load_config(cfg_path), llm=llm, fetch=fetch)
    agent.run_once()

    search = next(u for u in fetch.urls if "search_query=" in u)
    assert "submittedDate" in urllib.parse.unquote_plus(search)
    pick = next(u for s, u in llm.calls if "TASK: pick_paper" in s)
    assert "Physicists build a longer-lived qubit" in pick  # news headlines give context
    order = re.findall(r"^ID: (\S+)", pick, flags=re.M)
    assert order == ["2501.00009", "2501.00002", "2501.00001"]  # news > venue + big team > plain
    assert "ニュース記事で紹介されている" in pick and "Accepted in Nature" in pick
    assert articles(agent)[0].payload["arxiv_id"] == "2501.00009"


class PickNothingLLM(MockLLM):
    def complete(self, system, user, *, json_mode=False, max_tokens=1500):
        if "TASK: pick_paper" in system:
            return LLMResponse('{"ids": []}')
        return super().complete(system, user, json_mode=json_mode, max_tokens=max_tokens)


def test_falls_back_to_the_most_talked_about_paper(tmp_path):
    agent = paper_agent(tmp_path, llm=PickNothingLLM(), fetch=HotFetch(), papers="[]")
    agent.run_once()
    assert articles(agent)[0].payload["arxiv_id"] == "2501.00002"


def test_ids_in_text_finds_arxiv_links():
    assert ids_in_text(b'<a href="https://arxiv.org/abs/2501.00009v2">x</a> arxiv.org/pdf/2501.00009 '
                       b"https://www.arxiv.org/html/2410.12345") == ["2501.00009", "2410.12345"]


def test_arxiv_requests_wait_and_retry_when_busy(monkeypatch):
    waits = []
    monkeypatch.setattr(arxiv, "_sleep", waits.append)
    replies = [429, 503, None]

    def busy_fetch(url):
        code = replies.pop(0)
        if code:
            headers = {"Retry-After": "7"} if code == 503 else {}
            raise urllib.error.HTTPError(url, code, "busy", headers, None)
        return arxiv_feed(LISTED)

    assert [p.id for p in fetch_papers(busy_fetch, ["2408.13687"])] == ["2408.13687"]
    assert waits == [5, 7]

    def missing(url):
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    with pytest.raises(urllib.error.HTTPError):
        fetch_papers(missing, ["2408.13687"])
    assert waits == [5, 7]  # other errors are not retried

    monkeypatch.setattr(arxiv, "RETRY_WAITS", (1,))
    with pytest.raises(urllib.error.HTTPError):
        fetch_papers(lambda url: (_ for _ in ()).throw(urllib.error.HTTPError(url, 429, "busy", {}, None)),
                     ["2408.13687"])


def test_clean_shortcodes_repairs_a_missing_closer_and_drops_stray_delimiters():
    text = '{{< flow "a" "b" }}\n本文 {{< と >}} の間'
    cleaned = clean_shortcodes(text)
    assert cleaned.startswith('{{< flow "a" "b" >}}')
    assert cleaned.count("{{<") == 1 and cleaned.count(">}}") == 1


def test_block_components_get_their_own_lines():
    cleaned = clean_shortcodes("文章です。{{< tanerisu >}}やあ{{< /tanerisu >}}続き{{< ask >}}なぜ？{{< /ask >}}")
    assert cleaned == "文章です。\n\n{{< tanerisu >}}やあ{{< /tanerisu >}}\n\n続き\n\n{{< ask >}}なぜ？{{< /ask >}}"


def test_ask_keeps_only_the_moods_the_reader_character_has():
    assert clean_shortcodes('{{< ask mood="surprised" >}}えっ{{< /ask >}}') == '{{< ask mood="surprised" >}}えっ{{< /ask >}}'
    assert clean_shortcodes('{{< ask mood="angry" >}}えっ{{< /ask >}}') == "{{< ask >}}えっ{{< /ask >}}"
    assert clean_shortcodes('{{< tanerisu mood="surprised" >}}やあ{{< /tanerisu >}}') == "{{< tanerisu >}}やあ{{< /tanerisu >}}"


def test_plain_math_turns_latex_numbers_into_text():
    assert plain_math(r"誤り率は$(6.4\pm0.2)\times10^{-3}$です") == "誤り率は(6.4±0.2)×10⁻³です"
    assert plain_math(r"$XY(\pi/2)$ゲート、H$_2$O、10^{-4}") == "XY(π/2)ゲート、H₂O、10⁻⁴"
    assert plain_math("料金は1ドル、変数xは2") == "料金は1ドル、変数xは2"


def test_clean_shortcodes_checks_the_interactive_components():
    text = (
        '{{< flip real="配線が多すぎる" analogy="渋滞した交差点" >}}\n'
        '{{< flip real="片方だけ" >}}\n'
        '{{< bars unit="%" >}}{{< bar label="これまで" value="1,200" >}}{{< bar label="今回" value="約3" >}}{{< /bars >}}\n'
        '{{< quiz q="何を減らした？" a="制御ラインの数です。" >}}{{< quiz q="答えなし" >}}'
    )
    cleaned = clean_shortcodes(text)
    assert '{{< flip real="配線が多すぎる" analogy="渋滞した交差点" >}}' in cleaned and "片方だけ" not in cleaned
    assert '{{< bar label="これまで" value="1200" >}}' in cleaned and "約3" not in cleaned
    assert '{{< bars unit="%" >}}' in cleaned and "{{< /bars >}}" in cleaned
    assert cleaned.count("{{< quiz") == 1
