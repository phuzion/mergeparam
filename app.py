"""Toolforge entry point: uWSGI loads `app` from $HOME/www/python/src/app.py."""

from mergeparam.web import create_app

app = create_app()

if __name__ == "__main__":
    app.run(debug=True)
