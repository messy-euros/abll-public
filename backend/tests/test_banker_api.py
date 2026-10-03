"""NEW — banker console API (spec §7). Auth gate, race control end-to-end, the
cash desk, the bet-void action, and the guarantee that ANY horse can be the
winner (not just the first). All the money guards remain in the ledger."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ok, eq, D  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as DB  # noqa: E402
import banker as BANK  # noqa: E402
import banker_api as API  # noqa: E402

PW = API.BANKER_PASSWORD
HORSES = [{"number": "1", "name": "Comet"}, {"number": "3", "name": "Biscuit"},
          {"number": "7", "name": "Lightning"}]


def tok():
    return API.login(PW)["token"]


def _fund(c, who, cents):
    DB.topup_identity(c, {"contactId": who, "email": f"{who}@x.com", "label": who.title()},
                      cents, "zeffy", f"seed-{who}")
    return DB.lookup_identity(c, who, None)


def _login(c):
    ok(not API.login("nope")["ok"], "wrong password rejected")
    r = API.login(PW)
    ok(r["ok"] and r["token"], "right password issues a token")
    ok(API.is_banker(r["token"]), "token is a banker token")


def _auth_gate(c):
    ok(not API.dashboard(c, None)["ok"], "no token -> no dashboard")
    ok(not API.dashboard(c, "garbage.token")["ok"], "bad token -> no dashboard")
    ok(not API.settle_race(c, "guesttoken", "R", "3").get("ok"), "non-banker can't settle")
    ok(API.dashboard(c, tok())["ok"], "banker token -> dashboard")


def _dashboard_shape(c):
    t = tok()
    BANK.schedule_race(c, "R1", 1, name="Race 1", horses=HORSES)
    d = API.dashboard(c, t, now=5)
    ok("card" in d and "recon" in d, "dashboard has card + reconciliation")
    eq(d["card"]["races"][0]["status"], "upcoming", "scheduled race shows upcoming")


def _race_control_end_to_end(c):
    t = tok()
    a = _fund(c, "amy", D(50))
    API.schedule_race(c, t, "R1", 1, name="Race 1", horses=HORSES)
    API.open_race(c, t, "R1", now=10, closes_at=100)
    ok(DB.place_bet(c, a, "R1", "3", D(20), 50)["ok"], "guest can bet once open")
    API.extend_race(c, t, "R1", new_closes_at=200)          # room ran late
    ok(DB.place_bet(c, a, "R1", "3", D(10), 150)["ok"], "extend let a later bet through")
    API.lock_race(c, t, "R1", now=210)
    ok(not DB.place_bet(c, a, "R1", "3", D(10), 220)["ok"], "locked -> no more bets")
    s = API.settle_race(c, t, "R1", "3")
    ok(s["ok"] and s["pot"] == D(30), "settle returns the pot")


def _any_horse_can_win(c):
    """Regression guard for the sketch's 'first horse only' quirk."""
    t = tok()
    a = _fund(c, "amy", D(50))
    API.schedule_race(c, t, "R1", 1, horses=HORSES)
    API.open_race(c, t, "R1", now=10, closes_at=100)
    DB.place_bet(c, a, "R1", "7", D(20), 50)                # bet the LAST horse
    API.settle_race(c, t, "R1", "7")                        # settle on the LAST horse
    res = BANK.guest_result(c, a, "R1")
    ok(res["won"] and res["payout_cents"] > 0, "winner on horse 7 is paid")


def _cash_desk(c):
    t = tok()
    a = _fund(c, "dave", D(20))
    found = API.find_guests(c, t, "dave")
    ok(any(g["account_id"] == a for g in found["results"]), "guest found by name/email")
    API.cash_topup(c, t, a, D(30))
    eq(DB.balance(c, a), D(50), "cash top-up applied")
    r = API.cash_out(c, t, a, D(15))
    ok(r["ok"], "cash-out accepted")
    eq(DB.balance(c, a), D(35), "cash-out reduced balance")
    ok(not API.cash_out(c, t, a, D(999))["ok"], "can't cash out more than balance")


