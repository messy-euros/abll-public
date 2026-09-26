"""
Night at the Races — data layer (spec §3, §5).

Every money operation gets its safety from the database, not from Python:

  * place_bet  (§3.1) — atomic overspend guard + race row-lock
  * topup      (§3.2) — idempotent under webhook redelivery (unique payment_ref)
  * settle     (§3.3) — money conserved; dust to the house

Identity resolution (§5) is an alias upsert with automatic, logged merges.
rebuild_from_log() reconstructs every cache table by replaying the ledger,
which is the crash-safety property realised on durable storage (§2, §9).

Each public function takes a psycopg connection and runs in its own
transaction (BEGIN/COMMIT), so callers can fire them concurrently and the
DB serialises them. math.floor semantics match the four browser suites exactly.
"""
from __future__ import annotations
import math
import os
import psycopg


# ---- connection -----------------------------------------------------------
def connect(dsn: str | None = None) -> psycopg.Connection:
    dsn = dsn or os.environ.get(
        "DATABASE_URL", "host=/tmp port=5433 dbname=natr user=postgres"
    )
    return psycopg.connect(dsn, autocommit=False)


def _norm_email(e: str | None) -> str | None:
    return (e or "").strip().lower() or None


def _alias_keys(contact_id=None, email=None, use_contact=True):
    ks = []
    if use_contact and contact_id:
        ks.append("contact:" + contact_id)
    ne = _norm_email(email)
    if ne:
        ks.append("email:" + ne)
    return ks


# ---- account / alias primitives -------------------------------------------
def ensure_account(conn, account_id, label=None):
    """Create an account (and its zero balance) if new, logging open_account.
    Used by the suite-01 style where the player string *is* the account id."""
    with conn.transaction():
        cur = conn.execute(
            "INSERT INTO accounts(account_id,label) VALUES(%s,%s) "
            "ON CONFLICT (account_id) DO NOTHING RETURNING account_id",
            (account_id, label),
        )
        if cur.fetchone():
            conn.execute(
                "INSERT INTO balances(account_id,balance_cents) VALUES(%s,0)",
                (account_id,),
            )
            conn.execute(
                "INSERT INTO ledger_entries(type,account_id,meta) "
                "VALUES('open_account',%s,jsonb_build_object('label',%s::text))",
                (account_id, label),
            )
    return account_id


def _next_account_id(conn) -> str:
    n = conn.execute("SELECT count(*) FROM accounts").fetchone()[0]
    return f"acct_{n + 1}"


def resolve_identity(conn, contact_id=None, email=None, label=None, use_contact=True):
    """Resolve {contact, email} to a single account, merging when two known
    aliases point at different accounts. Returns (account_id, merged). (§5)"""
    keys = _alias_keys(contact_id, email, use_contact)
    with conn.transaction():
        if not keys:  # anonymous walk-in
            acct = _next_account_id(conn)
            _open(conn, acct, label)
            return acct, False

        rows = conn.execute(
            "SELECT DISTINCT account_id FROM account_aliases WHERE alias = ANY(%s)",
            (keys,),
        ).fetchall()
        roots = [r[0] for r in rows]

        merged = False
        if not roots:
            acct = _next_account_id(conn)
            _open(conn, acct, label)
        else:
            acct = roots[0]
            for loser in roots[1:]:
                _merge(conn, loser, acct)
                merged = True

        # attach any missing alias to the surviving account (upsert)
        for k in keys:
            conn.execute(
                "INSERT INTO account_aliases(alias,account_id) VALUES(%s,%s) "
                "ON CONFLICT (alias) DO UPDATE SET account_id=EXCLUDED.account_id "
                "WHERE account_aliases.account_id <> EXCLUDED.account_id",
                (k, acct),
            )
            conn.execute(
                "INSERT INTO ledger_entries(type,account_id,meta) "
                "VALUES('attach_alias',%s,jsonb_build_object('alias',%s::text))",
                (acct, k),
            )
    return acct, merged


def _open(conn, account_id, label):
    conn.execute(
        "INSERT INTO accounts(account_id,label) VALUES(%s,%s) "
        "ON CONFLICT (account_id) DO NOTHING",
        (account_id, label),
    )
    conn.execute(
        "INSERT INTO balances(account_id,balance_cents) VALUES(%s,0) "
        "ON CONFLICT (account_id) DO NOTHING",
        (account_id,),
    )
    conn.execute(
        "INSERT INTO ledger_entries(type,account_id,meta) "
        "VALUES('open_account',%s,jsonb_build_object('label',%s::text))",
        (account_id, label),
    )


