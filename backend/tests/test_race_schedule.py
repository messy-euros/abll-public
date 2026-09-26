"""RED suite — race schedule, timing, and the winner announcement (spec §6/§7).
Defines the behavior the banker console + card must have. All red for the right
reason: banker.py is stubbed. The green build makes these pass, then this joins
run_all.

Concepts under test:
  * a light, advisory schedule (scheduled -> live -> closed -> settled)
  * server-clock countdowns; the txn enforces close, banker can extend/lock
  * greying: past/live/upcoming come from a DERIVED card status
  * winner announcement: settle stores pot/house; guests see their result
  * always-on reconciliation (§7)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ok, eq, D  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as DB  # noqa: E402
import banker as B  # noqa: E402

HORSES = [{"number": "1", "name": "Comet"}, {"number": "3", "name": "Biscuit"},
          {"number": "7", "name": "Lightning"}]


def _fund(c, who, cents):
    DB.topup_identity(c, {"contactId": who, "email": f"{who}@x.com", "label": who},
                      cents, "zeffy", f"seed-{who}")
    return DB.lookup_identity(c, who, None)


def _status(c, race_id, now):
    card = B.race_card(c, now)
    return next(r for r in card["races"] if r["race_id"] == race_id)["status"]


# --- schedule / card -------------------------------------------------------
def _scheduled_is_upcoming_not_bettable(c):
    B.schedule_race(c, "R1", ordinal=1, name="Race 1", horses=HORSES, planned_at=1000)
    eq(_status(c, "R1", now=1), "upcoming", "scheduled race shows as upcoming")
    _fund(c, "a", D(50))
    r = DB.place_bet(c, DB.lookup_identity(c, "a", None), "R1", "3", D(10), 1)
    ok(not r["ok"], "a scheduled (unopened) race must not accept bets")


def _card_is_ordered_with_derived_status(c):
    B.schedule_race(c, "R1", ordinal=1, name="Race 1", horses=HORSES)
    B.schedule_race(c, "R2", ordinal=2, name="Race 2", horses=HORSES)
    B.open_race_now(c, "R1", now=10, closes_at=100)
    card = B.race_card(c, now=50)
    eq([r["race_id"] for r in card["races"]], ["R1", "R2"], "ordered by ordinal")
    eq([r["status"] for r in card["races"]], ["live", "upcoming"], "derived statuses")


def _card_reports_server_now_and_close(c):
    B.schedule_race(c, "R1", ordinal=1, horses=HORSES)
    B.open_race_now(c, "R1", now=10, closes_at=100)
    card = B.race_card(c, now=42)
    eq(card["server_now"], 42, "server clock returned so phones count down honestly")
    live = card["races"][0]
    eq(live["closes_at"], 100, "close time exposed for the countdown")


# --- opening / closing / override ------------------------------------------
def _open_makes_it_live_and_bettable(c):
    B.schedule_race(c, "R1", ordinal=1, horses=HORSES)
    B.open_race_now(c, "R1", now=10, closes_at=100)
    eq(_status(c, "R1", now=50), "live", "open race is live")
    a = _fund(c, "a", D(50))
    ok(DB.place_bet(c, a, "R1", "3", D(10), 50)["ok"], "bet accepted while live")


def _closes_at_is_enforced(c):
    B.schedule_race(c, "R1", ordinal=1, horses=HORSES)
    B.open_race_now(c, "R1", now=10, closes_at=100)
    a = _fund(c, "a", D(50))
    ok(not DB.place_bet(c, a, "R1", "3", D(10), 150)["ok"], "bet after close rejected")
    eq(_status(c, "R1", now=150), "closed", "past close shows as closed (greyed)")


def _extend_reopens_the_window(c):
    B.schedule_race(c, "R1", ordinal=1, horses=HORSES)
    B.open_race_now(c, "R1", now=10, closes_at=100)
    a = _fund(c, "a", D(50))
    ok(not DB.place_bet(c, a, "R1", "3", D(10), 120)["ok"], "just missed at t=120")
    B.extend_race(c, "R1", new_closes_at=200)          # banker: room ran late
    ok(DB.place_bet(c, a, "R1", "3", D(10), 120)["ok"], "extend lets the bet through")


def _lock_now_closes_early(c):
    B.schedule_race(c, "R1", ordinal=1, horses=HORSES)
    B.open_race_now(c, "R1", now=10, closes_at=100)
    B.lock_race_now(c, "R1", now=40)                   # banker closes early
    a = _fund(c, "a", D(50))
    ok(not DB.place_bet(c, a, "R1", "3", D(10), 50)["ok"], "locked early rejects bets")
    eq(_status(c, "R1", now=50), "closed", "shows closed after early lock")


# --- settle + announce -----------------------------------------------------
def _settle_stores_pot_and_house(c):
    B.schedule_race(c, "R1", ordinal=1, horses=HORSES)
    B.open_race_now(c, "R1", now=10, closes_at=100)
    a, b = _fund(c, "a", D(100)), _fund(c, "b", D(100))
    DB.place_bet(c, a, "R1", "3", D(30), 50)
    DB.place_bet(c, b, "R1", "1", D(30), 50)           # b loses
    B.lock_race_now(c, "R1", now=110)
    summary = B.settle_race(c, "R1", "3")
    row = B.race_card(c, now=200)["races"][0]
    eq(row["status"], "settled", "settled status")
    eq(row["winning_horse"], "3", "winner recorded")
    ok(row["pot_cents"] == D(60), "pot stored on the race")
    ok(row["house_cut_cents"] >= 0, "house cut stored for reconciliation")


def _guest_sees_their_result(c):
    B.schedule_race(c, "R1", ordinal=1, horses=HORSES)
    B.open_race_now(c, "R1", now=10, closes_at=100)
    a, b = _fund(c, "a", D(100)), _fund(c, "b", D(100))
    DB.place_bet(c, a, "R1", "3", D(30), 50)           # winner
    DB.place_bet(c, b, "R1", "1", D(30), 50)           # loser
    B.settle_race(c, "R1", "3")
    ra = B.guest_result(c, a, "R1")
    ok(ra["settled"] and ra["won"] and ra["payout_cents"] > 0, "winner sees a payout")
    rb = B.guest_result(c, b, "R1")
    ok(rb["settled"] and not rb["won"] and rb["payout_cents"] == 0, "loser sees no win")


# --- reconciliation (§7) ---------------------------------------------------
def _reconciliation_balances_at_rest(c):
    a = _fund(c, "a", D(100))          # zeffy in
    DB.topup_identity(c, {"contactId": "a", "email": "a@x.com"}, D(50), "cash")  # cash in
    b = _fund(c, "b", D(100))
    B.schedule_race(c, "R1", ordinal=1, horses=HORSES)
    B.open_race_now(c, "R1", now=10, closes_at=100)
    DB.place_bet(c, a, "R1", "3", D(50), 50)
    DB.place_bet(c, b, "R1", "1", D(50), 50)
    B.settle_race(c, "R1", "3")
    rec = B.reconciliation(c)
    eq(rec["credit_issued"], D(250), "all top-ups counted")
    ok(rec["balanced"], "credit_issued == outstanding + house + in-play (at rest)")


SUITE = [
 ("scheduled race is upcoming and not bettable", _scheduled_is_upcoming_not_bettable),
 ("card is ordered by ordinal with derived status", _card_is_ordered_with_derived_status),
 ("card reports server_now and close time for countdowns", _card_reports_server_now_and_close),
 ("opening makes a race live and bettable", _open_makes_it_live_and_bettable),
 ("close time is enforced; past-close shows closed", _closes_at_is_enforced),
 ("extend reopens the window for a late room", _extend_reopens_the_window),
 ("lock closes betting early", _lock_now_closes_early),
 ("settle stores pot + house on the race", _settle_stores_pot_and_house),
 ("guest sees their own win/loss result", _guest_sees_their_result),
 ("reconciliation balances at rest", _reconciliation_balances_at_rest),
]


if __name__ == "__main__":
    from harness import run_suite
    g, r, e = run_suite("RED - race schedule / timing / announce (spec §6/§7)", SUITE)
    sys.exit(1 if (r or e) else 0)
