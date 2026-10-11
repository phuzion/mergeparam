"""Build the form that opens Wikipedia's own "Show changes" screen.

The tool never saves anything. It posts the proposed text to index.php with
wpDiff, so Wikipedia shows its normal diff and edit form. The editor reviews
it and decides whether to publish, using their own Wikipedia session.

Passing the base revision (editRevId, wpEdittime) means Wikipedia reports an
edit conflict if the article changed after it was loaded here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlencode

from . import config
from .wiki import Page

SUMMARIES = {
    "rename": "Renamed deprecated |alma_mater= to |education=",
    "merge": "Merged deprecated |alma_mater= into |education=",
    "drop": "Removed deprecated |alma_mater= (already covered by |education=)",
}


def summary(action: str) -> str:
    return (f"{SUMMARIES[action]} per [[{config.RFC_PAGE}|RfC]] "
            f"using [[toolforge:mergeparam|mergeparam]] {config.HASHTAG}")


def edit_form(page: Page, new_text: str, action: str,
              now: datetime | None = None) -> tuple[str, dict[str, str]]:
    now = now or datetime.now(timezone.utc)
    url = config.INDEX + "?" + urlencode({"title": page.title, "action": "submit"})
    fields = {
        "wpTextbox1": new_text,
        "wpSummary": summary(action),
        "wpDiff": "Show changes",
        "wpStarttime": now.strftime("%Y%m%d%H%M%S"),
        "wpEdittime": page.mw_timestamp,
        "editRevId": str(page.revid),
        "parentRevId": str(page.revid),
        "model": "wikitext",
        "format": "text/x-wiki",
        # MediaWiki treats a form without this as truncated and refuses it.
        "wpUltimateParam": "1",
    }
    # wpWatchthis is a checkbox on the page itself, so the editor chooses.
    return url, fields
