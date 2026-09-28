"""
Banker console + race-card core (spec §6 timing, §7).

A very light, advisory schedule that a human is expected to break. The ONLY
automatic behavior is the bet transaction refusing bets past a race's close time
— and even that is overridable live (extend / lock / open). Settle is manual.

Race lifecycle:
    scheduled ─(open_race_now)─▶ open ─(now>closes_at, or lock_race_now)─▶
    closed ─(settle_race)─▶ settled

`races.state` stays scheduled|open|locked|settled; "closed" and "live" are
DERIVED from the clock in race_card, so no sweeper flips rows.
"""
from __future__ import annotations
import json as _json
import time

import db as DB


# ---- race scheduling & control (banker) -----------------------------------
def schedule_race(conn, race_id, ordinal, name=None, horses=None, planned_at=None):
    """Create a race in 'scheduled' state: on the card as upcoming, not bettable."""
    with conn.transaction():
        conn.execute(
            "INSERT INTO races(race_id,name,ordinal,state,planned_at,horses) "
            "VALUES(%s,%s,%s,'scheduled',%s,%s) ON CONFLICT (race_id) DO NOTHING",
            (race_id, name, ordinal, planned_at, _json.dumps(horses or [])),
        )


def open_race_now(conn, race_id, now, window_secs=None, closes_at=None):
    """Open betting immediately. If closes_at (or window_secs) is given, betting
    auto-closes then (an enforced countdown); otherwise it stays open until the
    banker locks it. Sets opens_at=now."""
    if closes_at is None and window_secs is not None:
        closes_at = now + window_secs
    with conn.transaction():
        conn.execute(
            "UPDATE races SET state='open',opens_at=%s,closes_at=%s WHERE race_id=%s",
            (now, closes_at, race_id),
        )


def extend_race(conn, race_id, new_closes_at):
    """Banker override for a late room: move an open race's close time. Only
    valid while the race is open."""
    with conn.transaction():
        conn.execute(
            "UPDATE races SET closes_at=%s WHERE race_id=%s AND state='open'",
            (new_closes_at, race_id),
        )


def lock_race_now(conn, race_id, now):
    """Close betting immediately, before any scheduled close."""
    with conn.transaction():
        conn.execute("UPDATE races SET state='locked' WHERE race_id=%s", (race_id,))


def settle_race(conn, race_id, winning_horse):
    """Manual: enter the winning horse, run §3.3 (which stores pot_cents +
    house_cut_cents + winning_horse on the race), return the settle summary."""
    return DB.settle(conn, race_id, winning_horse)


# ---- the race card (guests and bankers both read this) --------------------
def _status(state, closes_at, now):
    if state == "settled":
        return "settled"
    if state == "locked":
        return "closed"
    if state == "open":
        if closes_at is not None and now is not None and now > closes_at:
            return "closed"
        return "live"
    return "upcoming"  # scheduled


def race_card(conn, now=None):
    """{'server_now': int, 'races': [...]} ordered by ordinal, each with a
    DERIVED status (upcoming|live|closed|settled) plus times, horses, winner,
    pot_cents, house_cut_cents. server_now lets a phone count down against the
    SERVER clock, not its own."""
    if now is None:
        now = int(time.time())
    rows = conn.execute(
        "SELECT race_id,name,ordinal,state,planned_at,opens_at,closes_at,"
        "winning_horse,horses,pot_cents,house_cut_cents FROM races "
        "ORDER BY ordinal NULLS LAST, race_id"
    ).fetchall()
    races = []
    for (rid, name, ordinal, state, planned_at, opens_at, closes_at,
         winner, horses, pot, house) in rows:
        races.append({
            "race_id": rid, "name": name, "ordinal": ordinal,
            "status": _status(state, closes_at, now),
            "planned_at": planned_at, "opens_at": opens_at, "closes_at": closes_at,
            "horses": horses, "winning_horse": winner,
            "pot_cents": pot, "house_cut_cents": house,
        })
    return {"server_now": now, "races": races}


# ---- per-guest result (drives the winner reveal on the phone) -------------
def guest_result(conn, account_id, race_id):
    """For a settled race: {'settled':bool,'winning_horse':..,'won':bool,
    'payout_cents':int} for this account."""
    row = conn.execute(
        "SELECT state,winning_horse FROM races WHERE race_id=%s", (race_id,)
    ).fetchone()
    if row is None or row[0] != "settled":
        return {"settled": False, "won": False, "payout_cents": 0, "winning_horse": None}
    payout = conn.execute(
        "SELECT COALESCE(SUM(delta_cents),0) FROM ledger_entries "
        "WHERE type='payout' AND account_id=%s AND race_id=%s",
        (account_id, race_id),
    ).fetchone()[0]
    return {"settled": True, "winning_horse": row[1],
            "won": payout > 0, "payout_cents": int(payout)}


# ---- always-on reconciliation (spec §7) -----------------------------------
def reconciliation(conn):
    """The running invariant one volunteer watches all night. Money in (top-ups)
    must equal money still on accounts + money the house has taken + chips
    currently in play + cash handed back out:

        credit_issued == outstanding + house_cut + staked_in_open + cashed_out

    Holds continuously, not just at rest. Drift => investigate now."""
    one = lambda q: conn.execute(q).fetchone()[0]  # noqa: E731
    cash_in = one("SELECT COALESCE(SUM(delta_cents),0) FROM ledger_entries "
                  "WHERE type='topup' AND source='cash'")
    zeffy_in = one("SELECT COALESCE(SUM(delta_cents),0) FROM ledger_entries "
                   "WHERE type='topup' AND source='zeffy'")
    credit_issued = cash_in + zeffy_in
    cashed_out = -one("SELECT COALESCE(SUM(delta_cents),0) FROM ledger_entries "
                      "WHERE type='cashout'")
    house_cut = one("SELECT COALESCE(SUM(house_cut_cents),0) FROM races "
                    "WHERE state='settled'")
    outstanding = one("SELECT COALESCE(SUM(balance_cents),0) FROM balances")
    staked_in_open = -one(
        "SELECT COALESCE(SUM(b.delta_cents),0) FROM ledger_entries b "
        "JOIN races r ON r.race_id=b.race_id "
        "WHERE b.type='bet' AND r.state<>'settled'")
    balanced = credit_issued == outstanding + house_cut + staked_in_open + cashed_out
    return {
        "cash_in": cash_in, "zeffy_in": zeffy_in, "credit_issued": credit_issued,
        "outstanding_balances": outstanding, "house_cut": house_cut,
        "staked_in_open_races": staked_in_open, "cashed_out": cashed_out,
        "balanced": balanced,
    }
