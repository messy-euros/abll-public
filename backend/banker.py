"""
Banker console + race-card core (spec §6 timing, §7). RED PHASE — these are
stubs that document the intended contract and raise NotImplementedError so the
test suite is red for the right reason. The green build replaces each body.

Design (agreed): a very light, advisory schedule that a human is expected to
break. The ONLY automatic behavior is the bet transaction refusing bets past a
race's close time — and even that is overridable live (extend / lock / open).
Settle is always manual.

Race lifecycle:
    scheduled ──(banker opens)──▶ open ──(now > closes_at, or banker locks)──▶
    closed ──(banker enters winner)──▶ settled

`state` on the races row stays one of scheduled|open|locked|settled; "closed"
and "live" are DERIVED from the clock so there's no sweeper flipping rows.
"""
from __future__ import annotations


def _todo(name):
    raise NotImplementedError(f"{name}() not implemented")


# ---- race scheduling & control (banker) -----------------------------------
def schedule_race(conn, race_id, ordinal, name=None, horses=None, planned_at=None):
    """Create a race in 'scheduled' state: it appears on the card as upcoming
    with its ordinal and rough planned_at, but is NOT bettable yet."""
    _todo("schedule_race")


def open_race_now(conn, race_id, now, window_secs=None, closes_at=None):
    """Open betting immediately. If closes_at (or window_secs) is given, betting
    auto-closes then (a real, enforced countdown); otherwise it stays open until
    the banker locks it. Sets opens_at=now."""
    _todo("open_race_now")


def extend_race(conn, race_id, new_closes_at):
    """Banker override for a room running late: move an open race's close time.
    Every phone's countdown follows. Only valid while the race is open."""
    _todo("extend_race")


def lock_race_now(conn, race_id, now):
    """Close betting immediately, before any scheduled close. Irreversible for
    that race except by re-opening."""
    _todo("lock_race_now")


def settle_race(conn, race_id, winning_horse):
    """Manual: enter the winning horse, run §3.3, and STORE pot_cents +
    house_cut_cents + winning_horse on the race for the announcement and for
    reconciliation. Returns the settle summary."""
    _todo("settle_race")


# ---- the race card (what guests and bankers both read) --------------------
def race_card(conn, now=None):
    """Return {'server_now': <int>, 'races': [...]} ordered by ordinal. Each
    race carries a DERIVED status for display:
        'upcoming'  scheduled, not open yet
        'live'      open and now <= closes_at (bettable)
        'closed'    locked, or open but past closes_at (awaiting result)
        'settled'   winner in, payouts done
    plus opens_at, closes_at, planned_at, horses, winning_horse, pot_cents,
    house_cut_cents. server_now is included so a phone can run an honest
    countdown against the SERVER clock, not its own."""
    _todo("race_card")


# ---- per-guest result (drives the winner reveal on the phone) -------------
def guest_result(conn, account_id, race_id):
    """For a settled race, what this guest should see:
        {'settled': True, 'winning_horse': .., 'won': bool, 'payout_cents': int}
    won/payout reflect this account's payout rows for the race."""
    _todo("guest_result")


# ---- always-on reconciliation (spec §7) -----------------------------------
def reconciliation(conn):
    """The running invariant one volunteer watches all night. Returns the terms
    and a balanced flag:
        cash_in, zeffy_in, credit_issued (= cash_in + zeffy_in),
        outstanding_balances, house_cut, staked_in_open_races,
        balanced = (credit_issued == outstanding_balances + house_cut
                    + staked_in_open_races)
    At rest (all races settled, nothing in play) this reduces to
        credit_issued == outstanding_balances + house_cut.
    Drift means investigate immediately. (Cash-out at the bank is a future term.)"""
    _todo("reconciliation")
