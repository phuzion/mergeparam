import html
import re
from datetime import datetime, timezone

import pytest

from mergeparam import candidates, handoff, preview, replica
from mergeparam.candidates import Filters
from mergeparam.web import create_app
from mergeparam.wiki import Page, WikiBusy

RENAME_TEXT = "{{Infobox person\n| name = A\n| alma_mater = [[Yale University]]\n}}\nText."
MERGE_TEXT = ("{{Infobox academic\n| name = B\n| education = [[Stanford University]] (BS)\n"
              "| alma_mater = [[Harvard]]\n}}\nText.")


def make_page(title, text, revid=100, protection=None):
    protection = [{"type": "edit", "level": protection, "expiry": "infinity"}] if protection else []
    return Page(title=title, pageid=1, revid=revid, timestamp="2026-10-01T12:34:56Z",
                text=text, protection=protection)


class FakeWiki:
    def __init__(self, pages, supported=True, busy=False):
        self.pages = {p.title: p for p in pages}
        self.supported = supported
        self.busy = busy
        self.queries = []

    def search(self, query, limit=20, random=True):
        if self.busy:
            raise WikiBusy("busy")
        self.queries.append(query)
        return list(self.pages), len(self.pages)

    def search_total(self, query):
        return 1234

    def latest(self, titles):
        return [self.pages[t] for t in titles if t in self.pages]

    def revision(self, revid):
        return next((p for p in self.pages.values() if p.revid == revid), None)

    def resolve_redirects(self, titles):
        return {"Harvard": "Harvard University"}

    def supports_param(self, template, param):
        return self.supported

    def lead_html(self, revid):
        return ('<div class="mw-parser-output"><table class="infobox"><tr><th>Name</th>'
                '<td onclick="x()"><a href="/wiki/Foo">Foo</a></td></tr></table>'
                '<p class="mw-empty-elt"></p><p>Lead <script>bad()</script>text.</p></div>')

    def category_exists(self, category):
        return category != "Missing"


@pytest.fixture
def client_for():
    def make(*pages, **kw):
        app = create_app(FakeWiki(list(pages), **kw))
        app.testing = True
        return app.test_client()
    return make


