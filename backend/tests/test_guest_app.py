"""NEW — guest web app (spec §6, §8). Claim by email or check-in code, a signed
session bound to the account, and betting where every guard lives in the §3.1
transaction: overspend and late taps are rejected server-side, never trusted
from the phone. Winnings land back on settle so the guest can re-bet."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ok, eq, D  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as DB  # noqa: E402
import guest_api as G  # noqa: E402
import sessions  # noqa: E402

FUTURE = 10 ** 15
HORSES = [{"number": "1", "name": "Comet"}, {"number": "3", "name": "Biscuit"},
          {"number": "7", "name": "Lightning"}]


def _fund(c, contact, email, name, cents):
    """Register + fund an account the way pre-provisioning would."""
    DB.topup_identity(c, {"contactId": contact, "email": email, "label": name},
                      cents, "zeffy", f"seed-{contact}")
    return DB.lookup_identity(c, contact, email)


def _claim_email(c, email):
    return G.claim(c, email=email)["token"]


# --- claim / auth ----------------------------------------------------------
def _claim_by_email(c):
    _fund(c, "c1", "jane@example.com", "Jane", D(50))
    res = G.claim(c, email="Jane@Example.com ")     # messy case, still resolves
    ok(res["ok"], "claim succeeds")
    eq(G.state(c, res["token"])["balance_cents"], D(50), "state shows balance")


def _claim_by_code(c):
    acct = _fund(c, "c1", "jane@example.com", "Jane", D(20))
    code = DB.issue_claim_code(c, acct)
    res = G.claim(c, code=code.lower())             # case-insensitive
    ok(res["ok"], "claim by code succeeds")
    eq(res["account_id"], acct, "bound to the right account")


def _bad_claims_rejected(c):
    _fund(c, "c1", "jane@example.com", "Jane", D(20))
    ok(not G.claim(c, code="NOPE99")["ok"], "unknown code rejected")
    ok(not G.claim(c, email="stranger@example.com")["ok"], "unknown email rejected")
    ok(not G.claim(c)["ok"], "no email or code rejected")


def _forged_session_rejected(c):
    acct = _fund(c, "c1", "jane@example.com", "Jane", D(50))
    good = sessions.issue(acct)
    tampered = good[:-2] + ("aa" if good[-2:] != "aa" else "bb")
    eq(sessions.verify(tampered), None, "tampered signature rejected")
    r = G.place_bet(c, tampered, "R", "3", 1, now=1)
    ok(not r["ok"] and r["reason"] == "not signed in", "forged token can't bet")


def _session_binds_to_owner(c):
    a = _fund(c, "cA", "a@example.com", "A", D(30))
    _fund(c, "cB", "b@example.com", "B", D(30))
    DB.open_race(c, "R", FUTURE, horses=HORSES)
    tok_a = _claim_email(c, "a@example.com")
    G.place_bet(c, tok_a, "R", "3", 2, now=1)        # A bets $20
    eq(DB.balance(c, a), D(10), "A drawn down")
    eq(DB.balance_for_identity(c, {"contactId": "cB"}), D(30), "B untouched by A's token")


# --- betting (all safety in §3.1) ------------------------------------------
def _single_and_multi_chip(c):
    _fund(c, "c1", "jane@example.com", "Jane", D(100))
    DB.open_race(c, "R", FUTURE, horses=HORSES)
    tok = _claim_email(c, "jane@example.com")
    r1 = G.place_bet(c, tok, "R", "3", 1, now=1)
    ok(r1["ok"], "single chip accepted")
    eq(r1["balance_cents"], D(90), "one $10 chip debited")
    r2 = G.place_bet(c, tok, "R", "7", 3, now=1)
    ok(r2["ok"], "three chips accepted")
    eq(r2["balance_cents"], D(60), "three more chips ($30) debited")


def _overspend_rejected(c):
    _fund(c, "c1", "jane@example.com", "Jane", D(20))
    DB.open_race(c, "R", FUTURE, horses=HORSES)
    tok = _claim_email(c, "jane@example.com")
    r = G.place_bet(c, tok, "R", "3", 3, now=1)       # $30 on a $20 balance
    ok(not r["ok"], "over-balance bet rejected")
    eq(r["reason"], "insufficient balance", "reason surfaced for a friendly message")
    eq(DB.balance_for_identity(c, {"contactId": "c1"}), D(20), "balance untouched")


def _late_tap_rejected(c):
    _fund(c, "c1", "jane@example.com", "Jane", D(50))
    DB.open_race(c, "R", 100, horses=HORSES)          # posts at t=100
    tok = _claim_email(c, "jane@example.com")
    r = G.place_bet(c, tok, "R", "3", 1, now=200)     # tap after post
    ok(not r["ok"] and r["reason"] == "past post time", "late tap rejected by the txn")
    eq(DB.balance_for_identity(c, {"contactId": "c1"}), D(50), "no debit on a late tap")


def _winnings_then_rebet(c):
    a = _fund(c, "cA", "a@example.com", "A", D(100))
    _fund(c, "cB", "b@example.com", "B", D(100))
    DB.open_race(c, "R", FUTURE, horses=HORSES)
    tok = _claim_email(c, "a@example.com")
    G.place_bet(c, tok, "R", "3", 3, now=1)           # A: $30 on #3
    DB.place_bet(c, DB.lookup_identity(c, "cB", None), "R", "1", D(30), 1)  # B loses
    DB.settle(c, "R", "3")                            # #3 wins
    after = DB.balance(c, a)
    ok(after > D(70), "winnings credited back to A's chips")
    # a fresh open race lets A re-bet immediately with the winnings
    DB.open_race(c, "R2", FUTURE, horses=HORSES)
    st = G.state(c, tok)
    eq(st["race"]["race_id"], "R2", "state now shows the next open race")
    ok(G.place_bet(c, tok, "R2", "1", 1, now=1)["ok"], "can re-bet straight away")


def _state_shows_race_and_horses(c):
    _fund(c, "c1", "jane@example.com", "Jane", D(50))
    DB.open_race(c, "Derby", FUTURE, name="The Big Derby", horses=HORSES)
    st = G.state(c, _claim_email(c, "jane@example.com"), now=1)
    eq(st["race"]["name"], "The Big Derby", "race name shown")
    eq(len(st["race"]["horses"]), 3, "horses listed for tapping")
    ok(st["race"]["open_for_bets"], "open race is bettable")


SUITE = [
 ("claim by email (messy case) -> session with balance", _claim_by_email),
 ("claim by check-in code (case-insensitive) -> bound to account", _claim_by_code),
 ("bad code / unknown email / empty -> rejected", _bad_claims_rejected),
 ("forged or tampered session -> cannot bet", _forged_session_rejected),
 ("session binds to its owner -> can't touch another balance", _session_binds_to_owner),
 ("single and multi-chip bets debit correctly", _single_and_multi_chip),
 ("over-balance bet rejected server-side", _overspend_rejected),
 ("late tap after post rejected by the transaction", _late_tap_rejected),
 ("winnings credited on settle, then re-bet immediately", _winnings_then_rebet),
 ("state shows the open race and its horses", _state_shows_race_and_horses),
]


if __name__ == "__main__":
    from harness import run_suite
    g, r, e = run_suite("NEW - guest web app (spec §6/§8)", SUITE)
    sys.exit(1 if (r or e) else 0)
