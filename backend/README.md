# Backend — Milestone 1: schema + the three transactions

This is step 1 of the spec's build order (§12): the Postgres schema and the
three atomic transactions (§3), with suites 01 and 02 re-pointed from the
in-browser prototypes to a **real database** — plus the two tests the spec says
can only be run against real infrastructure (§10).

## What's here

| File | What it is |
|---|---|
| `schema.sql` | The data model (§2): append-only `ledger_entries` + derived cache tables, the idempotency index, the one-account-per-alias key, and an append-only trigger. |
| `db.py` | The data layer: `place_bet` (§3.1), `topup` (§3.2), `settle` (§3.3), identity `resolve`/merge (§5), and `rebuild_from_log` (crash recovery, §2/§9). Every money op runs in its own DB transaction. |
| `tests/test_pot_settle.py` | Suite 01 (13 assertions) against Postgres. |
| `tests/test_identity.py` | Suite 02 (9 assertions) against Postgres. |
| `tests/test_concurrency.py` | **New (§10):** N real threads, no overspend. |
| `tests/test_webhook_idempotency.py` | **New (§10):** concurrent redelivery credits once. |
| `run_all.py` | Applies the schema and runs all four suites. |

## Run it

Requires Python 3.10+ and a Postgres you can reach.

```bash
pip install "psycopg[binary]"

# point at your database (defaults to a local dev socket if unset)
export DATABASE_URL="postgres://user:pass@localhost:5432/natr"

python run_all.py
```

Expected: `TOTAL: 26 pass · 0 fail · 0 error`.

`run_all.py` **drops and recreates** all tables each run, so use a throwaway
database, never one with real data.

## What this proves (and doesn't)

Green here means the ledger rules hold against a real, concurrent database:
no overspend under simultaneous bets, webhook redelivery credits exactly once,
money is conserved on settle, identities merge correctly, and every cache table
rebuilds from the append-only log after a simulated crash.

Still **not** covered (later milestones): the HTTP layer (webhook receiver +
signature/re-fetch verification, §4.3), the guest web app and auth (§6/§8), the
banker console (§7), Zeffy pre-provisioning (§4.1), and a real
venue-network load test (§9). The webhook test here exercises the DB-level
idempotency guard directly; a full end-to-end test against a deployed endpoint
comes once the receiver exists.
