# W1 — Frozen data + deterministic scorer (War-Room)

War-Room is the **FINAL pick** (confirmed 26 Nov 2026; cold re-test 4/4 PASS,
so per the promotion rule D2 cannot displace it; I9 is CLOSED, no-go).
This directory is W1 of the War-Room spike: one frozen incident bundle and
the deterministic scorer that ranks its causes. Python 3.12, stdlib only,
no network, no keys, no LLM.

## Files

| File | What it is |
|---|---|
| `incident-bundle.schema.json` | Versioned JSON schema, bundle format version `1.0.0` |
| `incident-4815.json` | The frozen bundle: seed 4815, incident `INC-4815` |
| `generate_bundle.py` | Deterministic generator that builds `incident-4815.json` (re-running it reproduces the bundle byte-identically) |
| `scorer.py` | Deterministic scorer (`python3 scorer.py incident-4815.json`) |
| `test_scorer.py` | Tests, stdlib `unittest` only |
| `README.md` | This file |

## The incident

**"Checkout failing — 03:14"**, 26 Nov 2026, window 03:14:00–03:16:00 UTC.
200 log lines across `checkout`, `pay-gw`, `inventory`, `shipping`, `db`,
`auth` — mostly `ok 200` lines with scattered red ERROR lines.

| Rank | Cause | Share | Support | Contradicts |
|---|---|---|---|---|
| 1 | Payment gateway timeout (`C1`) | **82.9%** | 48 | 2 |
| 2 | Database connection pool exhausted (`C2`) | 10.9% | 4 | 5 |
| 3 | Bad deploy v2.14.3 (`C3`) | 6.2% | 2 | 6 |

Ground truth (`C1`) lives in the bundle as `ground_truth_cause_id` for
verification only. **The scorer never reads it** — tests prove it by
deleting/changing the field and requiring identical output.

Key evidence (all real lines in the bundle, referenced by id):
- **C1:** 41× `pay-gw connect timeout after 30s` from 03:14:07; 7×
  `checkout upstream 504 from pay-gw`; deploy line at 03:13:58 showing
  config drift (`connect_timeout=30s`, expected 5s). Contradicted by 2
  intermittent `pay-gw ok 200` lines in-window.
- **C2 (decoy):** `db pool 38/40 connections in use` (3×) + `db connection
  wait 850ms`. Contradicted by `db p95 query 210ms (SLO 250ms) — within
  SLO` and in-window `db query ok` lines — the pool is hot but healthy.
- **C3 (decoy):** deploy v2.14.3 at 03:13:58 + config drift + first ERROR
  after deploy. Contradicted by `deploy v2.14.3 finished clean; no errors
  from new code paths`, health checks passing, 2 auth errors that
  pre-date the deploy, and the intermittent pay-gw successes.

Fix: **"Roll back gateway config to v2.14.2 and fail over to standby
gateway (pay-gw-2)"** — wording matches the fix the cold re-test tester
remembered. Expected effect, computed from the bundle's own log lines
(not invented): **error rate 61% → 9% in 90s** (checkout: 11/18 ERROR in
the window, 1/11 in the 90 s after the 03:19:30 fix).

## Scorer rules (plain language)

For each cause, the scorer adds up four inspectable parts (weights are
constants at the top of `scorer.py`):

1. **Support lines** — log lines whose message matches the cause's support
   patterns (e.g. C1: `connect timeout`, `upstream 504`). In-window lines
   count 5.0 × √n, out-of-window lines 3.0 × √n. Square-rooted so 41
   identical timeout lines dominate but do not swamp the decoys entirely.
2. **Pinned evidence** — each of the bundle's pinned-evidence ids that
   exists in the log lines *and* matches a support pattern adds 2.0,
   capped at 10. A pinned id that does not exist adds nothing.
3. **Error-rate spike** — for the cause's linked service, the in-window
   error rate minus the baseline error rate (2 min before the window),
   weighted 40.0 — but only the fraction of that service's in-window
   ERROR lines matching *this* cause's patterns counts (attribution).
   The deploy cause gets almost no credit for timeout errors it does not
   explain (attribution 0.024); the gateway cause gets all of it (1.0).
4. **Contradicting lines** — in-window healthy (`ok`) lines from the
   linked service, plus lines matching the cause's contradict patterns,
   subtract 1.5 (in-window) / 1.0 (out-of-window) each.

Scores are normalised to percentage shares; ties break by cause id, so
the output is fully deterministic: same bundle → same output,
byte-identical. Per-cause score breakdowns are in the output
(`breakdown: support / pinned / spike / contradict`).

## Collapse accounting (W2 input)

`scorer.collapse_accounting(bundle)` classifies all 200 lines exactly
once — kept-as-evidence vs discarded, labelled `duplicate` / `stale` /
`unrelated` (priority kept > duplicate > stale > unrelated):

```
200 lines → 3 causes
kept: 64   discarded: duplicate 119, stale 9, unrelated 8
```

