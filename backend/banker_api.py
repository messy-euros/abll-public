"""
Banker console API core (spec §7). Framework-agnostic; app.py exposes it as
routes and serves the console page. Everything here except `login` requires a
valid banker session token, so a guest phone can never reach these controls.

The console does the money-touching, human-in-the-loop work: race control
(schedule / open / extend / lock / settle), the cash desk (top-up and cash-out),
voiding a genuine mistake bet, looking a guest up, and watching the always-on
reconciliation. Guests can't edit bets; a banker can void one here.
"""
from __future__ import annotations
import os
import time

import db as DB
import banker as BANK
import sessions

BANKER_PASSWORD = os.environ.get("BANKER_PASSWORD", "let-me-in")   # set a real one
_TTL = int(os.environ.get("BANKER_SESSION_TTL", "43200"))          # 12h


# ---- auth -----------------------------------------------------------------
def login(password):
    if not password or password != BANKER_PASSWORD:
        return {"ok": False, "reason": "wrong password"}
    return {"ok": True, "token": sessions.issue("banker", ttl=_TTL, role="banker")}


def is_banker(token):
    _subject, role = sessions.verify_role(token)
    return role == "banker"


def _guard(token):
    return None if is_banker(token) else {"ok": False, "reason": "not signed in as banker"}


# ---- dashboard (race card + reconciliation) -------------------------------
def dashboard(conn, token, now=None):
    g = _guard(token)
    if g:
        return g
    if now is None:
        now = int(time.time())
    return {"ok": True, "now": now,
            "card": BANK.race_card(conn, now), "recon": BANK.reconciliation(conn)}


# ---- race control ---------------------------------------------------------
def schedule_race(conn, token, race_id, ordinal, name=None, horses=None, planned_at=None):
    g = _guard(token)
    if g:
        return g
    BANK.schedule_race(conn, race_id, ordinal, name, horses, planned_at)
    return {"ok": True}


def open_race(conn, token, race_id, now=None, window_secs=None, closes_at=None):
    g = _guard(token)
    if g:
        return g
    BANK.open_race_now(conn, race_id, now if now is not None else int(time.time()),
                       window_secs, closes_at)
    return {"ok": True}


def extend_race(conn, token, race_id, new_closes_at):
    g = _guard(token)
    if g:
        return g
    BANK.extend_race(conn, race_id, new_closes_at)
    return {"ok": True}


def lock_race(conn, token, race_id, now=None):
    g = _guard(token)
    if g:
        return g
    BANK.lock_race_now(conn, race_id, now if now is not None else int(time.time()))
    return {"ok": True}


def settle_race(conn, token, race_id, winning_horse):
    g = _guard(token)
    if g:
        return g
    if not winning_horse:
        return {"ok": False, "reason": "pick a winning horse"}
    return BANK.settle_race(conn, race_id, winning_horse)


# ---- cash desk ------------------------------------------------------------
def find_guests(conn, token, query):
    g = _guard(token)
    if g:
        return g
    return {"ok": True, "results": DB.find_accounts(conn, query)}


def guest_detail(conn, token, account_id):
    g = _guard(token)
    if g:
        return g
    s = DB.account_summary(conn, account_id)
    return {"ok": True, "guest": s} if s else {"ok": False, "reason": "no such guest"}


def cash_topup(conn, token, account_id, cents):
    g = _guard(token)
    if g:
        return g
    if not (isinstance(cents, int) and cents > 0):
        return {"ok": False, "reason": "invalid amount"}
    DB.topup_account(conn, account_id, cents, "cash")
    return {"ok": True, "balance_cents": DB.balance(conn, account_id)}


def cash_out(conn, token, account_id, cents):
    g = _guard(token)
    if g:
        return g
    return DB.cashout(conn, account_id, cents)


def void_bet(conn, token, bet_id):
    g = _guard(token)
    if g:
        return g
    return DB.void_bet(conn, bet_id)


def issue_claim_code(conn, token, account_id):
    g = _guard(token)
    if g:
        return g
    return {"ok": True, "code": DB.issue_claim_code(conn, account_id)}


def new_guest(conn, token, label, email=None, initial_cents=0):
    g = _guard(token)
    if g:
        return g
    if not (label and label.strip()):
        return {"ok": False, "reason": "name required"}
    guest = DB.create_guest(conn, label.strip(), email,
                            initial_cents if isinstance(initial_cents, int) else 0)
    return {"ok": True, "guest": guest}
