"""Paper lookup on arXiv (https://info.arxiv.org/help/api/) for article sources."""
from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from income_agent.feeds import Fetcher, strip_html

API_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"


@dataclass
class Paper:
    id: str  # without the version suffix, e.g. "2408.13687" or "quant-ph/9508027"
    title: str
    abstract: str
    authors: list[str] = field(default_factory=list)
    published: str = ""  # YYYY-MM-DD

    @property
    def url(self) -> str:
        return f"https://arxiv.org/abs/{self.id}"

    @property
    def html_url(self) -> str:
        return f"https://arxiv.org/html/{self.id}"


def normalize_id(value: str) -> str:
    """Accepts "2408.13687", "arXiv:2408.13687v2" or an abs/pdf URL."""
    value = value.strip()
    value = re.sub(r"^(?:https?://)?(?:www\.|export\.)?arxiv\.org/(?:abs|pdf|html)/", "", value)
    value = re.sub(r"^arxiv:", "", value, flags=re.I)
    value = re.sub(r"\.pdf$", "", value)
    return re.sub(r"v\d+$", "", value)


def _text(el: ET.Element, tag: str) -> str:
    child = el.find(ATOM + tag)
    return " ".join((child.text or "").split()) if child is not None else ""


def parse_papers(data: bytes) -> list[Paper]:
    papers = []
    for entry in ET.fromstring(data).iter(ATOM + "entry"):
        raw_id = _text(entry, "id")
        # Unknown ids come back as an entry pointing at arxiv.org/api/errors.
        if "/abs/" not in raw_id:
            continue
        papers.append(Paper(
            id=normalize_id(raw_id),
            title=_text(entry, "title"),
            abstract=_text(entry, "summary"),
            authors=[_text(a, "name") for a in entry.findall(ATOM + "author")],
            published=_text(entry, "published")[:10],
        ))
    return papers


def fetch_papers(fetch: Fetcher, ids: list[str]) -> list[Paper]:
    """Papers for the given ids, in the same order; unknown ids are left out."""
    if not ids:
        return []
    ids = [normalize_id(i) for i in ids]
    query = urllib.parse.urlencode({"id_list": ",".join(ids), "max_results": len(ids)})
    found = {p.id: p for p in parse_papers(fetch(f"{API_URL}?{query}"))}
    return [found[i] for i in ids if i in found]


def search_papers(fetch: Fetcher, query: str, max_results: int = 40) -> list[Paper]:
    """Newest papers matching an arXiv search query, newest first."""
    params = urllib.parse.urlencode({"search_query": query, "sortBy": "submittedDate",
                                     "sortOrder": "descending", "max_results": max_results})
    return parse_papers(fetch(f"{API_URL}?{params}"))


def fetch_full_text(fetch: Fetcher, paper: Paper, limit: int = 12000) -> str:
    """Plain text of the paper's HTML version, cut down to `limit` characters by
    keeping the beginning (introduction) and the end of the body (conclusion).
    Not every paper has an HTML version, so this returns "" on any failure."""
    try:
        page = fetch(paper.html_url).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - the abstract alone is still usable
        return ""
    # arXiv's HTML (LaTeXML) puts the reference list in a "ltx_bibliography" section.
    refs = re.search(r"<section[^>]*ltx_bibliography|<h2[^>]*>\s*(?:References|Bibliography)\s*</h2>", page, re.I)
    text = strip_html(page[:refs.start()] if refs else page)
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    if len(text) <= limit:
        return text
    head = limit * 2 // 3
    return text[:head] + "\n…（中略）…\n" + text[-(limit - head):]
