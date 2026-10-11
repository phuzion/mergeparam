"""Turn Wikipedia's rendered lead section into a small, safe context preview."""

from __future__ import annotations

from bs4 import BeautifulSoup

from . import config

DROP_TAGS = ["script", "noscript", "iframe", "object", "embed", "form", "input",
             "button", "link", "meta"]
URL_ATTRS = ["href", "src"]


def _absolute(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("/"):
        return config.WIKI + url
    if url.startswith("#"):
        return url
    if url.startswith(("https://", "http://")):
        return url
    return ""  # javascript:, data: and anything else we don't expect


def context(html: str) -> dict[str, str]:
    """Return {"infobox": html, "lead": html} for the first infobox and paragraph."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all(DROP_TAGS):
        tag.decompose()
    for tag in soup.find_all(True):
        for attr in list(tag.attrs):
            if attr.lower().startswith("on"):
                del tag[attr]
        for attr in URL_ATTRS:
            if tag.has_attr(attr):
                tag[attr] = _absolute(tag[attr])
        if tag.has_attr("srcset"):
            tag["srcset"] = ", ".join(_absolute(part.strip()) for part in tag["srcset"].split(","))
        if tag.name == "a" and tag.get("href", "").startswith("http"):
            tag["target"] = "_blank"
            tag["rel"] = "noopener"

    infobox = soup.find("table", class_="infobox")
    lead = next((p for p in soup.find_all("p", recursive=True)
                 if p.get_text(strip=True) and "mw-empty-elt" not in (p.get("class") or [])
                 and not p.find_parent("table")), None)
    # TemplateStyles: keep the <style> blocks so the infobox looks as it does on-wiki.
    styles = "".join(str(s) for s in soup.find_all("style"))
    return {
        "infobox": styles + str(infobox) if infobox else "",
        "lead": str(lead) if lead else "",
    }
