from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

import pytest

from income_agent.agent import Agent
from income_agent.cli import main
from income_agent.config import load_config
from income_agent.feeds import parse_feed
from income_agent.llm import LLMResponse, MockLLM, parse_json
from income_agent.storage import DONE, PENDING, REJECTED
from income_agent.strategies.content_publisher import insert_affiliate_links

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


def make_config(tmp_path: Path, **content_overrides) -> Path:
    template = resources.files("income_agent").joinpath("config.example.toml")
    with resources.as_file(template) as src:
        text = Path(src).read_text(encoding="utf-8")
    text = text.replace('provider = "auto"', 'provider = "mock"')
    for key, value in content_overrides.items():
        text = text.replace(f"{key} = true", f"{key} = {value}")
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def make_agent(tmp_path: Path, llm=None, **overrides) -> Agent:
    cfg = load_config(make_config(tmp_path, **overrides))
    return Agent(cfg, llm=llm or MockLLM(), fetch=lambda url: RSS)


def test_parse_rss_and_atom():
    items = parse_feed(RSS)
    assert [i.id for i in items] == ["job-1", "job-2", "job-3"]
    assert items[0].summary == "Need a Python developer"
    atom = parse_feed(ATOM)
    assert atom[0].link == "https://a.example/x" and atom[0].title == "Atom job"


def test_parse_json_handles_fences():
    assert parse_json('```json\n{"score": 0.5}\n```') == {"score": 0.5}
    assert parse_json('sure: {"a": 1} done') == {"a": 1}


def test_full_cycle_queues_proposals_and_articles(tmp_path):
    agent = make_agent(tmp_path)
    result = agent.run_once()
    assert not result.errors

    # job-1 matches keywords; job-2 has no keyword; job-3 hits exclude ("unpaid"). Both feeds
    # return the same items so the second feed must be deduplicated.
    proposals = [a for a in agent.store.list_actions(PENDING) if a.kind == "proposal"]
    assert [p.title for p in proposals] == ["Python automation script for invoices"]
    assert "[mock]" in proposals[0].payload["proposal"]

    articles = [a for a in agent.store.list_actions(PENDING) if a.kind == "article"]
    assert len(articles) == 1
    draft = Path(articles[0].payload["draft_path"])
    assert draft.read_text(encoding="utf-8").startswith("---\ntitle:")

    # A second cycle must not re-evaluate seen jobs or repeat article titles.
    agent.run_once()
    assert len([a for a in agent.store.list_actions(PENDING) if a.kind == "proposal"]) == 1
    titles = [a.title for a in agent.store.list_actions(PENDING) if a.kind == "article"]
    assert len(titles) == 2 and len(set(titles)) == 2


def test_approve_article_publishes_and_reject(tmp_path):
    agent = make_agent(tmp_path)
    agent.run_once()
    article = next(a for a in agent.store.list_actions(PENDING) if a.kind == "article")
    proposal = next(a for a in agent.store.list_actions(PENDING) if a.kind == "proposal")

    msg = agent.approve(article.id)
    published = tmp_path / "site" / "content" / "posts" / Path(article.payload["draft_path"]).name
    assert published.exists(), msg
    assert agent.store.get_action(article.id).status == DONE

    agent.reject(proposal.id)
    assert agent.store.get_action(proposal.id).status == REJECTED
    with pytest.raises(ValueError):
        agent.approve(proposal.id)


def test_auto_publish_when_approval_disabled(tmp_path):
    agent = make_agent(tmp_path, require_approval="false")
    agent.run_once()
    assert not [a for a in agent.store.list_actions(PENDING) if a.kind == "article"]
    assert list((tmp_path / "site" / "content" / "posts").glob("*.md"))


def test_keyword_matching_uses_word_boundaries():
    from income_agent.strategies.freelance_scout import _contains
    assert _contains("build an ai agent", "ai")
    assert not _contains("maintain the platform", "ai")
    assert _contains("業務の自動化をお願いします", "自動化")


def test_affiliate_links_and_disclosure():
    md = "# Notion入門\n\nNotionは便利です。Notionで管理。\n\n## Zapier\n\nZapierも。"
    out, n = insert_affiliate_links(md, [
        {"keyword": "Notion", "url": "https://aff/n"},
        {"keyword": "Zapier", "url": "https://aff/z"},
    ])
    assert n == 2
    assert "# Notion入門" in out  # headings untouched
    assert "[Notion](https://aff/n)は便利です。Notionで管理。" in out
    assert "## Zapier\n\n[Zapier](https://aff/z)も。" in out


class PricedLLM(MockLLM):
    model = "priced"

    def complete(self, system, user, *, json_mode=False, max_tokens=1500):
        resp = super().complete(system, user, json_mode=json_mode, max_tokens=max_tokens)
        return LLMResponse(resp.text, input_tokens=1_000_000, output_tokens=0)


def test_budget_stops_cycle_and_costs_are_recorded(tmp_path):
    agent = make_agent(tmp_path, llm=PricedLLM())
    agent.config.agent.daily_llm_budget_usd = 0.2  # each call costs $0.15
    result = agent.run_once()
    assert result.budget_exhausted
    assert agent.llm.spent_today_usd() == pytest.approx(0.30)
    assert agent.store.total("cost") == pytest.approx(0.30 * 150)


def test_cli_end_to_end(tmp_path, capsys):
    cfg = make_config(tmp_path)
    shutil.copy(cfg, tmp_path / "c.toml")
    c = str(tmp_path / "c.toml")
    # The CLI uses the real HTTP fetcher; disable the scout so the test stays offline.
    text = Path(c).read_text(encoding="utf-8").replace(
        "[strategies.freelance_scout]\nenabled = true", "[strategies.freelance_scout]\nenabled = false")
    Path(c).write_text(text, encoding="utf-8")

    assert main(["-c", c, "run", "--once"]) == 0
    assert main(["-c", c, "queue"]) == 0
    assert "content_publisher" in capsys.readouterr().out
    assert main(["-c", c, "approve", "1"]) == 0
    assert main(["-c", c, "revenue", "add", "12000", "--strategy", "content_publisher"]) == 0
    assert main(["-c", c, "report"]) == 0
    out = capsys.readouterr().out
    assert "12,000" in out


def test_init_creates_config(tmp_path):
    dest = tmp_path / "config.toml"
    assert main(["-c", str(dest), "init"]) == 0
    assert "[strategies.freelance_scout]" in dest.read_text(encoding="utf-8")
    assert main(["-c", str(dest), "init"]) == 1
