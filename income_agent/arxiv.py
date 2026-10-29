"""Paper lookup on arXiv (https://info.arxiv.org/help/api/) for article sources."""
from __future__ import annotations

import re
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from income_agent.feeds import Fetcher, strip_html

API_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
ID_IN_TEXT = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(\d{4}\.\d{4,5})")
# arXiv answers 429/503 when it is busy or a shared address (such as a GitHub Actions
# runner) has made many requests; it asks clients to wait and try again.
RETRY_WAITS = (5, 20, 60)
_sleep = time.sleep


@dataclass
class Paper:
    id: str  # without the version suffix, e.g. "2408.13687" or "quant-ph/9508027"
    title: str
    abstract: str
    authors: list[str] = field(default_factory=list)
    published: str = ""  # YYYY-MM-DD
    comment: str = ""  # authors' note, e.g. "Accepted in Nature"
    journal_ref: str = ""

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


def _text(el: ET.Element, tag: str, ns: str = ATOM) -> str:
    child = el.find(ns + tag)
    return " ".join((child.text or "").split()) if child is not None else ""


def _api_get(fetch: Fetcher, url: str) -> bytes:
    for wait in (*RETRY_WAITS, None):
        try:
            return fetch(url)
        except urllib.error.HTTPError as e:
            if e.code not in (429, 503) or wait is None:
                raise
            retry_after = (e.headers or {}).get("Retry-After", "")
            _sleep(min(int(retry_after), 120) if retry_after.isdigit() else wait)
    raise AssertionError("unreachable")


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
            comment=_text(entry, "comment", ARXIV),
            journal_ref=_text(entry, "journal_ref", ARXIV),
        ))
    return papers


def fetch_papers(fetch: Fetcher, ids: list[str]) -> list[Paper]:
    """Papers for the given ids, in the same order; unknown ids are left out."""
    if not ids:
        return []
    ids = [normalize_id(i) for i in ids]
    query = urllib.parse.urlencode({"id_list": ",".join(ids), "max_results": len(ids)})
    found = {p.id: p for p in parse_papers(_api_get(fetch, f"{API_URL}?{query}"))}
    return [found[i] for i in ids if i in found]


def search_papers(fetch: Fetcher, query: str, max_results: int = 40, days: int = 0) -> list[Paper]:
    """Newest papers matching an arXiv search query, newest first. With `days`,
    only papers submitted within that many days."""
    if days:
        now = datetime.now(timezone.utc)
        query = f"({query}) AND submittedDate:[{now - timedelta(days=days):%Y%m%d}0000 TO {now:%Y%m%d}2359]"
    params = urllib.parse.urlencode({"search_query": query, "sortBy": "submittedDate",
                                     "sortOrder": "descending", "max_results": max_results})
    return parse_papers(_api_get(fetch, f"{API_URL}?{params}"))


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


def ids_in_text(data: bytes | str) -> list[str]:
    """arXiv ids linked from a page or feed (e.g. a news article), in order of first mention."""
    text = data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data
    return list(dict.fromkeys(ID_IN_TEXT.findall(text)))


def days_old(paper: Paper, today: date | None = None) -> int:
    try:
        published = date.fromisoformat(paper.published)
    except ValueError:
        return 0
    return ((today or datetime.now(timezone.utc).date()) - published).days
