from __future__ import annotations

import logging
from urllib.parse import urlencode, urlparse

from flask import Flask, abort, redirect, render_template, request, url_for
from werkzeug.exceptions import HTTPException

from . import candidates, config, handoff, infobox, preview
from .candidates import FilterError, Filters
from .replica import ReplicaUnavailable
from .wiki import PROTECTION_LEVELS, Wiki, WikiError

log = logging.getLogger(__name__)

# The tool's only cookies: one per setting, each holding just that setting,
# and each set only when the editor changes it from the default.
ACCESS_COOKIE = "access"
WATCH_COOKIE = "watch"      # "0" when the watchlist box should start unticked
AUTO_COOKIE = "auto"        # "1" to move on automatically after publishing
KIND_COOKIE = "kind"        # which kinds of fix to show, when not all of them
THEME_COOKIE = "theme"      # "light" or "dark"; no cookie means follow the device
INFOBOX_COOKIE = "infoboxes"  # e.g. "academic.christian_leader", when not all of them
COOKIE_MAX_AGE = 365 * 24 * 3600

# The theme toggle cycles through these, in this order.
THEMES = {"system": "Same as your device", "light": "Light", "dark": "Dark"}

# Heading and explanation for each error page.
ERRORS = {
    400: ("That request didn't make sense",
          "Something in the link or form was missing or out of date. Starting again usually fixes it."),
    403: ("That isn't allowed here",
          "This tool only accepts that from its own pages. If a link or form on another site brought "
          "you here, start from the tool instead."),
    404: ("Page not found",
          "There's nothing at this address. The link may be mistyped or out of date."),
    405: ("This page can't be opened that way",
          "It's a step in the middle of the tool, so it can't be reloaded, bookmarked or opened "
          "directly. Go back to the article and carry on from there."),
    500: ("Something went wrong on our side",
          "The tool hit an unexpected error. Please try again. If it keeps happening, let the "
          "maintainer know."),
}

CSP = "; ".join([
    "default-src 'self'",
    # Images in the infobox preview come from Wikimedia's media servers.
    "img-src 'self' https://upload.wikimedia.org https://thumb.wikimedia.org data:",
    # TemplateStyles in the preview arrive as inline <style> blocks.
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
    # Auto-advance asks Wikipedia's API whether the article has a new revision.
    "connect-src 'self' https://en.wikipedia.org",
    "form-action 'self' https://en.wikipedia.org",
    "frame-ancestors 'none'",
    "base-uri 'none'",
])


