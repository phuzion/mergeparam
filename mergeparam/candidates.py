"""Pick random articles that still have |alma_mater= in a covered infobox."""

from __future__ import annotations

from dataclasses import dataclass

from . import config, replica
from .infobox import Target, bare, find_target, normalize_title
from .wiki import Page, Wiki, protection_rank

BATCH = 10

# Which protected articles the editor can edit, chosen on the settings page.
# Wikipedia doesn't tell us (the tool has no login), so the editor says, and
# protected articles above that level are skipped.
ACCESS_LEVELS = {
    "any": "Any account, including new and temporary accounts",
    "autoconfirmed": "Autoconfirmed",
    "extendedconfirmed": "Extended confirmed",
    "templateeditor": "Template editor",
    "sysop": "Administrator",
}
DEFAULT_ACCESS = "autoconfirmed"

# Which kinds of fix to show, chosen on the settings page.
KINDS = {
    "all": "All articles",
    "merge": "Only articles where both parameters need combining",
    "rename": "Only simple renames",
}
DEFAULT_KIND = "all"


@dataclass
class Filters:
    category: str = ""      # without the "Category:" prefix
    subcats: bool = False
    project: str = ""       # without the "WikiProject " prefix

    @classmethod
    def from_args(cls, args) -> "Filters":
        category = normalize_title(args.get("category", ""))
        if category.lower().startswith("category:"):
            category = normalize_title(category[len("category:"):])
        project = normalize_title(args.get("project", ""))
        if project.lower().startswith("wikiproject "):
            project = normalize_title(project[len("wikiproject "):])
        return cls(category, bool(args.get("subcats")) and bool(category), project)

    def as_args(self) -> dict:
        args = {}
        if self.category:
            args["category"] = self.category
            if self.subcats:
                args["subcats"] = "1"
        if self.project:
            args["project"] = self.project
        return args

    def __bool__(self):
        return bool(self.category or self.project)


class FilterError(ValueError):
    pass


def valid_access(value: str | None) -> str:
    return value if value in ACCESS_LEVELS else DEFAULT_ACCESS


def valid_kind(value: str | None) -> str:
    return value if value in KINDS else DEFAULT_KIND


def is_rename(target: Target) -> bool:
    """Only |alma_mater= is filled in, so the fix is a straight rename."""
    return not (target.education and bare(target.education))


def wanted(target: Target, kind: str) -> bool:
    if kind == "merge":
        return not is_rename(target)
    if kind == "rename":
        return is_rename(target)
    return True


def can_edit(page: Page, access: str) -> bool:
    return protection_rank(page.edit_protection) <= protection_rank(
        None if access == "any" else access)


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def search_query(filters: Filters | None = None) -> str:
    cats = "|".join(c.replace(" ", "_") for c in config.TRACKING_CATEGORIES.values())
    query = f'insource:"alma_mater" incategory:{cats}'
    if filters and filters.category:
        keyword = "deepcategory" if filters.subcats else "incategory"
        query += f" {keyword}:{_quote(filters.category)}"
    return query


def random_titles(wiki: Wiki, filters: Filters, limit: int = 30) -> list[str]:
    if filters.project:
        if filters.subcats:
            raise FilterError("Subcategories can't be combined with a WikiProject filter. "
                              "Untick “include subcategories” or clear the WikiProject.")
        return replica.random_titles(filters.category, filters.project, limit)
    return wiki.search(search_query(filters), limit=limit)[0]


def find_next(wiki: Wiki, filters: Filters, access: str = DEFAULT_ACCESS,
              exclude: set[str] = frozenset(), kind: str = DEFAULT_KIND) -> tuple[Page, Target] | None:
    """A random page that needs fixing and that the tool can fix."""
    titles = [t for t in random_titles(wiki, filters) if t not in exclude]
    for i in range(0, len(titles), BATCH):
        for page in wiki.latest(titles[i:i + BATCH]):
            if not can_edit(page, access):
                continue
            target = find_target(page.text)
            # Check the kind before probing the template, to save a request.
            if target and wanted(target, kind) and wiki.supports_param(target.template, "education"):
                return page, target
    return None


def check_page(wiki: Wiki, page: Page) -> Target | None:
    target = find_target(page.text)
    if target and wiki.supports_param(target.template, "education"):
        return target
    return None
