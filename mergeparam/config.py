import os

VERSION = "0.1.0"

WIKI = "https://en.wikipedia.org"
API = WIKI + "/w/api.php"
INDEX = WIKI + "/w/index.php"

TOOL_URL = "https://mergeparam.toolforge.org/"
MAINTAINER = "Phuzion"
# The Toolforge rules require a link to the published source.
SOURCE_URL = os.environ.get("MERGEPARAM_SOURCE_URL", "https://github.com/phuzion/mergeparam")

# https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy
USER_AGENT = f"mergeparam/{VERSION} ({TOOL_URL}; User:{MAINTAINER})"

# The infoboxes covered by version 1, and the tracking category each adds when
# |alma_mater= is set. These categories also collect other deprecated
# parameters, so every page is checked before it is shown.
TRACKING_CATEGORIES = {
    "Infobox person": "Pages using infobox person with deprecated parameters",
    "Infobox officeholder": "Pages using infobox officeholder with deprecated parameters",
    "Infobox academic": "Pages using infobox academic with deprecated parameters",
    "Infobox Christian leader": "Pages using infobox Christian leader with deprecated parameters",
}

RFC_PAGE = ("Wikipedia:Village pump (miscellaneous)/Archive 88"
            "#RFC: Alma mater vs Education in Infoboxes")
RFC_URL = ("https://en.wikipedia.org/wiki/Wikipedia:Village_pump_(miscellaneous)/Archive_88"
           "#RFC:_Alma_mater_vs_Education_in_Infoboxes")
RFC_CLOSED = "5 July 2026"
MOS_URL = "https://en.wikipedia.org/wiki/MOS:INFOEDU"

HASHTAG = "#mergeparam"
HASHTAGS_URL = "https://hashtags.wmcloud.org/?query=mergeparam&project=en.wikipedia.org"

# Wiki replicas, only needed for the WikiProject filter.
# https://wikitech.wikimedia.org/wiki/Help:Toolforge/Database
REPLICA_HOST = os.environ.get("MERGEPARAM_REPLICA_HOST", "enwiki.web.db.svc.wikimedia.cloud")
REPLICA_PORT = int(os.environ.get("MERGEPARAM_REPLICA_PORT", "3306"))
REPLICA_DB = "enwiki_p"
REPLICA_CNF = os.environ.get("MERGEPARAM_REPLICA_CNF", os.path.expanduser("~/replica.my.cnf"))