def create_app(wiki: Wiki | None = None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.extensions["wiki"] = wiki or Wiki()
    # A global rather than context, so imported macros can use it too.
    app.jinja_env.globals["config"] = config

    def get_wiki() -> Wiki:
        return app.extensions["wiki"]

    def current_access() -> str:
        return candidates.valid_access(request.cookies.get(ACCESS_COOKIE))

    def current_watch() -> bool:
        return request.cookies.get(WATCH_COOKIE) != "0"

    def current_auto() -> bool:
        return request.cookies.get(AUTO_COOKIE) == "1"

    def current_kind() -> str:
        return candidates.valid_kind(request.cookies.get(KIND_COOKIE))

    def current_infoboxes() -> list[str]:
        return candidates.valid_infoboxes(request.cookies.get(INFOBOX_COOKIE))

    def narrowed(infoboxes: list[str]) -> bool:
        return len(infoboxes) < len(candidates.INFOBOXES)

    def current_theme() -> str:
        theme = request.cookies.get(THEME_COOKIE)
        return theme if theme in ("light", "dark") else "system"

    @app.context_processor
    def globals_():
        return {"config": config, "access_levels": candidates.ACCESS_LEVELS,
                "default_access": candidates.DEFAULT_ACCESS, "access": current_access(),
                "watch": current_watch(), "auto": current_auto(),
                "kinds": candidates.KINDS, "kind": current_kind(),
                "all_infoboxes": candidates.INFOBOXES, "infoboxes": current_infoboxes(),
                "infoboxes_narrowed": narrowed(current_infoboxes()),
                "themes": THEMES, "theme": current_theme(), "next_theme": next_theme(current_theme()),
                "protection_levels": PROTECTION_LEVELS}

    def next_theme(theme: str) -> str:
        order = list(THEMES)
        return order[(order.index(theme) + 1) % len(order)]

    def same_site() -> bool:
        """Only accept settings changes from this site, so other sites can't change them."""
        origin = request.headers.get("Origin")
        return not origin or urlparse(origin).netloc == request.host

    def remember_theme(resp, theme: str):
        remember(resp, THEME_COOKIE, theme if theme in ("light", "dark") else None)

    @app.template_filter("wikiurl")
    def wikiurl(title, **params):
        if params:
            return config.INDEX + "?" + urlencode({"title": title, **params})
        return config.WIKI + "/wiki/" + title.replace(" ", "_")

    @app.after_request
    def headers(resp):
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        return resp

    # -------------------------------------------------------------- pages

    @app.get("/")
    def index():
        filters = Filters.from_args(request.args)
        return render_index(filters)

    def render_index(filters: Filters, error: str = "", message: str = "", status: int = 200):
        remaining = None
        try:
            remaining = get_wiki().search_total(candidates.search_query(None, current_infoboxes()))
        except WikiError:
            pass
        return render_template("index.html", filters=filters, remaining=remaining,
                               error=error, message=message), status

    @app.get("/next")
    def next_article():
        filters = Filters.from_args(request.args)
        skip = set(request.args.getlist("skip"))
        try:
            if filters.category and not get_wiki().category_exists(filters.category):
                return render_index(filters, error=f"There is no “Category:{filters.category}” "
                                                   "on English Wikipedia.")
            found = candidates.find_next(get_wiki(), filters, current_access(), exclude=skip,
                                         kind=current_kind(), infoboxes=current_infoboxes())
        except (FilterError, ReplicaUnavailable, WikiError) as e:
            return render_index(filters, error=str(e), status=503 if isinstance(e, WikiError) else 200)
        if not found:
            msg = ("No articles matching these filters need this fix right now."
                   if filters or narrowed(current_infoboxes())
                   else "Couldn't find an article to fix. Please try again.")
            return render_index(filters, message=msg)
        page, _ = found
        return redirect(url_for("fix", title=page.title, **filters.as_args()))

    @app.get("/fix")
    def fix():
        filters = Filters.from_args(request.args)
        title = request.args.get("title", "").strip()
        if not title:
            return redirect(url_for("index"))
        try:
            pages = get_wiki().latest([title])
            page = pages[0] if pages else None
            target = candidates.check_page(get_wiki(), page) if page else None
        except WikiError as e:
            return render_index(filters, error=str(e), status=503)
        if not page:
            return render_error(404, "Article not found",
                                f"There's no article called “{title}” on English Wikipedia. "
                                "It may have been renamed, merged or deleted.")
        if not target:
            return render_template("nothing.html", page=page, filters=filters,
                                   has_alma=infobox.find_target(page.text) is not None)
        return render_review(page, target, filters)

    @app.post("/prepare")
    def prepare():
        """Step 2 for merges: rebuild the edit from the revision the user reviewed."""
        filters = Filters.from_args(request.form)
        try:
            revid = int(request.form.get("revid", ""))
        except ValueError:
            abort(400)
        try:
            page = get_wiki().revision(revid)
        except WikiError as e:
            return render_index(filters, error=str(e), status=503)
        if not page or page.title != request.form.get("title"):
            abort(400)
        target = infobox.find_target(page.text)
        if not target:
            return render_template("nothing.html", page=page, filters=filters, has_alma=False)

        value = request.form.get("value", "")
        try:
            new_text = infobox.apply(page.text, target.index, value)
        except infobox.InvalidValue as e:
            return render_review(page, target, filters, value=value, error=str(e))
        value = infobox.check_value(value)
        action = "drop" if value == (target.education or "").strip() else "merge"
        url, fields = handoff.edit_form(page, new_text, action)
        return render_template("publish.html", page=page, target=target, filters=filters,
                               value=value, action=action, form_url=url, fields=fields)

    def render_review(page, target, filters, value=None, error=""):
        wiki = get_wiki()
        context = {"infobox": "", "lead": ""}
        redirects = {}
        try:
            if target.education and infobox.bare(target.education):
                redirects = wiki.resolve_redirects(
                    infobox.lookup_titles(target.alma, target.education))
            context = preview.context(wiki.lead_html(page.revid))
        except WikiError:
            log.warning("context preview failed for %s", page.title, exc_info=True)
        suggestion = infobox.suggest(target.alma, target.education, redirects)

        form_url = fields = None
        if suggestion.action == "rename":
            new_text = infobox.apply(page.text, target.index, target.alma)
            form_url, fields = handoff.edit_form(page, new_text, "rename")

        return render_template(
            "review.html", page=page, target=target, filters=filters, suggestion=suggestion,
            value=suggestion.value if value is None else value, error=error,
            context=context, in_use=infobox.in_use_tagged(page.text),
            form_url=form_url, fields=fields,
        )

    @app.get("/stats")
    def stats():
        wiki = get_wiki()
        try:
            remaining = wiki.search_total(candidates.search_query())
            per_template = {
                name: wiki.search_total(
                    f'insource:"alma_mater" incategory:{cat.replace(" ", "_")}')
                for name, cat in config.TRACKING_CATEGORIES.items()
            }
        except WikiError as e:
            return render_index(Filters(), error=str(e), status=503)
        return render_template("stats.html", remaining=remaining, per_template=per_template)

    @app.get("/check")
    def check():
        """Called once by auto-advance when the article gets a new revision.

        Answers whether that revision fixed the infobox, i.e. has fewer
        infoboxes with |alma_mater= than the page had when it was reviewed.
        """
        try:
            revid = int(request.args.get("revid", ""))
            before = int(request.args.get("before", ""))
        except ValueError:
            abort(400)
        try:
            page = get_wiki().revision(revid)
        except WikiError:
            return {"done": False, "error": "busy"}, 503, {"Cache-Control": "no-store"}
        if not page or page.title != request.args.get("title"):
            abort(400)
        done = infobox.count_targets(page.text) < before
        return {"done": done}, 200, {"Cache-Control": "no-store"}

    @app.get("/settings")
    def settings():
        return render_template("settings.html", saved=request.args.get("saved") == "1")

    @app.post("/settings")
    def save_settings():
        if not same_site():
            abort(403)
        # The form always sends "infoboxes", so no "infobox" ticks means all were unticked.
        chosen = request.form.getlist("infobox")
        if "infoboxes" in request.form and not chosen:
            return render_template(
                "settings.html", error="Tick at least one infobox.",
                access=candidates.valid_access(request.form.get("access")),
                watch=request.form.get("watch") == "1", auto=request.form.get("auto") == "1",
                kind=candidates.valid_kind(request.form.get("kind")), infoboxes=[],
                infoboxes_narrowed=True), 400
        resp = redirect(url_for("settings", saved=1))
        access = candidates.valid_access(request.form.get("access"))
        remember(resp, ACCESS_COOKIE, None if access == candidates.DEFAULT_ACCESS else access)
        # An unticked checkbox isn't sent at all, so absence means "off".
        remember(resp, WATCH_COOKIE, None if request.form.get("watch") == "1" else "0")
        remember(resp, AUTO_COOKIE, "1" if request.form.get("auto") == "1" else None)
        kind = candidates.valid_kind(request.form.get("kind"))
        remember(resp, KIND_COOKIE, None if kind == candidates.DEFAULT_KIND else kind)
        infoboxes = candidates.valid_infoboxes(".".join(chosen))
        remember(resp, INFOBOX_COOKIE, ".".join(infoboxes) if narrowed(infoboxes) else None)
        if request.form.get("theme") in THEMES:
            remember_theme(resp, request.form["theme"])
        return resp

    @app.post("/theme")
    def save_theme():
        """The header's theme toggle. Scripts get 204; without JavaScript, it's a form post."""
        if not same_site():
            abort(403)
        theme = request.form.get("theme")
        if theme not in THEMES:
            abort(400)
        if request.headers.get("X-Requested-With") == "fetch":
            resp = app.response_class(status=204)
        else:
            back = request.form.get("next", "")
            # Only return to a page on this site.
            if not back.startswith("/") or back.startswith("//") or "\\" in back:
                back = url_for("index")
            resp = redirect(back)
        remember_theme(resp, theme)
        return resp

    def remember(resp, name: str, value: str | None):
        """Store a non-default setting, or delete its cookie when it's back to the default."""
        if value is None:
            if name in request.cookies:
                resp.delete_cookie(name)
            return
        # Toolforge's proxy terminates TLS, so check the forwarded scheme too.
        secure = request.is_secure or request.headers.get("X-Forwarded-Proto") == "https"
        resp.set_cookie(name, value, max_age=COOKIE_MAX_AGE, secure=secure,
                        httponly=True, samesite="Lax")

    @app.get("/about")
    def about():
        return render_template("about.html")

    def render_error(code: int, heading: str = "", message: str = "", headers=None):
        default_heading, default_message = ERRORS.get(
            code, ("Something went wrong", "Please try again from the start page."))
        return render_template("error.html", code=code, heading=heading or default_heading,
                               message=message or default_message), code, headers or {}

    @app.errorhandler(HTTPException)
    def http_error(e):
        # Keep the Allow header on 405s, as HTTP requires.
        headers = {k: v for k, v in e.get_headers() if k.lower() == "allow"}
        return render_error(e.code, headers=headers)

    return app
