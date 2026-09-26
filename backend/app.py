"""
HTTP adapter for the webhook receiver (spec §4.2). This is the ONLY file that
needs a web framework, and it's only needed to *serve* the endpoint — the tests
exercise webhook.process_event directly, so `pip install flask` is a deploy-time
step, not a test dependency.

    export ZEFFY_API_KEY=...            # read API, for re-fetch verification
    export ZEFFY_WEBHOOK_SECRET=...     # optional HMAC secret, if Zeffy sends one
    export DATABASE_URL=postgres://...
    pip install flask
    flask --app app run          # then point Zeffy's webhook at /webhooks/zeffy

Behind TLS in production (spec §4.3, §8). Return 200 fast; Zeffy retries non-200.
"""
import os

import db as DB
import webhook
from zeffy_client import ZeffyClient

try:
    from flask import Flask, request, jsonify
except ImportError:  # keep import-time failure friendly for the test runner
    Flask = None


def create_app():
    if Flask is None:
        raise RuntimeError("Flask is not installed. `pip install flask` to serve.")
    app = Flask(__name__)
    zeffy = ZeffyClient()
    secret = os.environ.get("ZEFFY_WEBHOOK_SECRET")

    @app.post("/webhooks/zeffy")
    def zeffy_webhook():
        conn = DB.connect()
        try:
            status, payload = webhook.process_event(
                conn, zeffy, request.get_data(), dict(request.headers), secret
            )
        finally:
            conn.close()
        return jsonify(payload), status

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


if __name__ == "__main__":
    create_app().run(port=int(os.environ.get("PORT", 5000)))
