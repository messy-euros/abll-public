# Night at the Races — Backend Specification

*A betting-ledger backend for a one-night youth-sports fundraiser. Guests buy $10 "Betting Ticket" chips through Zeffy, place bets on pre-recorded races from their phones, and cash out winnings. This spec turns the four executable test suites (pot/settle ledger, identity ledger, Zeffy→topup mapper, integration seam) into a buildable, hosted system.*

---

## 0. Scope, and why simplicity is the strategy

The whole design is governed by four facts about the event:

- **One night. No retry.** If it breaks at 8:30 pm mid-event, there is no patch window. A paper system degrades gracefully; a clever backend fails hard. So every choice favors "obviously correct and recoverable" over "elegant."
- **Tiny scale.** ~30–80 concurrent guests, 10–12 races, a few hundred bets total. This is nothing. The database will not notice. **Concurrency safety here is about *correctness*, not throughput** — do not reach for anything built for scale.
- **Live money, volunteers.** A payout bug is angry parents and a reconciliation mess, not a typo. The money math must be trivially auditable.
- **Legal framing.** Betting tickets are structured as donations to the nonprofit with prizes/pot-splits for winners, kept inside Zeffy's donation flow. *(Not legal advice — confirm MA raffle/gaming rules with someone qualified. This spec preserves that framing; it does not opine on it.)*

**The one-sentence architecture:** a single small web service, one managed Postgres database, and a web page behind a QR code — with the append-only log as the source of truth and the database's transactions as the place where concurrency is actually made safe.

That last clause is the whole point of moving off the prototypes. The JS harnesses proved the *rules* are right but ran single-threaded; they explicitly could not prove real concurrent safety. Postgres transactions are where that safety becomes real.

---

## 1. High-level architecture

```
        QR code
          │
          ▼
  ┌───────────────┐        ┌──────────────────────────┐
  │  Guest phone  │  HTTPS │      Backend service      │
  │  (web app)    │◄──────►│  • Player API             │        ┌────────────┐
  └───────────────┘        │  • Zeffy webhook receiver │◄──────►│  Postgres  │
                           │  • Banker/admin console   │        │ (managed)  │
  ┌───────────────┐  HTTPS │                           │        └────────────┘
  │ Banker laptop │◄──────►│                           │
  └───────────────┘        └───────────▲───────────────┘
                                        │ payment.completed (HTTPS POST)
                                        │
                                 ┌──────┴──────┐
                                 │    Zeffy    │  (payments + read API + webhooks)
                                 └─────────────┘
```

One process, three responsibilities (player API, webhook receiver, admin console). One database. No microservices, no queue, no cache tier, no websocket cluster. At this scale a single service handles everything with room to spare, and one moving part is one thing that can break.

**Client is a web page, not an app.** The QR code opens a URL; the "app" is that page (a PWA is a nice-to-have, not required). No app store, no install friction — the single-QR-code plan from the start.

---

## 2. Data model — the append-only log, in tables

The source of truth is an immutable, append-only event log. Current state (balances, accounts, races) is derived from it and cached in ordinary tables that are written **in the same transaction** as each log entry. If a cache table is ever suspect, it can be rebuilt by replaying the log — the crash-safety property the prototypes had, now backed by durable storage.

### `ledger_entries` — source of truth, INSERT-only, never updated or deleted
| column | notes |
|---|---|
| `id` | bigserial, monotonic order |
| `ts` | timestamptz, server clock |
| `type` | `open_account` \| `attach_alias` \| `merge` \| `topup` \| `bet` \| `payout` |
| `account_id` | the account the entry affects (null for pure alias/merge rows that carry it in `meta`) |
| `delta_cents` | signed; `+` topup/payout, `−` bet (money rows only) |
| `race_id` | for bet/payout |
| `payment_ref` | Zeffy payment id, for topups (idempotency key) |
| `source` | `zeffy` \| `cash` |
| `meta` | jsonb (alias value, merge from/into, label, etc.) |

A **unique index on `payment_ref` where `type = 'topup'`** is the idempotency guard: a redelivered webhook physically cannot create a second credit.