def _merge(conn, loser, survivor):
    """Union two accounts in-transaction and log it. (§5)"""
    conn.execute("UPDATE account_aliases SET account_id=%s WHERE account_id=%s",
                 (survivor, loser))
    conn.execute("UPDATE bets SET account_id=%s WHERE account_id=%s",
                 (survivor, loser))
    loser_bal = conn.execute(
        "SELECT balance_cents FROM balances WHERE account_id=%s", (loser,)
    ).fetchone()
    if loser_bal:
        conn.execute(
            "UPDATE balances SET balance_cents=balance_cents+%s WHERE account_id=%s",
            (loser_bal[0], survivor),
        )
        conn.execute("DELETE FROM balances WHERE account_id=%s", (loser,))
    conn.execute("DELETE FROM accounts WHERE account_id=%s", (loser,))
    conn.execute(
        "INSERT INTO ledger_entries(type,account_id,meta) "
        "VALUES('merge',%s,jsonb_build_object('from',%s::text,'into',%s::text))",
        (survivor, loser, survivor),
    )


def lookup_identity(conn, contact_id=None, email=None, use_contact=True):
    keys = _alias_keys(contact_id, email, use_contact)
    if not keys:
        return None
    row = conn.execute(
        "SELECT account_id FROM account_aliases WHERE alias = ANY(%s) LIMIT 1",
        (keys,),
    ).fetchone()
    return row[0] if row else None


# ---- §3.2 credit a payment (idempotent) -----------------------------------
def topup_account(conn, account_id, cents, source, ref=None):
    """Credit an existing account. The unique index on payment_ref makes a
    duplicate webhook a physical no-op. Returns {'ok':bool,'duplicate':bool}."""
    with conn.transaction():
        if ref is not None:
            cur = conn.execute(
                "INSERT INTO ledger_entries(type,account_id,delta_cents,payment_ref,source) "
                "VALUES('topup',%s,%s,%s,%s) "
                "ON CONFLICT (payment_ref) WHERE type='topup' AND payment_ref IS NOT NULL "
                "DO NOTHING RETURNING id",
                (account_id, cents, ref, source),
            )
            if cur.fetchone() is None:          # duplicate → already processed
                return {"ok": False, "duplicate": True}
        else:
            conn.execute(
                "INSERT INTO ledger_entries(type,account_id,delta_cents,source) "
                "VALUES('topup',%s,%s,%s)",
                (account_id, cents, source),
            )
        conn.execute(
            "UPDATE balances SET balance_cents=balance_cents+%s WHERE account_id=%s",
            (cents, account_id),
        )
    return {"ok": True, "duplicate": False}


def topup_identity(conn, identity, cents, source, ref=None, use_contact=True):
    """Resolve identity, then credit idempotently. (§4.2 → §3.2)"""
    acct, merged = resolve_identity(
        conn, identity.get("contactId"), identity.get("email"),
        identity.get("label"), use_contact,
    )
    res = topup_account(conn, acct, cents, source, ref)
    res.update(accountId=acct, merged=merged)
    return res


# ---- races ----------------------------------------------------------------
def open_race(conn, race_id, lock_at, name=None, players_share=0.5):
    with conn.transaction():
        conn.execute(
            "INSERT INTO races(race_id,name,lock_at,players_share,state) "
            "VALUES(%s,%s,%s,%s,'open') ON CONFLICT (race_id) DO NOTHING",
            (race_id, name, lock_at, players_share),
        )


def lock_race(conn, race_id):
    with conn.transaction():
        conn.execute("UPDATE races SET state='locked' WHERE race_id=%s", (race_id,))


# ---- §3.1 place a bet (atomic; no overspend, no bet after post) ------------
def place_bet(conn, account_id, race_id, horse, cents, now):
    if not (isinstance(cents, int) and cents > 0):
        return {"ok": False, "reason": "invalid amount"}
    try:
        with conn.transaction():
            row = conn.execute(
                "SELECT state,lock_at FROM races WHERE race_id=%s FOR UPDATE",
                (race_id,),
            ).fetchone()
            if row is None:
                return {"ok": False, "reason": "no such race"}
            state, lock_at = row
            if state != "open":
                return {"ok": False, "reason": "race not open"}
            if lock_at is not None and now > lock_at:
                return {"ok": False, "reason": "past post time"}

            # atomic overspend guard: debit only if the funds are there
            cur = conn.execute(
                "UPDATE balances SET balance_cents=balance_cents-%s "
                "WHERE account_id=%s AND balance_cents>=%s",
                (cents, account_id, cents),
            )
            if cur.rowcount == 0:
                return {"ok": False, "reason": "insufficient balance"}

            conn.execute(
                "INSERT INTO ledger_entries(type,account_id,delta_cents,race_id,meta) "
                "VALUES('bet',%s,%s,%s,jsonb_build_object('horse',%s::text))",
                (account_id, -cents, race_id, horse),
            )
            conn.execute(
                "INSERT INTO bets(account_id,race_id,horse,cents) VALUES(%s,%s,%s,%s)",
                (account_id, race_id, horse, cents),
            )
        return {"ok": True}
    except psycopg.errors.CheckViolation:
        # balance CHECK(>=0) is a belt-and-braces backstop to the WHERE guard
        return {"ok": False, "reason": "insufficient balance"}


