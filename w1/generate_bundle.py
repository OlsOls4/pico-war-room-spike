#!/usr/bin/env python3
"""Generate the frozen incident bundle incident-4815.json (seed 4815).

Deterministic, stdlib only, no network. Re-running this script must
reproduce incident-4815.json byte-identically.

Design (per W1 task spec):
- Incident: "Checkout failing — 03:14", 2026-11-26, window 03:14–03:16.
- 200 log lines. Top cause: Payment gateway timeout (41 pay-gw connect
  timeouts from 03:14:07 + checkout upstream 504s + config-drift deploy line).
- Decoys: DB connection pool exhausted (pool 38/40, p95 210ms within SLO)
  and Bad deploy v2.14.3 (deploy finished clean, no errors from new code
  paths, auth errors pre-date the deploy).
- The scorer derives the ranking; it never reads ground_truth_cause_id.
"""
import json
import os
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "incident-4815.json")

DAY = datetime(2026, 11, 26, tzinfo=timezone.utc)


def ts(minute, second=0):
    # `minute` is minutes after 03:00 UTC on 2026-11-26.
    return (DAY + timedelta(hours=3, minutes=minute, seconds=second)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


# Times are minutes after 03:00 UTC. Incident window 03:14:00–03:16:00.
# Baseline 03:12:00–03:13:59. Recovery 03:19:30–03:21:30.

# --- Explicit lines: (minute, second, service, level, message) -------------
explicit = []

# Baseline, 03:12:00–03:13:57 — 28 routine ok lines + 2 pre-deploy auth errors.
baseline_msgs = [
    ("checkout", "GET /checkout 200 42ms"),
    ("pay-gw", "pay-gw ok 200 38ms"),
    ("db", "db query ok 12ms"),
    ("auth", "auth token verify ok"),
    ("inventory", "inventory stock check ok"),
    ("shipping", "shipping rates ok 200"),
]
for i in range(28):
    svc, msg = baseline_msgs[i % len(baseline_msgs)]
    # Spread 03:12:00–03:13:50, ~4s apart.
    total_s = 12 * 60 + (i * 4) + (i % 3)
    explicit.append((total_s // 60, total_s % 60, svc, "INFO", msg))
# Two auth errors that PRE-DATE the deploy (contradict the bad-deploy cause).
explicit.append((13, 10, "auth", "ERROR",
                 "auth ERROR token refresh failed (transient) — pre-dates deploy"))
explicit.append((13, 22, "auth", "ERROR",
                 "auth ERROR token refresh failed (transient) — pre-dates deploy"))

# Deploy lines, 03:13:58–03:13:59.
explicit.append((13, 58, "pay-gw", "WARN",
                 "pay-gw deploy v2.14.3 finished; config drift detected: "
                 "connect_timeout=30s (expected 5s) — upstream closes idle "
                 "connections at 5s"))
explicit.append((13, 59, "pay-gw", "INFO",
                 "pay-gw deploy v2.14.3 finished clean; no errors from new code paths"))
explicit.append((13, 59, "pay-gw", "INFO",
                 "pay-gw deploy v2.14.3 health checks passing (3/3)"))

# 41 pay-gw connect timeouts, 03:14:07–03:14:58 (~1.25s apart).
# The first one is also the first ERROR after the deploy.
for i in range(41):
    s = 7 + int(i * 1.25)
    if i == 0:
        msg = ("pay-gw connect timeout after 30s — first ERROR after deploy "
               "v2.14.3 (attempt 1/3)")
    else:
        msg = "pay-gw connect timeout after 30s (attempt %d/3)" % ((i % 3) + 1)
    explicit.append((14, s, "pay-gw", "ERROR", msg))

# Checkout upstream 504s from pay-gw — 7 lines.
for s in (11, 16, 21, 27, 33, 40, 65):
    m = 14 if s < 60 else 15
    explicit.append((m, s % 60, "checkout", "ERROR",
                     "checkout upstream 504 from pay-gw (gateway timeout)"))
# Checkout 502s — 4 lines.
for s in (15, 25, 38, 52):
    explicit.append((14, s, "checkout", "ERROR", "checkout POST /pay 502 bad gateway"))
# Checkout ok lines inside the window — 7 lines (tunes the window error rate).
for s in (12, 19, 30, 44, 55, 68, 80):
    m = 14 if s < 60 else 15
    explicit.append((m, s % 60, "checkout", "INFO", "GET /checkout 200 44ms"))

# DB pool decoy — 4 support lines.
explicit.append((14, 20, "db", "WARN", "db pool 38/40 connections in use"))
explicit.append((14, 35, "db", "WARN", "db pool 38/40 connections in use"))
explicit.append((15, 10, "db", "WARN", "db pool 37/40 connections in use"))
explicit.append((15, 40, "db", "WARN", "db connection wait 850ms (pool nearly full)"))
# DB contradicting evidence — p95 within SLO (2) + db ok in window (3).
explicit.append((14, 45, "db", "INFO", "db p95 query 210ms (SLO 250ms) — within SLO"))
explicit.append((15, 25, "db", "INFO", "db p95 query 198ms (SLO 250ms) — within SLO"))
for s in (30, 65, 110):
    m = 14 if s < 60 else 15
    explicit.append((m, s % 60, "db", "INFO", "db query ok 14ms"))

# Pay-gw intermittent success in-window — contradicts the timeout cause (2).
explicit.append((14, 30, "pay-gw", "INFO", "pay-gw ok 200 41ms (intermittent success)"))
explicit.append((14, 55, "pay-gw", "INFO", "pay-gw ok 200 39ms (intermittent success)"))

# Other services stay healthy through the incident — 14 lines.
for i, (m, s, svc, msg) in enumerate([
        (14, 18, "inventory", "inventory stock check ok"),
        (14, 33, "shipping", "shipping rates ok 200"),
        (14, 47, "auth", "auth token verify ok"),
        (14, 58, "inventory", "inventory stock check ok"),
        (15, 5, "shipping", "shipping rates ok 200"),
        (15, 18, "auth", "auth token verify ok"),
        (15, 32, "inventory", "inventory stock check ok"),
        (15, 48, "shipping", "shipping rates ok 200"),
        (15, 55, "auth", "auth token verify ok"),
        (16, 2, "inventory", "inventory stock check ok"),
        (16, 12, "shipping", "shipping rates ok 200"),
        (16, 20, "auth", "auth token verify ok"),
        (16, 35, "inventory", "inventory stock check ok"),
        (16, 50, "shipping", "shipping rates ok 200"),
]):
    explicit.append((m, s, svc, "INFO", msg))

# Recovery, 03:19:30–03:21:30 — 6 lines (fix applied 03:19:30).
explicit.append((19, 30, "pay-gw", "INFO", "pay-gw failover to pay-gw-2 initiated (fix applied)"))
explicit.append((19, 45, "checkout", "INFO", "checkout error rate falling: 61% -> 34%"))
explicit.append((20, 15, "checkout", "INFO", "checkout error rate 61% -> 18%"))
explicit.append((20, 45, "pay-gw", "INFO", "pay-gw-2 ok 200 39ms (failover healthy)"))
explicit.append((21, 0, "checkout", "INFO", "checkout error rate 61% -> 9% in 90s — recovering"))
explicit.append((21, 30, "checkout", "INFO", "checkout recovering — 03:21"))
# One stale retry error in recovery — keeps the recovery error rate ~9%.
explicit.append((20, 10, "checkout", "ERROR",
                 "checkout POST /pay 502 (stale retry, pre-failover request)"))
# Extra checkout ok lines in recovery (tunes the recovery error rate to ~9%).
for m, s in [(19, 35), (19, 55), (20, 5), (20, 25), (20, 35), (20, 50), (21, 10), (21, 20)]:
    explicit.append((m, s, "checkout", "INFO", "GET /checkout 200 44ms"))

# --- Filler ok lines (outside the incident window only) --------------------
# Filler must never match a scorer pattern (no timeout/pool/504/deploy/
# drift/p95/SLO/connection-wait wording) and must not land in-window for
# pay-gw or db, where an "ok" line would count as contradicting evidence.
filler_msgs = {
    "checkout": "GET /checkout 200 44ms",
    "pay-gw": "pay-gw ok 200 36ms",
    "db": "db query ok 11ms",
    "auth": "auth token verify ok",
    "inventory": "inventory stock check ok",
    "shipping": "shipping rates ok 200",
}
services = ["checkout", "pay-gw", "db", "auth", "inventory", "shipping"]
n_explicit = len(explicit)
n_filler = 200 - n_explicit
assert n_filler > 0, "explicit lines already exceed 200"
# Filler slots: candidate times 03:12:00–03:13:57 and 03:16:01–03:21:30,
# then pick n_filler of them evenly spaced so filler spans the whole timeline.
candidates = []
t = 12 * 60 + 2
while t < 13 * 60 + 58:
    candidates.append(t)
    t += 2
t = 16 * 60 + 5
while t <= 21 * 60 + 30:
    candidates.append(t)
    t += 3
assert len(candidates) >= n_filler, (len(candidates), n_filler)
step = len(candidates) / n_filler
slots = [candidates[int(i * step)] for i in range(n_filler)]
filler = []
for i, total_s in enumerate(slots):
    svc = services[i % len(services)]
    filler.append((total_s // 60, total_s % 60, svc, "INFO", filler_msgs[svc]))

all_lines = explicit + filler
assert len(all_lines) == 200, len(all_lines)

# Sort by timestamp (stable — insertion order breaks ties), assign ids.
all_lines.sort(key=lambda r: (r[0] * 60 + r[1],))
log_lines = []
for i, (m, s, svc, level, msg) in enumerate(all_lines, start=1):
    log_lines.append({
        "id": "L%03d" % i,
        "timestamp": ts(m, s),
        "service": svc,
        "level": level,
        "message": msg,
    })

by_msg = {}
for line in log_lines:
    by_msg.setdefault(line["message"], []).append(line["id"])


def ids_with(substr, service=None):
    return [ln["id"] for ln in log_lines
            if substr in ln["message"] and (service is None or ln["service"] == service)]


def ids_exact(msg):
    return by_msg.get(msg, [])


# --- Causes ----------------------------------------------------------------
timeout_ids = ids_with("connect timeout after 30s", "pay-gw")
assert len(timeout_ids) == 41, len(timeout_ids)
deploy_drift_id = ids_exact(
    "pay-gw deploy v2.14.3 finished; config drift detected: "
    "connect_timeout=30s (expected 5s) — upstream closes idle "
    "connections at 5s")
assert len(deploy_drift_id) == 1
first_timeout_id = [timeout_ids[0]]
checkout_504_ids = ids_with("upstream 504 from pay-gw", "checkout")
assert len(checkout_504_ids) == 7, len(checkout_504_ids)
pool_ids = ids_with("pool", "db")
assert len(pool_ids) == 4, len(pool_ids)
p95_ids = ids_with("within SLO", "db")
assert len(p95_ids) == 2, len(p95_ids)
db_ok_window_ids = [ln["id"] for ln in log_lines
                    if ln["service"] == "db" and ln["message"] == "db query ok 14ms"]
assert len(db_ok_window_ids) == 3, len(db_ok_window_ids)
paygw_ok_window_ids = ids_with("(intermittent success)", "pay-gw")
assert len(paygw_ok_window_ids) == 2, len(paygw_ok_window_ids)
deploy_clean_id = ids_exact(
    "pay-gw deploy v2.14.3 finished clean; no errors from new code paths")
health_id = ids_exact("pay-gw deploy v2.14.3 health checks passing (3/3)")
auth_predate_ids = ids_with("pre-dates deploy", "auth")
assert len(auth_predate_ids) == 2, len(auth_predate_ids)

causes = [
    {
        "id": "C1",
        "title": "Payment gateway timeout",
        "linked_service": "pay-gw",
        "support_count": 48,
        "contradict_count": 2,
        "pinned_evidence": timeout_ids + checkout_504_ids + deploy_drift_id,
        "contradicting_evidence": paygw_ok_window_ids,
    },
    {
        "id": "C2",
        "title": "Database connection pool exhausted",
        "linked_service": "db",
        "support_count": 4,
        "contradict_count": 5,
        "pinned_evidence": pool_ids,
        "contradicting_evidence": p95_ids + db_ok_window_ids,
    },
    {
        "id": "C3",
        "title": "Bad deploy v2.14.3",
        "linked_service": "pay-gw",
        "support_count": 2,
        "contradict_count": 6,
        "pinned_evidence": deploy_drift_id + first_timeout_id,
        # The 2 intermittent pay-gw successes also weaken the deploy cause:
        # the gateway was still answering after v2.14.3 went out.
        "contradicting_evidence": (deploy_clean_id + health_id
                                   + auth_predate_ids + paygw_ok_window_ids),
    },
]

# --- Service states over time (03:14–03:21, per minute) --------------------
# The propagation wavefront: pay-gw goes down first, checkout follows,
# everything else stays green; recovery after the 03:19:30 failover.
service_states = {
    "pay-gw": [("03:14", "down"), ("03:15", "down"), ("03:16", "down"),
               ("03:17", "degraded"), ("03:18", "degraded"), ("03:19", "degraded"),
               ("03:20", "recovering"), ("03:21", "ok")],
    "checkout": [("03:14", "degraded"), ("03:15", "down"), ("03:16", "down"),
                 ("03:17", "degraded"), ("03:18", "degraded"), ("03:19", "degraded"),
                 ("03:20", "recovering"), ("03:21", "ok")],
    "inventory": [("03:14", "ok"), ("03:15", "ok"), ("03:16", "degraded"),
                  ("03:17", "ok"), ("03:18", "ok"), ("03:19", "ok"),
                  ("03:20", "ok"), ("03:21", "ok")],
    "shipping": [("03:14", "ok")] * 1 + [("03:15", "ok"), ("03:16", "ok"),
                 ("03:17", "ok"), ("03:18", "ok"), ("03:19", "ok"),
                 ("03:20", "ok"), ("03:21", "ok")],
    "db": [("03:14", "ok"), ("03:15", "ok"), ("03:16", "ok"),
           ("03:17", "ok"), ("03:18", "ok"), ("03:19", "ok"),
           ("03:20", "ok"), ("03:21", "ok")],
    "auth": [("03:14", "ok"), ("03:15", "ok"), ("03:16", "ok"),
             ("03:17", "ok"), ("03:18", "ok"), ("03:19", "ok"),
             ("03:20", "ok"), ("03:21", "ok")],
}
service_states = {
    svc: [{"timestamp": "2026-11-26T%s:00Z" % t, "state": st} for t, st in states]
    for svc, states in service_states.items()
}

# --- Checkout error rates (computed from the bundle, not asserted) ---------
def checkout_error_rate(start_min, end_min):
    window = [ln for ln in log_lines
              if ln["service"] == "checkout"
              and start_min <= int(ln["timestamp"][14:16]) < end_min]
    if not window:
        return 0, 0
    errors = sum(1 for ln in window if ln["level"] == "ERROR")
    return errors, len(window)


def checkout_error_rate_range(start_ts, end_ts):
    """Error rate over an exact timestamp range (recovery = 90s after fix)."""
    window = [ln for ln in log_lines
              if ln["service"] == "checkout" and start_ts <= ln["timestamp"] < end_ts]
    errors = sum(1 for ln in window if ln["level"] == "ERROR")
    return errors, len(window)


win_err, win_total = checkout_error_rate(14, 16)
rec_err, rec_total = checkout_error_rate_range(
    "2026-11-26T03:19:30Z", "2026-11-26T03:21:00Z")
win_pct = round(100.0 * win_err / win_total) if win_total else 0
rec_pct = round(100.0 * rec_err / rec_total) if rec_total else 0

bundle = {
    "format_version": "1.0.0",
    "incident_id": "INC-4815",
    "seed": 4815,
    "title": "Checkout failing — 03:14",
    "generated_at": "2026-11-26T09:10:00Z",
    "incident_window": {
        "start": "2026-11-26T03:14:00Z",
        "end": "2026-11-26T03:16:00Z",
    },
    "log_lines": log_lines,
    "causes": causes,
    "service_states": service_states,
    "fix": {
        # Wording matches the fix the cold re-test tester remembered:
        # "roll back gateway config to v2.14.2, fail over to standby;
        # 61% → 9% in 90s" (BUILD-CONSTRAINTS.md).
        "title": "Roll back gateway config to v2.14.2 and fail over to standby gateway (pay-gw-2)",
        "steps": [
            "Roll back pay-gw config to v2.14.2 (connect timeout back to 5s, matching the upstream idle-close).",
            "Route checkout payment traffic from pay-gw to standby gateway pay-gw-2.",
            "Drain in-flight pay-gw connections and confirm pay-gw-2 health checks pass.",
            "Watch checkout error rate for 90s before declaring recovery.",
        ],
        "expected_effect": "error rate %d%% → %d%% in 90s" % (win_pct, rec_pct),
    },
    "ground_truth_cause_id": "C1",
}

with open(OUT, "w") as f:
    json.dump(bundle, f, indent=2, sort_keys=False)
    f.write("\n")

print("wrote %s" % OUT)
print("log lines: %d (explicit %d, filler %d)" % (len(log_lines), n_explicit, n_filler))
print("checkout error rate in window: %d/%d = %d%%" % (win_err, win_total, win_pct))
print("checkout error rate in recovery: %d/%d = %d%%" % (rec_err, rec_total, rec_pct))
print("C1 pinned evidence: %d lines" % len(causes[0]["pinned_evidence"]))