def _void_bet(c):
    t = tok()
    a, b = _fund(c, "amy", D(50)), _fund(c, "ben", D(50))
    API.schedule_race(c, t, "R1", 1, horses=HORSES)
    API.open_race(c, t, "R1", now=10, closes_at=100)
    DB.place_bet(c, a, "R1", "3", D(20), 50)                # amy's mistake bet
    DB.place_bet(c, b, "R1", "3", D(10), 50)
    bet_id = DB.account_summary(c, a)["bets"][0]["bet_id"]
    r = API.void_bet(c, t, bet_id)
    ok(r["ok"] and r["refunded_cents"] == D(20), "void refunds the stake")
    eq(DB.balance(c, a), D(50), "amy's chips restored")
    ok(BANK.reconciliation(c)["balanced"], "books still balance after a void")
    s = API.settle_race(c, t, "R1", "3")
    eq(s["pot"], D(10), "voided bet excluded from the pot")


def _void_guards(c):
    t = tok()
    a = _fund(c, "amy", D(50))
    API.schedule_race(c, t, "R1", 1, horses=HORSES)
    API.open_race(c, t, "R1", now=10, closes_at=100)
    DB.place_bet(c, a, "R1", "3", D(20), 50)
    bet_id = DB.account_summary(c, a)["bets"][0]["bet_id"]
    API.settle_race(c, t, "R1", "3")
    ok(not API.void_bet(c, t, bet_id)["ok"], "can't void after settle")


def _reset_gated(c):
    t = tok()
    _fund(c, "amy", D(50))
    API.schedule_race(c, t, "R1", 1, horses=HORSES)
    os.environ.pop("ALLOW_RESET", None)
    ok(not API.reset_all(c, t)["ok"], "reset refused when ALLOW_RESET is off")
    ok(len(DB.accounts(c)) >= 1, "nothing wiped while disabled")
    os.environ["ALLOW_RESET"] = "true"
    try:
        ok(not API.reset_all(c, "not-a-banker").get("ok"), "non-banker can't reset")
        ok(API.reset_all(c, t)["ok"], "reset works when enabled for a banker")
        eq(len(DB.accounts(c)), 0, "accounts wiped")
        eq(len(BANK.race_card(c)["races"]), 0, "races wiped")
    finally:
        os.environ.pop("ALLOW_RESET", None)


def _new_guest(c):
    t = tok()
    r = API.new_guest(c, t, "Nora Newcomer", email="nora@x.com", initial_cents=D(30))
    ok(r["ok"], "guest created")
    g = r["guest"]
    ok(g["claim_code"], "gets a claim code")
    eq(g["balance_cents"], D(30), "starting chips loaded")
    # claimable by that code, and findable by the banker
    import guest_api
    cl = guest_api.claim(c, code=g["claim_code"])
    ok(cl["ok"] and cl["account_id"] == g["account_id"], "guest can claim with the code")
    ok(not API.new_guest(c, t, "")["ok"], "blank name rejected")
    ok(not API.new_guest(c, "not-a-banker", "X")["ok"], "non-banker can't create")


SUITE = [
 ("login: wrong password rejected, right issues a banker token", _login),
 ("auth gate: only a banker token reaches the controls", _auth_gate),
 ("dashboard returns the race card and reconciliation", _dashboard_shape),
 ("race control: schedule -> open -> extend -> lock -> settle", _race_control_end_to_end),
 ("any horse can be the winner, not just the first", _any_horse_can_win),
 ("cash desk: find, top up, cash out (guarded)", _cash_desk),
 ("void a mistake bet: refunded, excluded, still balanced", _void_bet),
 ("void is refused after settle", _void_guards),
 ("new guest: created, funded, claimable by code", _new_guest),
 ("reset is off by default, works only when enabled + banker", _reset_gated),
]


if __name__ == "__main__":
    from harness import run_suite
    g, r, e = run_suite("NEW - banker console API (spec §7)", SUITE)
    sys.exit(1 if (r or e) else 0)
