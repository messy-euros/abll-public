# Night at the Races — build bundle

A betting-ledger system for a one-night youth-sports fundraiser. Guests buy $10
"Betting Ticket" chips through Zeffy, bet on pre-recorded races from their phones,
and cash out winnings. This bundle is the design plus its executable specification.

## What's here

- **`night-at-the-races-backend-spec.md`** — the backend architecture spec. Start here.
  One web service + one Postgres + a web page behind a QR code. Right-sized for a
  one-night, ~80-person, live-money event where the enemy is complexity, not scale.

- **`test-suites/`** — four red-first TDD harnesses. Open any of them in a browser
  (locally). Each has three buttons: **Stub** (all red — the spec), **Correct**
  (green), and a **seeded bug** that reds only the tests it should. These are the
  acceptance criteria the backend must satisfy; port them to run against the real
  API and database.
  - `01-ledger-pot-settle.html` — balances, pots, proportional payouts, rounding, conservation, crash-recovery.
  - `02-ledger-identity.html` — one person / many keys: contact UUID + email resolve to one account; auto-merge; log-rebuildable.
  - `03-zeffy-mapper.html` — a real Zeffy `payment.completed` → a ledger top-up. Credits Betting-Ticket items only (by `rate_id`), excludes drinks.
  - `04-integration.html` — the seam: raw payment → map() → topup() → correct, identity-keyed, idempotent balance end-to-end.

- **`scripts/`** — read-only Zeffy pullers (Python 3.7+, stdlib only). Reads the key
  from `ZEFFY_API_KEY`; never hardcode it. Writes a real-data file (local only) and
  a redacted schema file (safe to share).
  - `zeffy_sample.py` — a general sample of payments/contacts/campaigns.
  - `zeffy_event_sample.py` — filtered to the "Night at the Races" campaigns; surfaces multi-line-item payments.

## Build order (each step ends green against a suite)

1. Postgres schema + the three transactions (bet / credit / settle) → suites 01 & 02 pass against the DB.
2. Webhook receiver with `map()` + authenticity check → suites 03 & 04 pass against the live endpoint.
3. Pre-provisioning job (read API, night before).
4. Guest web app + claim/auth.
5. Banker console + reconciliation.
6. Deploy, then the two infra-only tests: true concurrency (many phones, one account) and live webhook redelivery. Load-test on the actual venue network.

## Honest boundary

The suites are single-threaded logic — they prove the *rules* are right. They do not
prove concurrent atomicity or live webhook delivery; those need the real database and
a deployed endpoint (spec §3 and §4). Build small, keep a paper/chip fallback ready,
and verify the webhook is authenticated (spec §4.3) before it touches money.

*Not legal advice: the betting-ticket-as-donation framing is preserved throughout but
should be confirmed against MA raffle/gaming rules by someone qualified.*
