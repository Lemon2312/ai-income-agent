from __future__ import annotations

import html
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Callable

USER_AGENT = "ai-income-agent/0.1 (+https://github.com/)"

Fetcher = Callable[[str], bytes]


@dataclass
class FeedItem:
    id: str
    title: str
    link: str
    summary: str
    published: str = ""


def http_fetch(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def strip_html(text: str) -> str:
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text or "", flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</li>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child_text(el: ET.Element, *names: str) -> str:
    for child in el:
        if _local(child.tag) in names and child.text:
            return child.text.strip()
    return ""


def parse_feed(data: bytes) -> list[FeedItem]:
    """Parses RSS 2.0, RSS 1.0 (RDF) and Atom feeds."""
    root = ET.fromstring(data)
    items: list[FeedItem] = []
    for el in root.iter():
        name = _local(el.tag)
        if name == "item":
            link = _child_text(el, "link")
            title = _child_text(el, "title")
            summary = _child_text(el, "description", "encoded", "summary")
            guid = _child_text(el, "guid") or link or title
            published = _child_text(el, "pubDate", "date")
        elif name == "entry":
            link = ""
            for child in el:
                if _local(child.tag) == "link" and child.get("rel", "alternate") == "alternate":
                    link = child.get("href", "")
                    break
            title = _child_text(el, "title")
            summary = _child_text(el, "summary", "content")
            guid = _child_text(el, "id") or link or title
            published = _child_text(el, "updated", "published")
        else:
            continue
        if not guid:
            continue
        items.append(FeedItem(id=guid, title=strip_html(title), link=link,
                              summary=strip_html(summary), published=published))
    return items
