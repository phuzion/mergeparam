from __future__ import annotations

import logging
from urllib.parse import urlencode

from flask import Flask, abort, redirect, render_template, request, url_for

from . import candidates, config, handoff, infobox, preview
from .candidates import FilterError, Filters
from .replica import ReplicaUnavailable
from .wiki import Wiki, WikiError

log = logging.getLogger(__name__)

CSP = "; ".join([
    "default-src 'self'",
    # Images in the infobox preview come from Wikimedia's media servers.
    "img-src 'self' https://upload.wikimedia.org https://thumb.wikimedia.org data:",
    # TemplateStyles in the preview arrive as inline <style> blocks.
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self'",
    "form-action 'self' https://en.wikipedia.org",
    "frame-ancestors 'none'",
    "base-uri 'none'",
])


def create_app(wiki: Wiki | None = None) -> Flask:
    app = Flask(__name__, template_folder="../templates", static_folder="../static")
    app.extensions["wiki"] = wiki or Wiki()

    def get_wiki() -> Wiki:
        return app.extensions["wiki"]

    @app.context_processor
    def globals_():
        return {"config": config}

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
            remaining = get_wiki().search_total(candidates.search_query())
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
            found = candidates.find_next(get_wiki(), filters, exclude=skip)
        except (FilterError, ReplicaUnavailable, WikiError) as e:
            return render_index(filters, error=str(e), status=503 if isinstance(e, WikiError) else 200)
        if not found:
            msg = ("No articles matching these filters need this fix right now."
                   if filters else "Couldn't find an article to fix. Please try again.")
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
            abort(404)
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
                links = infobox.link_targets(target.alma) + infobox.link_targets(target.education)
                redirects = wiki.resolve_redirects(links)
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

    @app.get("/about")
    def about():
        return render_template("about.html")

    @app.errorhandler(400)
    @app.errorhandler(404)
    def client_error(e):
        return render_template("error.html", error=e), e.code

    @app.errorhandler(500)
    def server_error(e):
        return render_template("error.html", error=e), 500

    return app