### Derived / cache tables (written transactionally alongside the log)
- **`accounts`** — `account_id` (pk), `label`, `created_at`.
- **`account_aliases`** — `alias` (pk, e.g. `contact:<uuid>` or `email:<normalized>`), `account_id`. The **unique `alias` primary key is what enforces one-account-per-alias** and makes resolution an upsert.
- **`balances`** — `account_id` (pk), `balance_cents`. The read model for "how much can this person bet."
- **`races`** — `race_id` (pk), `name`, `state` (`open`\|`locked`\|`settled`), `lock_at`, `winning_horse`, `players_share` (e.g. 0.5).
- **`bets`** — `bet_id`, `account_id`, `race_id`, `horse`, `cents`, `ts`. (Convenient for pot math; also present as `bet` rows in the log.)

Money is stored in **integer cents** everywhere (Zeffy already gives cents). Never floats, never re-multiply — a "Betting Ticket" is one `items` entry of `amount: 1000`.

---

## 3. Where atomicity lives — the crux

This section is what the prototypes could not deliver. Three operations must be atomic; all three get their safety from the database, not application code.

### 3.1 Placing a bet (no overspend, no double-spend, no betting after post)
One transaction:

```sql
BEGIN;
  -- 1. lock + verify the race; the row lock also blocks a settle racing this bet
  SELECT state, lock_at FROM races WHERE race_id = :race FOR UPDATE;
  --    reject if state <> 'open' OR now() > lock_at   → ROLLBACK

  -- 2. atomic overspend guard: debit only if funds exist
  UPDATE balances
     SET balance_cents = balance_cents - :amt
   WHERE account_id = :acct AND balance_cents >= :amt;
  --    if rows_affected = 0 → insufficient funds → ROLLBACK

  -- 3. record it
  INSERT INTO ledger_entries(type, account_id, delta_cents, race_id)
         VALUES ('bet', :acct, -:amt, :race);
  INSERT INTO bets(account_id, race_id, horse, cents)
         VALUES (:acct, :race, :horse, :amt);
COMMIT;
```

Why this is safe where the JS harness only *modeled* safety:
- The `UPDATE ... WHERE balance_cents >= :amt` plus the rows-affected check is the **atomic overspend guard**. Two simultaneous full-balance bets can't both succeed, because the row lock on the balance serializes them — the second sees the debited balance and affects zero rows.
- The `SELECT ... FOR UPDATE` on the race row prevents a bet from sneaking in while the race is being settled.
- Works at `READ COMMITTED`; no exotic isolation needed at this scale.

### 3.2 Crediting a Zeffy payment (idempotent under webhook redelivery)
```sql
BEGIN;
  -- idempotency: the unique index makes a duplicate a no-op
  INSERT INTO ledger_entries(type, account_id, delta_cents, payment_ref, source, meta)
         VALUES ('topup', :acct, :cents, :ref, 'zeffy', :meta)
  ON CONFLICT (payment_ref) WHERE type='topup' DO NOTHING;
  --    if nothing inserted → already processed → COMMIT, return 200

  UPDATE balances SET balance_cents = balance_cents + :cents WHERE account_id = :acct;
COMMIT;
```
The database enforces "credit once," not a hand-written check. This is exactly the seam bug the integration suite catches (dropping the ref) — here the ref is a hard constraint, so the bug can't survive.

### 3.3 Settling a race (money conserved)
In one transaction: read the race's bets, compute `pot = Σ bets`, `house = floor(pot × players_share_complement)`, distribute the rest proportionally to winning stake with rounding dust going to the house, insert `payout` ledger rows crediting winners' balances, set `race.state = 'settled'`. Assert `Σpayouts + house = pot` before commit. (This is the earlier pot/settle suite, now transactional.)

---

## 4. Zeffy integration

Two independent paths, plus a hard security requirement.

