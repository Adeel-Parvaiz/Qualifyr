"""Press/RSS signals: a company's own blog or press-room feed often announces the exact
events that open a buying window - a product launch, a funding round, a partnership, a
new-market expansion. Tries a handful of common feed paths on the company's own site;
most sites have none, and a miss is silent. Parsed with the stdlib (no feedparser
dependency) since RSS 2.0 and Atom both reduce to <item>/<entry> elements with a title,
a link and a date."""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from gtm_engine.config.schema import DefaultRules
from gtm_engine.scraping.fetcher import Fetcher

# Kept short: a site with no feed pays for every miss, and these four cover the
# overwhelming majority of WordPress, Ghost and hand-rolled blogs. The other six from the
# original list (/feed/, /rss, /blog/rss.xml, /news/rss, /press/rss, /index.xml) matched
# almost nothing extra in practice and cost ~20s of robots-gated requests per site for it.
FEED_PATHS = ["/feed", "/rss.xml", "/blog/feed", "/atom.xml"]

_TAG_RE = re.compile(r"\{[^}]*\}")


def _local(tag: str) -> str:
    return _TAG_RE.sub("", tag)


@dataclass
class PressMention:
    title: str
    url: str
    date: str | None
    kind: str


def _parse_entries(xml_text: str) -> list[tuple[str, str, str | None]]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out: list[tuple[str, str, str | None]] = []
    for el in root.iter():
        if _local(el.tag) not in ("item", "entry"):
            continue
        title = link = date = None
        for child in el:
            tag = _local(child.tag)
            if tag == "title":
                title = (child.text or "").strip()
            elif tag == "link":
                link = child.get("href") or (child.text or "").strip()
            elif tag in ("pubDate", "published", "updated"):
                date = (child.text or "").strip()
        if title:
            out.append((title, link or "", date))
    return out[:20]


def _classify(title: str, keywords: dict[str, list[str]]) -> str | None:
    low = title.lower()
    for kind, phrases in keywords.items():
        if any(p in low for p in phrases):
            return kind
    return None


async def press_mentions(fetcher: Fetcher, base_url: str | None, defaults: DefaultRules) -> list[PressMention]:
    if not base_url:
        return []
    base = base_url.rstrip("/")
    for path in FEED_PATHS:
        # No api=True here: this is a path on the prospect's own site, not a programmatic
        # endpoint, so it is subject to the same respect_robots policy as the crawl itself.
        result = await fetcher.get(base + path)
        if not result.ok or "<" not in (result.text or ""):
            continue
        entries = _parse_entries(result.text)
        if not entries:
            continue
        out: list[PressMention] = []
        for title, link, date in entries:
            kind = _classify(title, defaults.press_signal_keywords)
            if kind:
                out.append(PressMention(title=title[:160], url=link, date=date, kind=kind))
        return out[:5]
    return []
