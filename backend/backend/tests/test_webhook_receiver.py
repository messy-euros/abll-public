"""NEW — webhook receiver verification (spec §4.3). The endpoint is public, so a
POST body is an untrusted claim. These tests prove the receiver credits only
what the read API confirms: spoofed ids and tampered amounts get nothing, a
redelivery credits once, and an HMAC secret (when set) is enforced."""
import sys, os, json, hmac, hashlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from harness import ok, eq  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import db as DB  # noqa: E402
import webhook  # noqa: E402
from mapper import BETTING_RATE_ID  # noqa: E402
from zeffy_client import FakeZeffy  # noqa: E402

DRINK_RATE = "c5fd0e98-c247-4b8a-ace6-a595efe3c02f"


def bet(a):
    return {"amount": a, "rate_id": BETTING_RATE_ID, "rate_title": "Betting Ticket"}


def drink():
    return {"amount": 200, "rate_id": DRINK_RATE, "rate_title": "Alcoholic Drink Ticket"}


# The payments that ACTUALLY happened, per the read API.
REAL = [
 {"id": "pay_real", "status": "succeeded", "refund_status": "none", "refunds": [],
  "contact": "contact-uuid-1",
  "buyer": {"email": "jane@example.com", "first_name": "Jane", "last_name": "Doe"},
  "items": [bet(1000), bet(1000)]},                                  # $20 of chips
 {"id": "pay_drinks", "status": "succeeded", "refund_status": "none", "refunds": [],
  "contact": "contact-uuid-9", "buyer": {"email": "d@x.com"},
  "items": [drink(), drink()]},                                      # no chips
]
ZEFFY = FakeZeffy(REAL)


def _body(payment_id, **extra):
    b = {"type": "payment.completed", "data": {"id": payment_id, **extra}}
    return json.dumps(b).encode("utf-8")


def valid_event_credits(c):
    status, resp = webhook.process_event(c, ZEFFY, _body("pay_real"))
    eq(status, 200, "accepted")
    eq(resp["credited"], 2000, "credited the two real chips")
    eq(DB.balance_for_identity(c, {"contactId": "contact-uuid-1"}), 2000, "balance")


def spoofed_id_rejected(c):
    # An attacker POSTs an id that never happened, claiming a big payout.
    status, resp = webhook.process_event(
        c, ZEFFY, _body("pay_FAKE", items=[bet(999999)]))
    eq(status, 404, "unknown payment rejected")
    eq(len(DB.accounts(c)), 0, "nothing credited to anyone")


def tampered_amount_ignored(c):
    # Real payment is drinks-only; the POST lies that it's a huge chip buy.
    body = _body("pay_drinks", items=[bet(500000)], amount=500000)
    status, resp = webhook.process_event(c, ZEFFY, body)
    eq(status, 200, "processed")
    eq(resp["credited"], 0, "trust the re-fetched drinks-only record, not the body")
    eq(len(DB.accounts(c)), 0, "no betting credit from a drinks-only payment")


def redelivery_credits_once(c):
    webhook.process_event(c, ZEFFY, _body("pay_real"))
    webhook.process_event(c, ZEFFY, _body("pay_real"))   # Zeffy retried
    eq(DB.balance_for_identity(c, {"contactId": "contact-uuid-1"}), 2000, "once only")


def missing_id_is_bad_request(c):
    status, _ = webhook.process_event(c, ZEFFY, json.dumps({"type": "x"}).encode())
    eq(status, 400, "no payment id -> 400")


def signature_enforced(c):
    secret = "whsec_test"
    raw = _body("pay_real")
    good = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()

    s_bad, _ = webhook.process_event(c, ZEFFY, raw,
                                     {"X-Zeffy-Signature": "sha256=deadbeef"}, secret)
    eq(s_bad, 401, "wrong signature rejected")
    eq(len(DB.accounts(c)), 0, "nothing credited on bad signature")

    s_missing, _ = webhook.process_event(c, ZEFFY, raw, {}, secret)
    eq(s_missing, 401, "missing signature rejected when secret configured")

    s_ok, resp = webhook.process_event(c, ZEFFY, raw,
                                       {"X-Zeffy-Signature": f"sha256={good}"}, secret)
    eq(s_ok, 200, "correct signature accepted")
    eq(resp["credited"], 2000, "credited after valid signature")


SUITE = [
 ("valid event -> re-fetched and credited", valid_event_credits),
 ("spoofed payment id -> rejected, nothing credited", spoofed_id_rejected),
 ("tampered POST amount -> ignored; only re-fetched record is trusted",
  tampered_amount_ignored),
 ("redelivered event -> credited exactly once", redelivery_credits_once),
 ("missing payment id -> 400", missing_id_is_bad_request),
 ("HMAC secret configured -> bad/missing rejected, valid accepted",
  signature_enforced),
]


if __name__ == "__main__":
    from harness import run_suite
    g, r, e = run_suite("NEW - webhook verification (spec §4.3)", SUITE)
    sys.exit(1 if (r or e) else 0)