def hidden_fields(body):
    return {html.unescape(k): html.unescape(v)
            for k, v in re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', body)}


def test_index(client_for):
    r = client_for().get("/")
    assert r.status_code == 200
    assert b"1,234" in r.data
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_next_redirects_to_fix_with_filters(client_for):
    r = client_for(make_page("Jane Doe", RENAME_TEXT)).get("/next?category=Category:American_physicists")
    assert r.status_code == 302
    assert "/fix?title=Jane+Doe&category=American+physicists" in r.headers["Location"]


def test_next_unknown_category(client_for):
    r = client_for(make_page("Jane Doe", RENAME_TEXT)).get("/next?category=Missing")
    assert "There is no" in r.get_data(as_text=True)


def test_next_skips_unsupported_templates(client_for):
    r = client_for(make_page("Jane Doe", RENAME_TEXT), supported=False).get("/next")
    assert r.status_code == 200
    assert "Couldn" in r.get_data(as_text=True)


def test_next_when_wikipedia_busy(client_for):
    r = client_for(make_page("Jane Doe", RENAME_TEXT), busy=True).get("/next")
    assert r.status_code == 503


def test_fix_rename_builds_wikipedia_form(client_for):
    r = client_for(make_page("Jane Doe", RENAME_TEXT)).get("/fix?title=Jane+Doe")
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'action="https://en.wikipedia.org/w/index.php?title=Jane+Doe&amp;action=submit"' in body
    assert 'target="_blank"' in body
    fields = hidden_fields(body)
    assert fields["wpTextbox1"] == RENAME_TEXT.replace("alma_mater", "education")
    assert fields["wpSummary"].startswith("Renamed deprecated |alma_mater= to |education= per [[Wikipedia:")
    assert fields["wpSummary"].endswith("#mergeparam")
    assert fields["editRevId"] == "100"
    assert fields["wpEdittime"] == "20261001123456"
    assert fields["wpUltimateParam"] == "1"
    assert "wpDiff" in fields
    # context preview is sanitized
    assert "bad()" not in body and "onclick" not in body
    assert 'href="https://en.wikipedia.org/wiki/Foo"' in body


def test_fix_merge_shows_editable_suggestion(client_for):
    r = client_for(make_page("John Roe", MERGE_TEXT)).get("/fix?title=John+Roe")
    body = r.get_data(as_text=True)
    assert "Combine the two values" in body
    assert "wpTextbox1" not in body  # merges go through /prepare first
    assert "{{Plainlist|\n* [[Stanford University]] (BS)\n* [[Harvard]]\n}}" in html.unescape(body)


def test_fix_merge_detects_duplicate_via_redirect(client_for):
    text = MERGE_TEXT.replace("[[Stanford University]] (BS)", "[[Harvard University]] (AB)")
    body = client_for(make_page("John Roe", text)).get("/fix?title=John+Roe").get_data(as_text=True)
    assert "Remove the duplicate" in body


def test_fix_nothing_to_do(client_for):
    body = client_for(make_page("Done", "{{Infobox person|education=[[X]]}}")).get("/fix?title=Done")
    assert "Nothing to do here" in body.get_data(as_text=True)


def test_fix_unsupported_template_explained(client_for):
    r = client_for(make_page("Jane Doe", RENAME_TEXT), supported=False).get("/fix?title=Jane+Doe")
    assert "doesn't seem to display" in r.get_data(as_text=True)


def test_fix_missing_page_404(client_for):
    assert client_for().get("/fix?title=Nope").status_code == 404


def test_prepare_builds_form_from_user_value(client_for):
    client = client_for(make_page("John Roe", MERGE_TEXT))
    value = "{{ubl|[[Stanford University]] (BS)|[[Harvard University]] (PhD)}}"
    r = client.post("/prepare", data={"title": "John Roe", "revid": "100", "value": value.replace("\n", "\r\n")})
    body = r.get_data(as_text=True)
    assert "Ready to publish" in body
    fields = hidden_fields(body)
    assert "alma_mater" not in fields["wpTextbox1"]
    assert "| education = " + value + "\n" in fields["wpTextbox1"]
    assert fields["wpSummary"].startswith("Merged deprecated")


def test_prepare_unchanged_value_is_a_drop(client_for):
    client = client_for(make_page("John Roe", MERGE_TEXT))
    r = client.post("/prepare", data={"title": "John Roe", "revid": "100",
                                      "value": "[[Stanford University]] (BS)"})
    assert hidden_fields(r.get_data(as_text=True))["wpSummary"].startswith("Removed deprecated")


def test_prepare_rejects_breaking_value(client_for):
    client = client_for(make_page("John Roe", MERGE_TEXT))
    r = client.post("/prepare", data={"title": "John Roe", "revid": "100", "value": "[[A]] | name = Hacked"})
    body = r.get_data(as_text=True)
    assert "stray |" in body
    assert "wpTextbox1" not in body
    assert "[[A]] | name = Hacked" in html.unescape(body)  # user's input kept


def test_prepare_title_mismatch(client_for):
    client = client_for(make_page("John Roe", MERGE_TEXT))
    assert client.post("/prepare", data={"title": "Other", "revid": "100", "value": "x"}).status_code == 400


def test_stats_and_about(client_for):
    client = client_for()
    assert b"Hashtags tool" in client.get("/stats").data
    assert b"Your edits are your edits" in client.get("/about").data


# --------------------------------------------------------------- helpers

def test_filters_normalize():
    f = Filters.from_args({"category": "category:american_physicists", "project": "WikiProject biography",
                           "subcats": "1"})
    assert (f.category, f.project, f.subcats) == ("American physicists", "Biography", True)
    assert not Filters.from_args({"subcats": "1"}).subcats


def test_search_query():
    q = candidates.search_query(Filters(category='Say "hi"', subcats=True))
    assert q.startswith('insource:"alma_mater" incategory:Pages_using_infobox_person_with_deprecated_parameters|')
    assert q.endswith(' deepcategory:"Say \\"hi\\""')


def test_project_with_subcats_rejected():
    with pytest.raises(candidates.FilterError):
        candidates.random_titles(None, Filters(category="X", subcats=True, project="Biography"))


def test_replica_query_shape():
    sql, args = replica.build_query("American physicists", "Biography", 30)
    assert "cl_to" not in sql  # removed in MediaWiki 1.45
    assert sql.count("%s") == len(args)
    assert args[-3:] == ["American_physicists", "Biography", 30]


def test_edit_form_times():
    page = make_page("A", "x")
    url, fields = handoff.edit_form(page, "y", "merge", now=datetime(2026, 10, 10, 1, 2, 3, tzinfo=timezone.utc))
    assert fields["wpStarttime"] == "20261010010203"
    assert url == "https://en.wikipedia.org/w/index.php?title=A&action=submit"


def test_preview_drops_unsafe_urls():
    out = preview.context('<table class="infobox"><tr><td><a href="javascript:alert(1)">x</a>'
                          '<img src="//upload.wikimedia.org/a.png" srcset="//upload.wikimedia.org/b.png 2x">'
                          '</td></tr></table>')
    assert "javascript:" not in out["infobox"]
    assert 'src="https://upload.wikimedia.org/a.png"' in out["infobox"]
    assert "https://upload.wikimedia.org/b.png 2x" in out["infobox"]


# -------------------------------------------------------------- protection

def test_strictest_protection_wins():
    page = make_page("A", "x")
    page.protection = [{"type": "move", "level": "sysop"},
                       {"type": "edit", "level": "autoconfirmed"},
                       {"type": "edit", "level": "extendedconfirmed", "source": "Cascade"}]
    assert page.edit_protection == "extendedconfirmed"


@pytest.mark.parametrize("access,level,ok", [
    ("any", None, True),
    ("any", "autoconfirmed", False),
    ("autoconfirmed", "autoconfirmed", True),
    ("autoconfirmed", "extendedconfirmed", False),
    ("extendedconfirmed", "extendedconfirmed", True),
    ("extendedconfirmed", "sysop", False),
    ("sysop", "sysop", True),
    ("sysop", "some-new-level", False),  # unknown levels count as strictest
])
def test_can_edit(access, level, ok):
    assert candidates.can_edit(make_page("A", "x", protection=level), access) is ok


def test_valid_access():
    assert candidates.valid_access(None) == "autoconfirmed"
    assert candidates.valid_access("bogus") == "autoconfirmed"
    assert candidates.valid_access("sysop") == "sysop"


def test_next_skips_pages_above_access_level(client_for):
    client = client_for(make_page("Locked", RENAME_TEXT, protection="extendedconfirmed"),
                        make_page("Open", RENAME_TEXT, revid=101))
    assert "title=Open" in client.get("/next").headers["Location"]
    client.set_cookie("access", "extendedconfirmed")
    assert "title=Locked" in client.get("/next?skip=Open").headers["Location"]


def test_access_is_not_read_from_the_url(client_for):
    client = client_for(make_page("Locked", RENAME_TEXT, protection="sysop"))
    assert client.get("/next?access=sysop").status_code == 200  # nothing found


def test_settings_saves_cookie_and_default_deletes_it(client_for):
    client = client_for()
    r = client.post("/settings", data={"access": "extendedconfirmed", "watch": "1"})
    assert r.status_code == 302 and "saved=1" in r.headers["Location"]
    assert len(r.headers.getlist("Set-Cookie")) == 1
    cookie = r.headers["Set-Cookie"]
    assert cookie.startswith("access=extendedconfirmed;")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    body = client.get("/settings?saved=1").get_data(as_text=True)
    assert "Saved." in body
    assert '<option value="extendedconfirmed" selected>' in body
    assert "Extended confirmed" in client.get("/").get_data(as_text=True)  # start page says so

    r = client.post("/settings", data={"access": "autoconfirmed", "watch": "1"})
    assert "access=;" in r.headers["Set-Cookie"] and "Max-Age=0" in r.headers["Set-Cookie"]


def test_settings_cookie_secure_behind_toolforge_proxy(client_for):
    r = client_for().post("/settings", data={"access": "sysop", "watch": "1"},
                          headers={"X-Forwarded-Proto": "https"})
    assert "Secure" in r.headers["Set-Cookie"]


def test_settings_rejects_cross_site_post(client_for):
    r = client_for().post("/settings", data={"access": "sysop"},
                          headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert "Set-Cookie" not in r.headers


def test_no_cookie_by_default(client_for):
    client = client_for(make_page("Jane Doe", RENAME_TEXT))
    for url in ["/", "/next", "/fix?title=Jane+Doe", "/settings", "/about"]:
        assert "Set-Cookie" not in client.get(url).headers, url


def test_review_names_protection_level(client_for):
    client = client_for(make_page("Locked", RENAME_TEXT, protection="extendedconfirmed"))
    assert "extended-confirmed protected" in client.get("/fix?title=Locked").get_data(as_text=True)


def test_settings_link_in_header(client_for):
    assert 'href="/settings">Settings</a>' in client_for().get("/").get_data(as_text=True)


def test_saving_defaults_sets_no_cookies(client_for):
    r = client_for().post("/settings", data={"access": "autoconfirmed", "watch": "1"})
    assert "Set-Cookie" not in r.headers


def watch_box(body):
    return re.search(r'<input type="checkbox" name="wpWatchthis" value="1" ?(checked)?>', body).group(1)


def test_watchlist_box_ticked_by_default(client_for):
    client = client_for(make_page("Jane Doe", RENAME_TEXT))
    assert watch_box(client.get("/fix?title=Jane+Doe").get_data(as_text=True)) == "checked"
    assert 'name="watch" value="1" checked' in client.get("/settings").get_data(as_text=True)


def test_watchlist_setting_off_and_back_on(client_for):
    client = client_for(make_page("John Roe", MERGE_TEXT))
    r = client.post("/settings", data={"access": "autoconfirmed"})  # box unticked
    assert r.headers.getlist("Set-Cookie")[0].startswith("watch=0;")
    assert 'name="watch" value="1" >' in client.get("/settings").get_data(as_text=True)
    body = client.post("/prepare", data={"title": "John Roe", "revid": "100",
                                         "value": "[[A]]"}).get_data(as_text=True)
    assert watch_box(body) is None  # unticked on the publish step too

    r = client.post("/settings", data={"access": "autoconfirmed", "watch": "1"})
    assert r.headers["Set-Cookie"].startswith("watch=;") and "Max-Age=0" in r.headers["Set-Cookie"]
    assert watch_box(client.post("/prepare", data={"title": "John Roe", "revid": "100",
                                                   "value": "[[A]]"}).get_data(as_text=True)) == "checked"


# ------------------------------------------------------------ auto-advance

def test_auto_advance_off_by_default(client_for):
    client = client_for(make_page("Jane Doe", RENAME_TEXT))
    body = client.get("/fix?title=Jane+Doe").get_data(as_text=True)
    assert 'target="mergeparam-wikipedia"' in body
    assert "data-auto" not in body
    assert 'name="auto" value="1" >' in client.get("/settings").get_data(as_text=True)


def test_auto_advance_setting_adds_poll_data(client_for):
    client = client_for(make_page("Jane Doe", RENAME_TEXT + "\n{{Infobox academic|alma_mater=[[X]]}}"))
    r = client.post("/settings", data={"access": "autoconfirmed", "watch": "1", "auto": "1"})
    assert r.headers["Set-Cookie"].startswith("auto=1;")
    body = client.get("/fix?title=Jane+Doe").get_data(as_text=True)
    assert "data-auto" in body
    assert 'data-revid="100"' in body
    assert 'data-before="2"' in body  # two infoboxes still need fixing
    assert 'data-check="/check"' in body
    assert 'data-api="https://en.wikipedia.org/w/api.php"' in body
    assert "moves on to the" in body

    r = client.post("/settings", data={"access": "autoconfirmed", "watch": "1"})
    assert r.headers["Set-Cookie"].startswith("auto=;")


def test_csp_allows_wikipedia_api_only(client_for):
    csp = client_for().get("/").headers["Content-Security-Policy"]
    assert "connect-src 'self' https://en.wikipedia.org" in csp


def test_check_detects_fix(client_for):
    fixed = make_page("Jane Doe", RENAME_TEXT.replace("alma_mater", "education"), revid=101)
    client = client_for(fixed)
    r = client.get("/check?title=Jane+Doe&revid=101&before=1")
    assert r.get_json() == {"done": True}
    assert r.headers["Cache-Control"] == "no-store"


def test_check_unrelated_edit_is_not_done(client_for):
    other = make_page("Jane Doe", RENAME_TEXT + "\nMore text.", revid=101)
    assert client_for(other).get("/check?title=Jane+Doe&revid=101&before=1").get_json() == {"done": False}


def test_check_one_of_two_infoboxes_fixed(client_for):
    text = "{{Infobox person|education=[[A]]}}\n{{Infobox academic|alma_mater=[[X]]}}"
    page = make_page("Two", text, revid=101)
    assert client_for(page).get("/check?title=Two&revid=101&before=2").get_json() == {"done": True}


@pytest.mark.parametrize("query", ["title=Jane+Doe&revid=x&before=1", "title=Other&revid=101&before=1",
                                   "title=Jane+Doe&revid=101"])
def test_check_bad_requests(client_for, query):
    page = make_page("Jane Doe", RENAME_TEXT, revid=101)
    assert client_for(page).get("/check?" + query).status_code == 400


# ------------------------------------------------------------- kind filter

@pytest.mark.parametrize("text,rename", [
    (RENAME_TEXT, True),
    ("{{Infobox person|alma_mater=[[A]]|education=}}", True),
    ("{{Infobox person|alma_mater=[[A]]|education=<!-- degrees -->}}", True),
    (MERGE_TEXT, False),
])
def test_is_rename(text, rename):
    from mergeparam.infobox import find_target
    assert candidates.is_rename(find_target(text)) is rename


def test_valid_kind():
    assert candidates.valid_kind(None) == "all"
    assert candidates.valid_kind("bogus") == "all"
    assert candidates.valid_kind("merge") == "merge"


@pytest.mark.parametrize("kind,expected", [("merge", "John+Roe"), ("rename", "Jane+Doe")])
def test_next_respects_kind(client_for, kind, expected):
    client = client_for(make_page("Jane Doe", RENAME_TEXT), make_page("John Roe", MERGE_TEXT, revid=101))
    client.set_cookie("kind", kind)
    for _ in range(3):  # FakeWiki returns pages in a fixed order, so this is deterministic
        assert f"title={expected}" in client.get("/next").headers["Location"]


def test_next_kind_with_nothing_matching(client_for):
    client = client_for(make_page("Jane Doe", RENAME_TEXT))
    client.set_cookie("kind", "merge")
    assert "Couldn" in client.get("/next").get_data(as_text=True)


def test_settings_saves_kind(client_for):
    client = client_for()
    r = client.post("/settings", data={"access": "autoconfirmed", "watch": "1", "kind": "rename"})
    assert r.headers["Set-Cookie"].startswith("kind=rename;")
    assert '<option value="rename" selected>' in client.get("/settings").get_data(as_text=True)
    assert "only simple renames" in client.get("/").get_data(as_text=True)
    r = client.post("/settings", data={"access": "autoconfirmed", "watch": "1", "kind": "all"})
    assert r.headers["Set-Cookie"].startswith("kind=;")
    assert "Showing:" not in client.get("/").get_data(as_text=True)


# ------------------------------------------------------------- error pages

def test_unknown_url_is_themed_404(client_for):
    r = client_for().get("/no-such-page")
    body = html.unescape(r.get_data(as_text=True))
    assert r.status_code == 404
    assert "Page not found" in body
    assert "<title>Page not found · mergeparam</title>" in body
    assert 'class="brand"' in body and 'href="/settings">Settings</a>' in body  # site chrome
    assert "English Wikipedia" not in body  # not the missing-article message


def test_missing_article_404_names_it(client_for):
    r = client_for().get("/fix?title=Nope")
    body = html.unescape(r.get_data(as_text=True))
    assert r.status_code == 404
    assert "Article not found" in body and "“Nope”" in body


def test_cross_site_settings_post_is_themed_403(client_for):
    r = client_for().post("/settings", data={"access": "sysop"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert "That isn't allowed here" in html.unescape(r.get_data(as_text=True))


def test_wrong_method_is_themed_405_with_allow_header(client_for):
    r = client_for().get("/prepare")
    assert r.status_code == 405
    assert set(r.headers["Allow"].split(", ")) == {"OPTIONS", "POST"}  # order varies
    body = html.unescape(r.get_data(as_text=True))
    assert "can't be opened that way" in body
    assert "Give me an article" in body


def test_bad_request_is_themed_400(client_for):
    r = client_for().post("/prepare", data={"title": "X", "revid": "nope"})
    assert r.status_code == 400
    assert "didn't make sense" in html.unescape(r.get_data(as_text=True))


def test_unexpected_error_is_themed_500():
    class Broken(FakeWiki):
        def latest(self, titles):
            raise RuntimeError("boom")

    app = create_app(Broken([]))  # not app.testing, so the error handler runs
    r = app.test_client().get("/fix?title=X")
    body = html.unescape(r.get_data(as_text=True))
    assert r.status_code == 500
    assert "Something went wrong on our side" in body
    assert "User talk:Phuzion" in body
    assert "boom" not in body  # no internals leaked


def test_redirects_are_not_turned_into_error_pages(client_for):
    assert client_for().get("/fix").status_code == 302


def test_prefilled_summary_does_not_trigger_blank_summary_prompt():
    # MediaWiki prompts when md5(summary) equals wpAutoSummary. Declaring the
    # starting summary blank means only a blanked summary prompts.
    import hashlib
    _, fields = handoff.edit_form(make_page("A", "x"), "y", "merge")
    assert fields["wpAutoSummary"] == hashlib.md5(b"").hexdigest() == "d41d8cd98f00b204e9800998ecf8427e"
    assert hashlib.md5(fields["wpSummary"].encode()).hexdigest() != fields["wpAutoSummary"]


def test_hidden_attribute_beats_button_display_styles():
    # .btn sets display: inline-flex, which would otherwise show [hidden] buttons
    # ("Reset to suggestion", "Stay here", "Go back") all the time.
    from pathlib import Path
    css = (Path(__file__).parent.parent / "static" / "style.css").read_text(encoding="utf-8")
    assert "[hidden] { display: none !important; }" in css
