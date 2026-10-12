"""Find a deprecated |alma_mater= in an article's infobox and propose an |education= value.

Everything here is pure: wikitext in, wikitext out. Network access (redirect
resolution, template probing) is done by the caller and passed in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import mwparserfromhell
from mwparserfromhell.nodes import Comment, Tag, Template, Text
from mwparserfromhell.wikicode import Wikicode

OLD_PARAM = "alma_mater"
NEW_PARAM = "education"

# Redirect names taken from enwiki (prop=redirects), first letter uppercased.
PLAINLIST = {"Plainlist", "Flatlist"}
POSITIONAL_LISTS = {
    "Unbulleted list", "Ubl", "Unbullet", "Ubt", "Vunblist", "Ublist",
    "Unbulletedlist", "Unbulleted", "Nblist", "Nonbulleted list", "UBL",
    "Plainlist pipe", "Unbulleted list pipe", "UBl",
    "Bulleted list", "Ulist", "Blist", "Unordered list", "Bulleted", "BList",
    "Bulletlist", "Bullet list", "Bulletedlist", "Bulleted List",
}
DEFAULT_LIST_TEMPLATE = "Plainlist"
IN_USE_TEMPLATES = {"In use", "Inuse", "In-use", "Under construction", "GOCEinuse"}


def normalize_title(title: str) -> str:
    """Normalize a page or template title roughly the way MediaWiki does."""
    title = title.split("#", 1)[0]
    title = re.sub(r"[\s_]+", " ", title).strip().lstrip(":").strip()
    return title[:1].upper() + title[1:]


def template_name(tpl: Template) -> str:
    name = "".join(str(n) for n in tpl.name.nodes if not isinstance(n, Comment))
    name = normalize_title(name)
    if name.lower().startswith("template:"):
        name = normalize_title(name[len("template:"):])
    return name


def is_infobox(tpl: Template) -> bool:
    return template_name(tpl).lower().startswith("infobox")


def bare(code: Wikicode | str) -> str:
    """Wikitext with top-level comments removed, stripped."""
    if isinstance(code, str):
        code = mwparserfromhell.parse(code)
    return "".join(str(n) for n in code.nodes if not isinstance(n, Comment)).strip()


def _params_named(tpl: Template, name: str):
    return [p for p in tpl.params if p.name.strip() == name]


def _has_value(tpl: Template, name: str) -> bool:
    return tpl.has(name) and bool(bare(tpl.get(name).value))


def _candidates(code: Wikicode) -> list[Template]:
    return [
        t for t in code.filter_templates(recursive=True)
        if is_infobox(t) and _has_value(t, OLD_PARAM)
    ]


@dataclass
class Target:
    """An infobox invocation that has a non-empty |alma_mater=."""

    index: int                 # position among such infoboxes, in document order
    template: str              # normalized template name, e.g. "Infobox person"
    alma: str                  # current |alma_mater= value, stripped
    education: str | None      # current |education= value (None when absent)
    others: int = 0            # further infoboxes on the page that also need fixing
    repeated_alma: bool = False  # |alma_mater= given more than once in this infobox


def find_target(text: str) -> Target | None:
    code = mwparserfromhell.parse(text)
    found = _candidates(code)
    if not found:
        return None
    tpl = found[0]
    education = str(tpl.get(NEW_PARAM).value).strip() if tpl.has(NEW_PARAM) else None
    return Target(
        index=0,
        template=template_name(tpl),
        alma=str(tpl.get(OLD_PARAM).value).strip(),
        education=education,
        others=len(found) - 1,
        repeated_alma=len(_params_named(tpl, OLD_PARAM)) > 1,
    )


def count_targets(text: str) -> int:
    """How many infoboxes on the page still have a non-empty |alma_mater=."""
    return len(_candidates(mwparserfromhell.parse(text)))


def in_use_tagged(text: str) -> bool:
    code = mwparserfromhell.parse(text)
    return any(template_name(t) in IN_USE_TEMPLATES for t in code.filter_templates(recursive=False))


# ---------------------------------------------------------------- list parsing

@dataclass
class ValueList:
    """A parameter value seen as a list of entries, plus how it is formatted."""

    kind: str                   # empty | single | br | bullets | plainlist | positional
    items: list[str] = field(default_factory=list)
    code: Wikicode | None = None  # the (copied) value the items were read from
    container: Template | None = None  # list template inside `code`, if any
    br: str = "<br />"


def _significant(code: Wikicode):
    return [
        n for n in code.nodes
        if not isinstance(n, Comment) and not (isinstance(n, Text) and not str(n).strip())
    ]


def _star_items(text: str) -> list[str]:
    items = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("*"):
            item = line.lstrip("*").strip()
            if item:
                items.append(item)
        elif line and items:
            items[-1] += " " + line
    return items


def _is_br(node) -> bool:
    return isinstance(node, Tag) and str(node.tag).strip().lower() == "br"


def parse_list(value: str) -> ValueList:
    code = mwparserfromhell.parse(value)
    nodes = _significant(code)
    if not nodes:
        return ValueList("empty", [], code)

    if len(nodes) == 1 and isinstance(nodes[0], Template):
        tpl = nodes[0]
        name = template_name(tpl)
        if name in PLAINLIST and tpl.has("1"):
            items = _star_items(str(tpl.get("1").value))
            if items:
                return ValueList("plainlist", items, code, tpl)
        if name in POSITIONAL_LISTS:
            items = [str(p.value).strip() for p in tpl.params
                     if p.name.strip().isdigit() and str(p.value).strip()]
            if items:
                return ValueList("positional", items, code, tpl)

    brs = [n for n in code.nodes if _is_br(n)]
    if brs:
        items, current = [], []
        for node in code.nodes:
            if _is_br(node):
                items.append("".join(current).strip())
                current = []
            elif not isinstance(node, Comment):
                current.append(str(node))
        items.append("".join(current).strip())
        return ValueList("br", [i for i in items if i], code, br=str(brs[0]))

    text = bare(code)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if lines and all(l.startswith("*") for l in lines):
        return ValueList("bullets", _star_items(text), code)

    return ValueList("single", [str(code).strip()], code)


# ------------------------------------------------------------ duplicate checks

def link_targets(text: str) -> list[str]:
    code = mwparserfromhell.parse(text)
    return [normalize_title(str(l.title)) for l in code.filter_wikilinks(recursive=True)
            if str(l.title).strip() and not str(l.title).strip().startswith("#")]


def _plain(text: str) -> str:
    plain = mwparserfromhell.parse(text).strip_code(normalize=True, collapse=True)
    return re.sub(r"\s+", " ", plain).strip().lower()


def is_duplicate(item: str, existing: list[str], redirects: dict[str, str]) -> bool:
    """Is a single |alma_mater= entry already covered by the |education= entries?"""
    def resolve(title):
        return redirects.get(title, title)

    # Err on the side of keeping entries: a missed duplicate is visible in the
    # diff, a wrongly dropped entry is silent data loss.
    item_links = [resolve(t) for t in link_targets(item)]
    existing_links = {resolve(t) for e in existing for t in link_targets(e)}
    if item_links and item_links[0] in existing_links:
        return True

    plain = _plain(item)
    if len(plain) < 4:
        return False
    for e in existing:
        other = _plain(e)
        # "Harvard University" vs "Harvard University (BA)"
        if other == plain or other.startswith(plain + " ("):
            return True
    return False


# --------------------------------------------------------------- suggestions

@dataclass
class Suggestion:
    action: str          # rename | merge | drop
    value: str           # proposed |education= value
    added: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _build_list(like: ValueList, items: list[str]) -> str:
    """Build a fresh list in the same style as `like` (or the default)."""
    if like.kind == "positional":
        name = str(like.container.name).strip()
        return "{{" + name + "|" + "|".join(items) + "}}"
    if like.kind == "br":
        return like.br.join(items)
    if like.kind == "bullets":
        return "\n".join("* " + i for i in items)
    if like.kind == "plainlist":
        name = str(like.container.name).strip()
    else:
        name = DEFAULT_LIST_TEMPLATE
    return "{{" + name + "|\n" + "".join("* " + i + "\n" for i in items) + "}}"


def _append(edu: ValueList, new_items: list[str]) -> str:
    """Append entries to an existing list, keeping its formatting untouched."""
    if edu.kind == "plainlist":
        param = edu.container.get("1")
        old = str(param.value)
        body = old.rstrip()
        param.value = body + "".join("\n* " + i for i in new_items) + old[len(body):]
        return str(edu.code).strip()
    if edu.kind == "positional":
        # Positional values keep their whitespace, so "{{ubl\n|A\n|B\n}}" has
        # "B\n" as its last value. Reuse that layout for the new entries.
        params = [p for p in edu.container.params if p.name.strip().isdigit()]
        last = params[-1]
        old = str(last.value)
        body = old.rstrip()
        closing = old[len(body):]
        between = _split_ws(str(params[-2].value))[1] if len(params) > 1 else ""
        last.value = body + between
        n = int(last.name.strip())
        for i, item in enumerate(new_items, start=n + 1):
            end = closing if i == n + len(new_items) else between
            edu.container.add(str(i), item + end, showkey=False)
        return str(edu.code).strip()
    if edu.kind == "br":
        return str(edu.code).strip() + edu.br + edu.br.join(new_items)
    if edu.kind == "bullets":
        return str(edu.code).strip() + "".join("\n* " + i for i in new_items)
    raise ValueError(f"cannot append to a {edu.kind} value")


def suggest(alma: str, education: str | None, redirects: dict[str, str] | None = None) -> Suggestion:
    redirects = redirects or {}
    alma_list = parse_list(alma)
    edu_list = parse_list(education or "")

    if edu_list.kind == "empty":
        return Suggestion("rename", alma.strip())

    added, dupes = [], []
    for item in alma_list.items:
        (dupes if is_duplicate(item, edu_list.items, redirects) else added).append(item)

    notes = []
    if not added:
        notes.append("Every |alma_mater= entry already appears in |education=, "
                     "so |alma_mater= can simply be removed.")
        return Suggestion("drop", education.strip(), [], dupes, notes)

    if edu_list.kind == "single":
        style = alma_list if alma_list.kind not in ("single", "empty") else ValueList("default")
        value = _build_list(style, edu_list.items + added)
        if style.kind == "default":
            notes.append(f"Neither value was a list, so the entries were combined with "
                         f"{{{{{DEFAULT_LIST_TEMPLATE}}}}}.")
    else:
        value = _append(edu_list, added)

    notes.append("New entries were added at the end. Check the order "
                 "(education is usually listed chronologically).")
    notes.append("If |education= lists degrees and |alma_mater= lists where they were earned, "
                 "combine them into one entry per institution, "
                 "e.g. [[Harvard University]] (BA, 1990).")
    if dupes:
        notes.append("Some |alma_mater= entries look like duplicates of existing "
                     "|education= entries and were left out. Check that nothing was lost.")
    return Suggestion("merge", value, added, dupes, notes)


# ------------------------------------------------------------------ applying

class InvalidValue(ValueError):
    pass


def check_value(value: str) -> str:
    """Normalize a user-supplied |education= value and make sure it is safe to insert."""
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not value:
        raise InvalidValue("The education value is empty.")
    probe = mwparserfromhell.parse("{{X|" + NEW_PARAM + "=" + value + "}}")
    nodes = probe.nodes
    if (len(nodes) != 1 or not isinstance(nodes[0], Template)
            or [p.name.strip() for p in nodes[0].params] != [NEW_PARAM]
            or str(nodes[0].get(NEW_PARAM).value) != value):
        raise InvalidValue("The value contains a stray | or unbalanced braces or brackets, "
                           "which would break the infobox.")
    return value


def _split_ws(text: str) -> tuple[str, str]:
    body = text.strip()
    if not body:
        nl = text.find("\n")
        return (text, "") if nl < 0 else (text[:nl], text[nl:])
    start = text.find(body)
    return text[:start], text[start + len(body):]


def _renamed(tpl: Template, param) -> str:
    name = str(param.name)
    m = re.fullmatch(r"(\s*)" + OLD_PARAM + r"(\s*)", name)
    if not m:  # e.g. a comment inside the name
        return name.replace(OLD_PARAM, NEW_PARAM, 1)
    lead, trail = m.group(1), m.group(2)
    # If the "=" signs are lined up, keep them lined up: education is one
    # character shorter than alma_mater.
    aligned = "\n" not in trail and any(
        len(str(p.name)) == len(name) and p.name.strip() != OLD_PARAM
        for p in tpl.params if p.showkey
    )
    if aligned:
        trail += " "
    return lead + NEW_PARAM + trail


def apply(text: str, index: int, value: str) -> str:
    """Return the page text with infobox number `index` fixed.

    |education= is set to `value` and every |alma_mater= is removed (or renamed,
    when the infobox has no |education= yet).
    """
    value = check_value(value)
    code = mwparserfromhell.parse(text)
    tpl = _candidates(code)[index]
    almas = _params_named(tpl, OLD_PARAM)
    kept = tpl.get(OLD_PARAM)  # the last one is the one MediaWiki uses

    if tpl.has(NEW_PARAM):
        edu = tpl.get(NEW_PARAM)
        old = str(edu.value)
        source = old if bare(old) else str(kept.value)
        lead, trail = _split_ws(source)
        comments = "".join(str(n) for n in edu.value.nodes if isinstance(n, Comment))
        if not bare(old) and comments:
            value = value + " " + comments
        edu.value = lead + value + trail
        for p in almas:
            tpl.remove(p)
    else:
        lead, trail = _split_ws(str(kept.value))
        kept.value = lead + value + trail
        kept.name = _renamed(tpl, kept)
        for p in almas:
            if p is not kept:
                tpl.remove(p)
    return str(code)