### 4.1 Pre-provisioning (read API, the night before)
A one-shot job pulls Contacts and Payments from the **Drinks & Bets** campaign (and ticket buyers for labels/contact linkage) and pre-creates accounts: one per Zeffy `contact` UUID, aliased by both `contact:<uuid>` and `email:<normalized>`, pre-loaded with any Betting-Ticket chips already purchased. Result: most of the room arrives already registered *and* funded, killing the check-in bottleneck. Uses the read-only API (send a browser `User-Agent` to avoid Zeffy's Cloudflare 1010 filter).

### 4.2 Live webhook receiver (`payment.completed`)
A public HTTPS endpoint. On each event: verify authenticity (§4.3), run `map(payment)` (the mapper suite, ported verbatim — it's a pure function), then credit via §3.2. Return `200` quickly; Zeffy retries on non-200, and idempotency makes retries harmless. Betting credit comes **only** from Betting-Ticket items (`rate_id`), never drinks, never the gross — the discriminator is `rate_id`, refreshed each year from the new form.

### 4.3 Webhook authenticity — do not skip this
The webhook URL is public. Anyone who finds it can POST a fake `payment.completed` and credit themselves free betting money. The receiver **must** verify every event before crediting, by whichever Zeffy offers:
1. **Signature/secret** on the webhook, if Zeffy provides one — verify the HMAC.
2. **Re-fetch by id.** Regardless of signature, the robust check is to call the read API for that payment id and trust *only* the re-fetched record's amount/items/status. A spoofed POST won't correspond to a real payment. Given the read API is already wired for pre-provisioning, this is cheap and I'd do it even if a signature exists.

Treat the webhook body as an untrusted claim, not an instruction.

---

## 5. Identity & accounts

- **Canonical key: the Zeffy `contact` UUID.** Your data proved it's stable across both campaigns for the same person — far more robust than matching email strings. Email (normalized) is the fallback for cash walk-ins and the human label.
- **Resolution is an alias upsert.** For each of `{contact:C, email:E}`, `INSERT ... ON CONFLICT (alias) DO NOTHING`. A new alias on a known account attaches; unknown aliases open an account.
- **Merges are automatic and logged** (your decision). When two aliases resolve to two different existing accounts, union them in-transaction and write a `merge` ledger row. The append-only trail means the exception desk can see and audit every merge — correct balances *and* a paper trail.
- The identity suite is the acceptance test for this table set and its transactions.

---

## 6. The guest web app (self-service betting)

The bottleneck last year was a human transcribing every bet. Self-service on each guest's own phone removes it entirely — betting is fully parallel because there is no shared write path through a volunteer.

Flow:
1. **Open** the QR URL.
2. **Claim** the account — enter the email used at Zeffy (matches an alias) or a claim code from check-in; server issues a signed session bound to `account_id`. (Auth tradeoffs in §8.)
3. **Bet** — see the open race and horses, tap a horse, confirm a $10 (or multiple-chip) bet. The server runs §3.1; the phone shows the updated balance.
4. **Locks** at post time server-side; late taps are rejected by the transaction, not trusted from the client.
5. **Winnings** are credited back to the balance on settle; the guest can re-bet immediately (keeps the handle rolling) or cash out at the bank.

---

## 7. Banker / admin console

A password-protected console for a few trusted volunteers:
- **Cash top-ups** — confirm cash received, credit an account by email/contact (a `source='cash'` topup).
- **Exception desk** — resolve `missing_identity` flags, group buys (one payment, several bettors), and review auto-merges. Create walk-in accounts.
- **Race control** — open / lock / settle races, enter the winning horse, trigger payouts.
- **Reconciliation (always on)** — the running invariant: `cash_in + zeffy_in == credit_issued`, and `credit_issued == outstanding_balances + house_cut + paid_out`. One volunteer watches this all night; drift means investigate immediately.

---

## 8. Auth & security

- **Guests (low stakes, but not zero):** binding a session to a claimed account matters so people can't bet from someone else's balance. Options, in order of friction:
  - *Claim code* handed out at check-in or printed on the Zeffy receipt — simple, works offline-ish, recommended.
  - *Magic link/code to email* — stronger, but requires email sending and adds a step.
  - *Email-only claim* — weakest (someone could type another's email); acceptable only given the physical room and social accountability, and I wouldn't rely on it alone.
- **Banker/admin:** real login (password or PIN). These are privileged money actions; restrict to a handful of volunteers.
- **No card data, ever.** All card handling stays inside Zeffy's hosted checkout. The backend never sees or stores a PAN; card `last4` isn't needed and shouldn't be pulled.
- **Webhook verification** per §4.3.
- **HTTPS everywhere** (required anyway for the webhook and for a QR-launched web app).

---

## 9. Hosting & operations

- **Host:** a small managed platform (a PaaS or a modest VPS) + **managed Postgres**. Smallest tiers are ample. Do not run the server on a laptop at the venue — host it, so the server doesn't depend on venue wifi (only guests' phones need connectivity to reach it).
- **Crash-safety, concretely:** all state lives in Postgres, not process memory. If the app restarts mid-event, balances and races are intact on reconnect — the "recover from the log" property, realized by durable storage. Managed Postgres backups add a second net; the append-only log adds a third.
- **Network is the real event-night risk.** Guests' phones need to reach the server over venue wifi/cell, which can be flaky. Mitigations:
  - **Paper fallback stays ready.** The chip/token system can still run at the bank if the app wobbles. Keep physical chips on hand. This is the graceful-degradation path a software-only plan lacks.
  - **Load-test on the actual venue network** with a dozen real phones before doors open. "Works on my laptop" is not evidence it works for 80 phones on venue wifi.
  - The app should tolerate transient disconnects (retry a submit, show clear pending/failed states) rather than silently losing a bet.

---

## 10. The test suites are the acceptance criteria

The four suites aren't throwaway prototypes — ported to run against the real API and database, they're the contract the backend must satisfy:

| Suite | Becomes | Verifies against real infra |
|---|---|---|
| Pot/settle ledger | §3.3 settle transaction + `races`/`bets` | conservation, proportional payout, rounding |
| Identity ledger | §5 `accounts`/`account_aliases` + resolve/merge | one account per person, logged merges, recovery |
| Zeffy→topup mapper | §4.2 pure `map()` in the receiver | chips-only credit, drinks excluded, ref passthrough |
| Integration seam | §4.2→§3.2 credit path | end-to-end, idempotent, identity-keyed |

Add two **new** tests that only real infrastructure can exercise — the exact gaps the prototypes couldn't cover:
- **True concurrency:** hammer one account with N simultaneous bets against the live DB; assert no overspend. (Proves §3.1's row-lock guard, not just the modeled version.)
- **Live webhook + redelivery:** a real `payment.completed` (and a duplicate) hitting the deployed endpoint credits exactly once.

---

## 11. What NOT to build

The danger here is over-engineering, which adds fragility to a system that can't afford it:
- No microservices, message queues, event-bus, or CQRS framework. One service, synchronous handlers.
- No Redis/cache tier. Postgres is the cache.
- No websocket cluster or real-time sync engine. Poll for the current race; at this scale it's free.
- No custom auth infrastructure. A session cookie and a hashed admin password suffice.
- No horizontal scaling, autoscaling, or sharding. One small instance.
- No in-memory state of record. Ever. That's the failure the append-only-in-Postgres model exists to prevent.

If a proposed piece isn't needed to make one of the suites green or to survive a restart on a flaky network, it's probably fragility in disguise.

---

## 12. Open decisions & next steps

**Decisions to make before building:**
1. Guest auth: claim code vs magic link vs email-only (§8).
2. Whether payouts credit chips back (recommended — keeps handle rolling) vs cash-only at the bank.
3. House/players split per race (`players_share`) and rounding rule confirmation.
4. This year's Betting-Ticket `rate_id` (grab it from the new Drinks & Bets form).
5. Language/stack for the service (any is fine; pick what the builder knows — the design is stack-agnostic).

**Build order (each step ends green against a suite):**
1. Schema + the three transactions (§3) → pot/settle + identity suites pass against Postgres.
2. Webhook receiver with `map()` + verification (§4) → mapper + integration suites pass against the live endpoint.
3. Pre-provisioning job (§4.1).
4. Guest web app (§6) + claim/auth (§8).
5. Banker console + reconciliation (§7).
6. Deploy, then the two infra-only tests (§10) and a venue-network load test (§9).

---

*Boundary, stated plainly: everything above §10's new tests is proven by single-threaded logic. The two things that still require real infrastructure to trust — concurrent atomicity and live webhook delivery — are called out explicitly and are the first things to test once the service is deployed. Build small, keep the paper fallback, test on the real network.*
