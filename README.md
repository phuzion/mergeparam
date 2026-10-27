# mergeparam

A [Toolforge](https://toolforge.org/) tool that guides editors through merging the
deprecated `|alma_mater=` infobox parameter into `|education=` on English Wikipedia,
one article at a time, in the style of [Citation Hunt](https://citationhunt.toolforge.org/).

Consensus: [RfC: Alma mater vs Education in Infoboxes](https://en.wikipedia.org/wiki/Wikipedia:Village_pump_(miscellaneous)/Archive_88#RFC:_Alma_mater_vs_Education_in_Infoboxes)
(closed 5 July 2026).

## How it works

1. A random article is picked from the tracking categories of `{{Infobox person}}`,
   `{{Infobox officeholder}}`, `{{Infobox academic}}`, `{{Infobox Christian leader}}` and
   `{{Infobox college coach}}`,
   using CirrusSearch (`insource:"alma_mater" incategory:…`, `srsort=random`).
   A topic filter can narrow this by category (optionally with subcategories) or by WikiProject.
2. The article's wikitext is parsed with mwparserfromhell. The tool checks that the infobox
   (or wrapper) actually displays `|education=` by rendering a probe, then suggests a change:
   - only `|alma_mater=` → rename the parameter;
   - both → a combined value the user edits, in the same list format `|education=` already
     uses (`{{Plainlist}}` if neither value is a list), with likely duplicates left out and flagged.
3. The full new page text is POSTed to Wikipedia's own `index.php?action=submit` with `wpDiff`,
   so the editor sees Wikipedia's **Show changes** screen and publishes it from their own account.
   The base revision is passed along, so Wikipedia reports an edit conflict if the article changed.

The tool never edits Wikipedia, has no OAuth consumer and stores nothing about users. Its only
cookies hold the editor's settings (which kinds of fix to show, protection level, watchlist default,
auto-advance, light/dark theme), one
cookie per setting, and each is set only if the editor changes that setting from the default.

With auto-advance on (off by default), the browser polls Wikipedia's public API every five
seconds for the article's latest revision ID, for up to 15 minutes. When it changes, it asks the
tool once (`/check`) whether that revision removed the `|alma_mater=`, and if so counts down and
loads the next article.
Edit summaries include `#mergeparam` for tracking through the
[Hashtags tool](https://hashtags.wmcloud.org/?query=mergeparam&project=en.wikipedia.org).

## Development

```bash
python -m venv .venv
.venv/Scripts/pip install -r requirements-dev.txt   # .venv/bin/pip on Linux/macOS
.venv/Scripts/python -m pytest
.venv/Scripts/python -m flask --app app run
```

Everything except the WikiProject filter works locally against the public API.
The WikiProject filter needs the wiki replicas. To use it locally, open an SSH tunnel and
point the tool at it:

```bash
ssh -N -L 3306:enwiki.web.db.svc.wikimedia.cloud:3306 <shell-user>@login.toolforge.org
MERGEPARAM_REPLICA_HOST=127.0.0.1 MERGEPARAM_REPLICA_CNF=/path/to/replica.my.cnf flask --app app run
```

## Deploying on Toolforge

```bash
ssh <shell-user>@login.toolforge.org
become mergeparam
git clone https://github.com/phuzion/mergeparam.git $HOME/www/python/src
toolforge webservice python3.13 shell
  python3 -m venv $HOME/www/python/venv
  source $HOME/www/python/venv/bin/activate
  pip install --upgrade pip wheel
  pip install -r $HOME/www/python/src/requirements.txt
  exit
toolforge webservice python3.13 start
```

Logs are in `$HOME/uwsgi.log`. To update: `git pull` in `$HOME/www/python/src`, then
`toolforge webservice python3.13 restart`.

The footer and About page link to the source code, as the Toolforge rules require.
If the repository moves, update `SOURCE_URL` in `mergeparam/config.py` (or set the
`MERGEPARAM_SOURCE_URL` environment variable with `toolforge envvars create`).

## Configuration

`mergeparam/config.py` holds the covered infoboxes and their tracking categories, the RfC
link, the hashtag and the maintainer name used in the User-Agent.

## License

MIT. See [LICENSE](LICENSE).
