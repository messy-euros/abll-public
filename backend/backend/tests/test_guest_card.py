"""RED suite — the guest phone's state contract (test-session feedback).
Drives the phone fixes: show the whole card, countdown off the server clock,
per-race stakes, and a winner result when a race settles. All red until
guest_api.state returns the richer shape; then the phone renders from it."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ok, eq, D  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as DB  # noqa: E402
import banker as BANK  # noqa: E402
import guest_api as G  # noqa: E402

HORSES = [{"number": "2", "name": "Blitzen"}, {"number": "3", "name": "Comet"},
          {"number": "4", "name": "Donner"}]


def _guest(c, chips):
    DB.topup_identity(c, {"contactId": "amy", "email": "amy@x.com", "label": "Amy"},
                      chips, "zeffy", "seed")
    return DB.lookup_identity(c, "amy", None), G.claim(c, email="amy@x.com")["token"]


def _race(r, rid):
    return next((x for x in r["races"] if x["race_id"] == rid), None)


def _server_now(c):
    _a, t = _guest(c, D(50))
    st = G.state(c, t, now=500)
    ok("server_now" in st, "state exposes server_now so phones count down honestly")
    eq(st["server_now"], 500, "server_now reflects the server clock")


def _lists_all_races(c):
    _a, t = _guest(c, D(50))
    BANK.schedule_race(c, "R1", 1, name="Race 1", horses=HORSES)
    BANK.schedule_race(c, "R2", 2, name="Race 2", horses=HORSES)
    BANK.schedule_race(c, "R3", 3, name="Race 3", horses=HORSES)
    BANK.open_race_now(c, "R1", now=10, closes_at=100)
    st = G.state(c, t, now=50)
    ok("races" in st, "state returns the whole card, not just one race")
    eq(len(st["races"]), 3, "all three races present")
    eq([r["status"] for r in st["races"]], ["live", "upcoming", "upcoming"], "statuses")


def _bettable_flag_and_horses(c):
    _a, t = _guest(c, D(50))
    BANK.schedule_race(c, "R1", 1, name="Race 1", horses=HORSES)
    BANK.open_race_now(c, "R1", now=10, closes_at=100)
    BANK.schedule_race(c, "R2", 2, name="Race 2", horses=HORSES)
    BANK.open_race_now(c, "R2", now=10, closes_at=100)
    BANK.lock_race_now(c, "R2", now=20)                 # closed
    st = G.state(c, t, now=50)
    r1, r2 = _race(st, "R1"), _race(st, "R2")
    ok(r1 and r1.get("open_for_bets") is True, "live race is bettable")
    ok(len(r1.get("horses") or []) == 3, "horses present for tapping")
    ok("closes_at" in r1, "close time present for the countdown")
    ok(r2 and r2.get("open_for_bets") is False, "closed race is NOT bettable (no bet UI)")


def _your_stake_per_race(c):
    a, t = _guest(c, D(100))
    BANK.schedule_race(c, "R1", 1, name="Race 1", horses=HORSES)
    BANK.open_race_now(c, "R1", now=10, closes_at=100)
    G.place_bet(c, t, "R1", "3", 2, now=50)             # $20 on Comet
    G.place_bet(c, t, "R1", "4", 1, now=50)             # $10 on Donner
    st = G.state(c, t, now=60)
    stake = _race(st, "R1").get("your_stake") or []
    by = {s["horse"]: s for s in stake}
    eq(by["3"]["cents"], D(20), "rolls up $20 on 3")
    eq(by["4"]["cents"], D(10), "rolls up $10 on 4")
    ok(by["3"].get("name") == "Comet", "stake carries the horse name for display")


def _settled_result(c):
    a, t = _guest(c, D(100))
    BANK.schedule_race(c, "R1", 1, name="Race 1", horses=HORSES)
    BANK.open_race_now(c, "R1", now=10, closes_at=100)
    G.place_bet(c, t, "R1", "4", 3, now=50)             # $30 on Donner
    BANK.settle_race(c, "R1", "4")                      # Donner wins
    st = G.state(c, t, now=200)
    res = _race(st, "R1").get("result")
    ok(res is not None, "settled race the guest bet on carries a result for the pop-up")
    ok(res.get("won") and res.get("payout_cents", 0) > 0, "winner sees a payout")
    eq(res.get("winning_horse"), "4", "result names the winning horse")


SUITE = [
 ("state exposes server_now for honest countdowns", _server_now),
 ("state lists the whole race card with statuses", _lists_all_races),
 ("each race carries open_for_bets, horses, closes_at", _bettable_flag_and_horses),
 ("each race carries the guest's own stake, by horse, with names", _your_stake_per_race),
 ("settled race carries a win/loss result for the reveal", _settled_result),
]


if __name__ == "__main__":
    from harness import run_suite
    g, r, e = run_suite("RED - guest phone card contract (feedback)", SUITE)
    sys.exit(1 if (r or e) else 0)