**Constraint (BUILD-CONSTRAINTS.md #2, binding):** counters must always
read as remaining/discarded accounting — e.g. `200 lines → 3 causes`
with discards labelled — **never a bare "0 log lines" state** (it reads
as "no logs"). The scorer output carries a ready-made `collapse.label`
(`"200 lines -> 3 causes"`) for W2 to display; a test asserts the label
never contains "0 log lines" and the counts always sum to 200.

## How to run the tests

```bash
python3 -m unittest test_scorer -v
# or
python3 test_scorer.py
```

11 tests, all passing (26 Nov 2026). They cover: bundle validates
against the schema (hand-rolled checks, no `jsonschema` lib); exactly
200 log lines; exactly 3 causes ranked with C1 top (shares 82.9 / 10.9 /
6.2, within 1 point of the 82 / 11 / 7 spec); bundle support/contradict
counts agree with the scorer's; scorer deterministic across runs
(byte-identical); scorer never reads ground truth (source check +
delete/change tests); and the three perturbation checks below.

## Perturbation results (the build gate)

Logged by the tests (before → after, as `(cause, score, share%)`):

- **A — neutralise the 41 timeout lines** (message → `pay-gw ok 200`):
  ranking **flips**. Before: C1 79.78/82.9%, C2 10.5/10.9%, C3 5.93/6.2%.
  After: **C2 10.5/91.3%**, C1 0.5/4.3%, C3 0.5/4.3%. The scorer is
  sensitive to its input — the "agent" is not theatre.
- **B — remove the db-pool evidence** (4 pool/wait lines → `db query ok`):
  top cause **holds**. Before: C1 79.78/82.9%, C2 10.5/10.9%, C3 5.93/6.2%.
  After: C1 79.78/92.5%, C3 5.93/6.9%, C2 0.5/0.6%. The db-pool cause
  collapses (2nd→3rd) but C1 stays top — robustness.
- **C — move the deploy line 03:13:58 → 03:14:30** (into the window):
  ranking unchanged, C3 score moves 5.93 → 4.98 (its out-of-window
  support becomes in-window support at a different weight, and the
  in-window healthy-line accounting shifts). Informational only.

## Discrepancy note (refined design vs task spec)

`WAR-ROOM-REFINED-DESIGN.md` §1 shows the demo's Card 1 as **"Deploy
2.4.1 broke payment auth"** (4 support, 1 contradicts), with Card 2
"Database connection pool exhausted" and Card 3 "Upstream payments API
degraded". This W1 bundle follows the **task spec instead**: the top cause
is **"Payment gateway timeout" 82%**, decoys "Database connection pool
exhausted" 11% and "Bad deploy v2.14.3" 7%, and the deploy version is
**v2.14.3**, not 2.4.1. The refined design's §1 table is the cold-tested
*web prototype's* incident; the frozen bundle is a different incident
with the same three-card structure. W2's cards must use this bundle's
titles/counts, not the design doc's.

## What W2 needs from this (collapse input contract)

W2 consumes `incident-4815.json` + `scorer.score_bundle(bundle)`:

- `log_lines` — 200 lines, timestamp-sorted, ids `L001`–`L200`. The wall.
- `ranking` — 3 causes with `score`, `share_percent`, `support`,
  `contradict`, `pinned_evidence` (ids of real lines) and the `breakdown`.
  Each card shows its contradicting lines too (in `causes[].contradicting_evidence`).
- `collapse` — `{total_lines: 200, kept_lines: 64, discarded:
  {duplicate: 119, stale: 9, unrelated: 8}, causes: 3, label:
  "200 lines -> 3 causes"}`. Drive the counter from this; never display
  a "0 log lines" state.
- `fix` — `{title, steps[], expected_effect: "error rate 61% → 9% in
  90s"}`. Complete and ready for W3's fix panel.
- `service_states` — per-service state (`ok`/`degraded`/`down`/
  `recovering`) at 03:14–03:21, one snapshot per minute per service.
  This is W4's wavefront input: pay-gw goes `down` at 03:14, checkout
  follows at 03:15, everything else stays green, recovery after the
  03:19:30 fix.
- **Reject / re-rank (W3):** call `score_bundle` on a copy of the bundle
  with a cause removed from `causes` — it re-ranks the survivors with
  shares renormalised (tested: dropping C3 gives C1 88.4%, C2 11.6%).
  The grab must be a **visible action** (BUILD-CONSTRAINTS.md #6 — D2's
  lesson: an auto-fired reroute was invisible and failed its re-test):
  card scales/moves, others recede, fix panel appears — never
  auto-fired or a border-colour change only. The data for that is all
  here: per-cause evidence stays attached to its card, and the fix
  effect (`61% → 9%`) is data, not a static caption, so W3 can show it
  changing rather than frozen.

Other binding constraints from `../BUILD-CONSTRAINTS.md` that touch W2–W4
(recorded here so the contract does not contradict them): no frozen
screen — every beat keeps something moving (collapse streaming, card
entry, wavefront motion); no layout jump ~0:45 — frame stable for the
whole 60 s; timeline stays in view at 800 px height in manual mode too;
evidence type large (13–14 px is borderline at YouTube compression) and
the narration strip always visible and accurate. Standing: deterministic-
first / LLM-last, zero cloud calls by default, keyless, emulator-only,
airplane-mode cold start 5/5.
