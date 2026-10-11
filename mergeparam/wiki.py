"""Read-only access to the English Wikipedia API.

Follows https://www.mediawiki.org/wiki/API:Etiquette: one request at a time,
an identifying User-Agent, maxlag, and backing off when asked to.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime

import requests

from . import config
from .infobox import normalize_title

PROBE = "MergeparamProbeValue"

# English Wikipedia's edit protection levels, lowest first, with the wording
# Wikipedia uses for them. An unknown level is treated as the strictest.
PROTECTION_LEVELS = {
    "autoconfirmed": "semi-protected",
    "extendedconfirmed": "extended-confirmed protected",
    "templateeditor": "template-protected",
    "sysop": "fully protected",
}


def protection_rank(level: str | None) -> int:
    if not level:
        return 0
    order = list(PROTECTION_LEVELS)
    return order.index(level) + 1 if level in order else len(order) + 1


class WikiError(RuntimeError):
    pass


class WikiBusy(WikiError):
    """Wikipedia asked us to slow down."""


@dataclass
class Page:
    title: str
    pageid: int
    revid: int
    timestamp: str        # ISO 8601, as returned by the API
    text: str
    protection: list[dict]

    @property
    def edit_protection(self) -> str | None:
        """The strictest edit protection (direct or cascading), if any."""
        levels = [p["level"] for p in self.protection if p.get("type") == "edit"]
        return max(levels, key=protection_rank) if levels else None

    @property
    def mw_timestamp(self) -> str:
        """The timestamp in MediaWiki's YYYYMMDDHHMMSS form."""
        return datetime.strptime(self.timestamp, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y%m%d%H%M%S")


class _TTLCache:
    def __init__(self, ttl: float):
        self.ttl = ttl
        self.data: dict = {}

    def get(self, key):
        hit = self.data.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        return None

    def set(self, key, value):
        self.data[key] = (time.monotonic() + self.ttl, value)


class Wiki:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers["User-Agent"] = config.USER_AGENT
        self.lock = threading.Lock()  # serialize requests from this process
        self.probe_cache = _TTLCache(6 * 3600)
        self.stats_cache = _TTLCache(10 * 60)

    # ------------------------------------------------------------ transport

    def call(self, params: dict, post: bool = False, retries: int = 3) -> dict:
        params = {"format": "json", "formatversion": "2", "maxlag": "5", **params}
        for attempt in range(retries + 1):
            with self.lock:
                if post:
                    r = self.session.post(config.API, data=params, timeout=30)
                else:
                    r = self.session.get(config.API, params=params, timeout=30)
            wait = r.headers.get("Retry-After")
            if r.status_code == 429:
                raise WikiBusy("Wikipedia is rate-limiting requests. Please try again in a minute.")
            r.raise_for_status()
            data = r.json()
            error = data.get("error")
            if error and error.get("code") == "maxlag" and attempt < retries:
                time.sleep(min(int(wait or 5), 10))
                continue
            if error:
                if error.get("code") == "maxlag":
                    raise WikiBusy("Wikipedia's servers are busy. Please try again in a minute.")
                raise WikiError(f"{error.get('code')}: {error.get('info')}")
            return data
        raise WikiBusy("Wikipedia's servers are busy. Please try again in a minute.")

    # ---------------------------------------------------------------- pages

    def _pages(self, data: dict) -> list[Page]:
        pages = []
        for p in data.get("query", {}).get("pages", []):
            if p.get("missing") or p.get("invalid") or not p.get("revisions"):
                continue
            rev = p["revisions"][0]
            pages.append(Page(
                title=p["title"],
                pageid=p["pageid"],
                revid=rev["revid"],
                timestamp=rev["timestamp"],
                text=rev["slots"]["main"].get("content", ""),
                protection=p.get("protection", []),
            ))
        return pages

    def latest(self, titles: list[str]) -> list[Page]:
        """Current revisions of up to 50 pages, in the order given."""
        if not titles:
            return []
        data = self.call({
            "action": "query", "titles": "|".join(titles),
            "prop": "revisions|info", "rvprop": "ids|timestamp|content", "rvslots": "main",
            "inprop": "protection",
        }, post=True)
        by_title = {p.title: p for p in self._pages(data)}
        normalized = {n["from"]: n["to"] for n in data.get("query", {}).get("normalized", [])}
        return [by_title[t] for t in (normalized.get(t, t) for t in titles) if t in by_title]

    def revision(self, revid: int) -> Page | None:
        data = self.call({
            "action": "query", "revids": str(revid),
            "prop": "revisions|info", "rvprop": "ids|timestamp|content", "rvslots": "main",
            "inprop": "protection",
        })
        pages = self._pages(data)
        return pages[0] if pages else None

    def resolve_redirects(self, titles: list[str]) -> dict[str, str]:
        """Map each title that is a redirect to its (normalized) target."""
        result: dict[str, str] = {}
        titles = sorted({t for t in titles if t})
        for i in range(0, len(titles), 50):
            data = self.call({"action": "query", "titles": "|".join(titles[i:i + 50]),
                              "redirects": "1"}, post=True)
            query = data.get("query", {})
            normalized = {n["to"]: n["from"] for n in query.get("normalized", [])}
            for r in query.get("redirects", []):
                source = normalize_title(normalized.get(r["from"], r["from"]))
                result[source] = normalize_title(r["to"])
        return result

    # ------------------------------------------------------------- search

    def search(self, query: str, limit: int = 20, random: bool = True) -> tuple[list[str], int]:
        params = {"action": "query", "list": "search", "srsearch": query,
                  "srlimit": str(limit), "srprop": "", "srinfo": "totalhits", "srnamespace": "0"}
        if random:
            params["srsort"] = "random"
        data = self.call(params)
        query_data = data.get("query", {})
        titles = [r["title"] for r in query_data.get("search", [])]
        return titles, query_data.get("searchinfo", {}).get("totalhits", 0)

    def search_total(self, query: str) -> int:
        cached = self.stats_cache.get(("total", query))
        if cached is None:
            cached = self.search(query, limit=1, random=False)[1]
            self.stats_cache.set(("total", query), cached)
        return cached

    def category_sizes(self, categories: list[str]) -> dict[str, int]:
        key = ("sizes", tuple(categories))
        cached = self.stats_cache.get(key)
        if cached is None:
            data = self.call({"action": "query", "prop": "categoryinfo",
                              "titles": "|".join("Category:" + c for c in categories)})
            cached = {p["title"].split(":", 1)[1]: p.get("categoryinfo", {}).get("pages", 0)
                      for p in data["query"]["pages"]}
            self.stats_cache.set(key, cached)
        return cached

    def category_exists(self, category: str) -> bool:
        data = self.call({"action": "query", "titles": "Category:" + category})
        pages = data.get("query", {}).get("pages", [])
        return bool(pages) and not pages[0].get("missing") and not pages[0].get("invalid")

    # ------------------------------------------------------------ rendering

    def supports_param(self, template: str, param: str) -> bool:
        """Does the template actually display `param`?

        Rendering it is the only reliable test: wrappers may or may not pass a
        parameter through to the infobox they wrap.
        """
        key = (template, param)
        cached = self.probe_cache.get(key)
        if cached is None:
            data = self.call({
                "action": "parse", "contentmodel": "wikitext", "prop": "text",
                "title": "Mergeparam probe", "disablelimitreport": "1",
                "text": "{{" + template + "|name=Probe|" + param + "=" + PROBE + "}}",
            }, post=True)
            cached = PROBE in data.get("parse", {}).get("text", "")
            self.probe_cache.set(key, cached)
        return cached

    def lead_html(self, revid: int) -> str:
        data = self.call({
            "action": "parse", "oldid": str(revid), "prop": "text", "section": "0",
            "disableeditsection": "1", "disablelimitreport": "1",
        })
        return data.get("parse", {}).get("text", "")
