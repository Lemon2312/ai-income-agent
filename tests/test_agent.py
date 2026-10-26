from __future__ import annotations

import re
import subprocess
from importlib import resources
from pathlib import Path

import pytest

from income_agent.agent import Agent
from income_agent.cli import main
from income_agent.config import load_config
from income_agent.feeds import parse_feed
from income_agent.llm import LLMResponse, MockLLM, parse_json
from income_agent.site import init_site
from income_agent.storage import DONE, PENDING, REJECTED
from income_agent.strategies.content_publisher import insert_affiliate_links
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


def test_repo_agent_config_has_study_guidelines():
    cfg = load_config(Path(__file__).resolve().parent.parent / "agent.toml")
    publisher = cfg.strategies["content_publisher"]
    assert "理系大学生" in publisher["niche"]
    assert any("不正行為" in g for g in publisher["guidelines"])
    assert any("アニメーション" in t for t in publisher["author_tips"])


def test_author_tips_are_offered_as_optional_context(tmp_path):
    cfg_path = make_config(tmp_path, scout=False)
    text = cfg_path.read_text(encoding="utf-8").replace(
        "[strategies.content_publisher]\n",
        '[strategies.content_publisher]\nauthor_tips = ["別タブで聞く"]\n', 1)
    text = re.sub(r'^author = .*$', 'author = "LK"', text, count=1, flags=re.M)
    cfg_path.write_text(text, encoding="utf-8")
    llm = RecordingLLM()
    Agent(load_config(cfg_path), llm=llm, fetch=lambda url: RSS).run_once()
    section_prompts = [u for s, u in llm.calls if "TASK: write_section" in s]
    assert section_prompts and all("別タブで聞く" in u for u in section_prompts)
    assert "LKが実際にやっている方法" in section_prompts[0]
    assert "無関係なら使わない" in section_prompts[0]


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
