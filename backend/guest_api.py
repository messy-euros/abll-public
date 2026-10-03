"""
Guest web-app API core (spec §6). Framework-agnostic so it's testable without
HTTP; app.py exposes it as routes and serves the phone page.

The whole point (§6): betting is fully parallel because there's no shared human
write path. Each phone hits place_bet directly and the atomic §3.1 transaction is
the only arbiter — late taps and overspends are rejected by the database, never
trusted from the client.
"""
from __future__ import annotations

import db as DB
import banker as BANK
import sessions

CHIP_CENTS = 1000  # a Betting Ticket is $10 (spec §2)


# ---- claim: email or check-in code -> a signed session (spec §6.2, §8) -----
def claim(conn, email=None, code=None):
    """Resolve a guest to their account and issue a session token. Returns
    {'ok':True,'token':..,'account_id':..} or {'ok':False,'reason':..}."""
    account_id = None
    if code:
        account_id = DB.account_for_claim_code(conn, code)
        if account_id is None:
            return {"ok": False, "reason": "invalid claim code"}
    elif email:
        account_id = DB.lookup_identity(conn, None, email)
        if account_id is None:
            # weakest path (§8): only works for a known email; unknown -> no guess
            return {"ok": False, "reason": "email not found — see the bank to check in"}
    else:
        return {"ok": False, "reason": "email or claim code required"}

    return {"ok": True, "token": sessions.issue(account_id), "account_id": account_id}


def authed_account(token):
    """The account_id a request is authorised for, or None."""
    return sessions.verify(token)


# ---- state: what the phone renders (spec §6.3) ----------------------------
def _my_stake(conn, account_id, race):
    """This guest's live (non-voided) bets on a race, rolled up per horse with
    the horse's name, for the 'your bets this race' summary."""
    rows = conn.execute(
        "SELECT horse, SUM(cents) FROM bets WHERE account_id=%s AND race_id=%s "
        "AND NOT voided GROUP BY horse",
        (account_id, race["race_id"]),
    ).fetchall()
    names = {str(h.get("number")): h.get("name") for h in (race.get("horses") or [])}
    return [{"horse": h, "name": names.get(str(h)), "cents": int(c)} for h, c in rows]


def state(conn, token, now=None):
    import time as _t
    account_id = authed_account(token)
    if account_id is None:
        return {"ok": False, "reason": "not signed in"}
    if now is None:
        now = int(_t.time())
    card = BANK.race_card(conn, now)

    races, live = [], None
    for r in card["races"]:
        stake = _my_stake(conn, account_id, r)
        bettable = r["status"] == "live"
        result = None
        if r["status"] == "settled" and stake:
            result = BANK.guest_result(conn, account_id, r["race_id"])
        rr = {**r, "open_for_bets": bettable, "your_stake": stake, "result": result}
        races.append(rr)
        if bettable and live is None:
            live = rr

    return {
        "ok": True,
        "account_id": account_id,
        "balance_cents": DB.balance(conn, account_id),
        "server_now": now,
        "races": races,
        "race": live,                       # the current live race (compat)
        "recent_bets": DB.recent_bets(conn, account_id),
    }


# ---- bet: tap a horse, N chips (spec §6.3 -> §3.1) ------------------------
def place_bet(conn, token, race_id, horse, chips=1, now=None):
    """Place a bet of `chips` x $10 on `horse`. All the safety is in §3.1:
    overspend, double-spend, and betting after post are rejected there."""
    account_id = authed_account(token)
    if account_id is None:
        return {"ok": False, "reason": "not signed in"}
    if not (isinstance(chips, int) and chips > 0):
        return {"ok": False, "reason": "chips must be a positive whole number"}

    cents = chips * CHIP_CENTS
    result = DB.place_bet(conn, account_id, race_id, horse, cents, now)
    if result.get("ok"):
        result["balance_cents"] = DB.balance(conn, account_id)
    return result