def debit_account(conn, account_id, cents):
    """A plain debit (recorded as a raceless bet row). Used by identity suite."""
    with conn.transaction():
        conn.execute(
            "UPDATE balances SET balance_cents=balance_cents-%s WHERE account_id=%s",
            (cents, account_id),
        )
        conn.execute(
            "INSERT INTO ledger_entries(type,account_id,delta_cents) "
            "VALUES('bet',%s,%s)",
            (account_id, -cents),
        )
    return {"ok": True}


# ---- §3.3 settle a race (money conserved) ---------------------------------
def settle(conn, race_id, winning_horse):
    with conn.transaction():
        row = conn.execute(
            "SELECT state,players_share FROM races WHERE race_id=%s FOR UPDATE",
            (race_id,),
        ).fetchone()
        if row is None or row[0] == "settled":
            return {"ok": False, "reason": "bad state"}
        players_share = float(row[1])

        bets = conn.execute(
            "SELECT account_id,horse,cents FROM bets WHERE race_id=%s", (race_id,)
        ).fetchall()
        pot = sum(b[2] for b in bets)
        winners = [b for b in bets if b[1] == winning_horse]
        stake = sum(b[2] for b in winners)

        # house takes the complement of the players' share; winners split the rest;
        # rounding dust falls to the house. Matches suite 01 exactly.
        house_target = math.floor(pot * (1.0 - players_share))
        dist = pot - house_target
        paid = 0
        payouts = {}
        if stake > 0:
            for acct, _h, cents in winners:
                s = math.floor(dist * cents / stake)
                payouts[acct] = payouts.get(acct, 0) + s
                paid += s
            house_cut = house_target + (dist - paid)
        else:
            house_cut = pot  # no winners → house takes the pot

        for acct, cents in payouts.items():
            conn.execute(
                "INSERT INTO ledger_entries(type,account_id,delta_cents,race_id) "
                "VALUES('payout',%s,%s,%s)",
                (acct, cents, race_id),
            )
            conn.execute(
                "UPDATE balances SET balance_cents=balance_cents+%s WHERE account_id=%s",
                (cents, acct),
            )

        assert paid + house_cut == pot, "conservation: payouts + house must equal pot"
        conn.execute(
            "UPDATE races SET state='settled',winning_horse=%s WHERE race_id=%s",
            (winning_horse, race_id),
        )
    return {"ok": True, "pot": pot, "houseCut": house_cut,
            "payouts": [{"player": a, "cents": c} for a, c in payouts.items()]}


# ---- reads ----------------------------------------------------------------
def balance(conn, account_id):
    row = conn.execute(
        "SELECT balance_cents FROM balances WHERE account_id=%s", (account_id,)
    ).fetchone()
    return row[0] if row else 0


def balance_for_identity(conn, identity, use_contact=True):
    acct = lookup_identity(conn, identity.get("contactId"), identity.get("email"),
                           use_contact)
    return balance(conn, acct) if acct else 0


def accounts(conn):
    return [r[0] for r in conn.execute("SELECT account_id FROM accounts").fetchall()]


# ---- recovery: rebuild every cache table by replaying the log (§2, §9) -----
def rebuild_from_log(conn):
    """Truncate the derived tables and replay ledger_entries, proving the
    'recover from the log' property against durable storage. Reconstructs
    accounts, aliases (with union-find over merges), and balances."""
    with conn.transaction():
        conn.execute("TRUNCATE account_aliases, balances, bets RESTART IDENTITY")
        conn.execute("DELETE FROM accounts")

        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        rows = conn.execute(
            "SELECT type,account_id,delta_cents,race_id,meta FROM ledger_entries "
            "ORDER BY id"
        ).fetchall()
        labels, aliases, bal = {}, {}, {}
        for typ, acct, delta, race_id, meta in rows:
            if typ == "open_account":
                find(acct)
                if meta and meta.get("label") is not None:
                    labels[acct] = meta["label"]
            elif typ == "attach_alias":
                aliases[meta["alias"]] = acct
            elif typ == "merge":
                a, b = find(meta["from"]), find(meta["into"])
                if a != b:
                    parent[a] = b
            elif typ in ("topup", "bet", "payout"):
                if acct is not None:
                    bal[find(acct)] = bal.get(find(acct), 0) + (delta or 0)

        roots = {find(a) for a in parent}
        for r in roots:
            conn.execute(
                "INSERT INTO accounts(account_id,label) VALUES(%s,%s)",
                (r, labels.get(r)),
            )
        for r in roots:
            conn.execute(
                "INSERT INTO balances(account_id,balance_cents) VALUES(%s,%s)",
                (r, bal.get(r, 0)),
            )
        for alias, acct in aliases.items():
            conn.execute(
                "INSERT INTO account_aliases(alias,account_id) VALUES(%s,%s) "
                "ON CONFLICT (alias) DO UPDATE SET account_id=EXCLUDED.account_id",
                (alias, find(acct)),
            )
