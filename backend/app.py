"""
HTTP adapter (spec §4.2, §6). The only file that needs a web framework, and only
to *serve* — the tests exercise the cores directly, so Flask is a deploy-time
dependency, not a test one.

    export ZEFFY_API_KEY=...            # read API, for re-fetch verification
    export ZEFFY_WEBHOOK_SECRET=...     # optional HMAC secret, if Zeffy sends one
    export GUEST_SESSION_SECRET=...     # sign guest sessions (set a real one!)
    export DATABASE_URL=postgres://...
    pip install flask
    flask --app app run

Routes:
    POST /webhooks/zeffy      Zeffy payment.completed (verify -> map -> credit)
    GET  /                    the guest phone app (QR target)
    POST /api/claim           {email|code} -> signed session token
    GET  /api/state           balance + open race + recent bets (Bearer token)
    POST /api/bet             {race_id,horse,chips} -> §3.1 (Bearer token)

Behind TLS in production (§4.3, §8). Return 200 fast; Zeffy retries non-200.
"""
import os
import time
import pathlib

import db as DB
import webhook
import guest_api
import banker_api
from zeffy_client import ZeffyClient

try:
    from flask import Flask, request, jsonify, send_file
except ImportError:  # keep import-time failure friendly for the test runner
    Flask = None

HERE = pathlib.Path(__file__).parent


def _bearer(req):
    h = req.headers.get("Authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


def create_app():
    if Flask is None:
        raise RuntimeError("Flask is not installed. `pip install flask` to serve.")
    # On a hosted deploy the database starts empty; create tables if missing.
    try:
        if DB.ensure_schema():
            print("Initialized database schema.")
    except Exception as e:  # noqa: BLE001
        print(f"Schema check skipped: {e}")
    app = Flask(__name__)
    zeffy = ZeffyClient()
    webhook_secret = os.environ.get("ZEFFY_WEBHOOK_SECRET")

    # ---- Zeffy webhook (spec §4.2/§4.3) -----------------------------------
    @app.post("/webhooks/zeffy")
    def zeffy_webhook():
        conn = DB.connect()
        try:
            status, payload = webhook.process_event(
                conn, zeffy, request.get_data(), dict(request.headers), webhook_secret
            )
        finally:
            conn.close()
        return jsonify(payload), status

    # ---- guest web app (spec §6) ------------------------------------------
    @app.get("/")
    def guest_page():
        return send_file(HERE / "guest.html")

    @app.post("/api/claim")
    def api_claim():
        body = request.get_json(silent=True) or {}
        conn = DB.connect()
        try:
            res = guest_api.claim(conn, email=body.get("email"), code=body.get("code"))
        finally:
            conn.close()
        return jsonify(res), (200 if res.get("ok") else 401)

    @app.get("/api/state")
    def api_state():
        conn = DB.connect()
        try:
            res = guest_api.state(conn, _bearer(request), now=int(time.time()))
        finally:
            conn.close()
        return jsonify(res), (200 if res.get("ok") else 401)

    @app.post("/api/bet")
    def api_bet():
        body = request.get_json(silent=True) or {}
        conn = DB.connect()
        try:
            res = guest_api.place_bet(
                conn, _bearer(request), body.get("race_id"), body.get("horse"),
                int(body.get("chips", 1)), now=int(time.time()),
            )
        finally:
            conn.close()
        code = 200 if res.get("ok") else (401 if res.get("reason") == "not signed in" else 400)
        return jsonify(res), code

    # ---- banker console (spec §7) -----------------------------------------
    @app.get("/banker")
    def banker_page():
        return send_file(HERE / "banker_console.html")

    @app.post("/banker/login")
    def banker_login():
        body = request.get_json(silent=True) or {}
        return jsonify(banker_api.login(body.get("password")))

    def _bank_call(fn):
        conn = DB.connect()
        try:
            res = fn(conn, _bearer(request))
        finally:
            conn.close()
        code = 200 if res.get("ok") else (
            401 if str(res.get("reason", "")).endswith("banker") else 400)
        return jsonify(res), code

    @app.get("/banker/state")
    def banker_state():
        return _bank_call(lambda c, t: banker_api.dashboard(c, t, now=int(time.time())))

    @app.post("/banker/race/schedule")
    def banker_schedule():
        b = request.get_json(silent=True) or {}
        return _bank_call(lambda c, t: banker_api.schedule_race(
            c, t, b.get("race_id"), b.get("ordinal"), b.get("name"),
            b.get("horses"), b.get("planned_at")))

    @app.post("/banker/race/open")
    def banker_open():
        b = request.get_json(silent=True) or {}
        return _bank_call(lambda c, t: banker_api.open_race(
            c, t, b.get("race_id"), now=int(time.time()),
            window_secs=b.get("window_secs"), closes_at=b.get("closes_at")))

    @app.post("/banker/race/extend")
    def banker_extend():
        b = request.get_json(silent=True) or {}
        return _bank_call(lambda c, t: banker_api.extend_race(
            c, t, b.get("race_id"), b.get("new_closes_at")))

    @app.post("/banker/race/lock")
    def banker_lock():
        b = request.get_json(silent=True) or {}
        return _bank_call(lambda c, t: banker_api.lock_race(
            c, t, b.get("race_id"), now=int(time.time())))

    @app.post("/banker/race/settle")
    def banker_settle():
        b = request.get_json(silent=True) or {}
        return _bank_call(lambda c, t: banker_api.settle_race(
            c, t, b.get("race_id"), b.get("winning_horse")))

    @app.post("/banker/guest/find")
    def banker_find():
        b = request.get_json(silent=True) or {}
        return _bank_call(lambda c, t: banker_api.find_guests(c, t, b.get("query")))

    @app.post("/banker/guest")
    def banker_guest():
        b = request.get_json(silent=True) or {}
        return _bank_call(lambda c, t: banker_api.guest_detail(c, t, b.get("account_id")))

    @app.post("/banker/cash/topup")
    def banker_topup():
        b = request.get_json(silent=True) or {}
        return _bank_call(lambda c, t: banker_api.cash_topup(
            c, t, b.get("account_id"), int(b.get("cents", 0))))

    @app.post("/banker/cash/cashout")
    def banker_cashout():
        b = request.get_json(silent=True) or {}
        return _bank_call(lambda c, t: banker_api.cash_out(
            c, t, b.get("account_id"), int(b.get("cents", 0))))

    @app.post("/banker/bet/void")
    def banker_void():
        b = request.get_json(silent=True) or {}
        return _bank_call(lambda c, t: banker_api.void_bet(c, t, int(b.get("bet_id", 0))))

    @app.post("/banker/claimcode")
    def banker_claimcode():
        b = request.get_json(silent=True) or {}
        return _bank_call(lambda c, t: banker_api.issue_claim_code(c, t, b.get("account_id")))

    @app.post("/banker/reset")
    def banker_reset():
        return _bank_call(lambda c, t: banker_api.reset_all(c, t))

    @app.post("/banker/guest/new")
    def banker_new_guest():
        b = request.get_json(silent=True) or {}
        dollars = b.get("initial_dollars") or 0
        cents = int(round(float(dollars) * 100)) if dollars else 0
        return _bank_call(lambda c, t: banker_api.new_guest(
            c, t, b.get("label"), b.get("email"), cents))

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    return app


if __name__ == "__main__":
    create_app().run(port=int(os.environ.get("PORT", 5000)))
