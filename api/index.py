from werkzeug.middleware.proxy_fix import ProxyFix

# Import the Flask `app` from the project's entry point.
# Vercel's Python builder (@vercel/python) will serve a WSGI app
# exposed as the variable `app` in this file.
from app import app

# Trust proxy headers (useful behind Vercel's edge)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)

# Expose `app` for the platform to discover.
# No additional code needed; the runtime will call the WSGI app.
