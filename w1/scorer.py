#!/usr/bin/env python3
"""Deterministic scorer for War-Room incident bundles (W1).

Stdlib only, no network, no LLM. Reads an incident bundle (format
version 1.0.0) and ranks its 3 causes by inspectable rules applied to
the log lines. It NEVER reads `ground_truth_cause_id` — that field is
for verification only, and tests prove the scorer ignores it.

Scoring rules (per cause, see RULES below):
  1. Support lines: log lines whose message matches one of the cause's
     support patterns. In-window lines count more than out-of-window
     ones, and the count is square-rooted so 41 identical timeout lines
     dominate but do not swamp the decoys entirely.
  2. Pinned evidence: each of the bundle's pinned-evidence ids that
     exists in the log lines AND matches a support pattern adds points
     (capped). A pinned id that does not exist adds nothing.
  3. Error-rate spike: for the cause's linked service, the error rate
     inside the incident window minus the baseline error rate (the 2
     minutes before the window). Only the fraction of the service's
     in-window ERROR lines that match this cause's support patterns
     counts toward this cause (attribution) — a deploy cause does not
     get credit for timeout errors it does not explain.
  4. Contradicting lines: in-window healthy ("ok") lines from the
     linked service, plus lines matching the cause's contradict
     patterns (anywhere in the bundle), subtract points.

Scores are normalised to percentage shares. Ranking ties break by
cause id, so output is fully deterministic: same bundle -> same
output, byte-identical.

Usage: python3 scorer.py incident-4815.json
"""
import json
import math
import sys
from datetime import datetime, timedelta

# --- Tunable weights -------------------------------------------------------
SUPPORT_IN_W = 5.0      # per sqrt(support lines inside the incident window)
SUPPORT_OUT_W = 3.0     # per sqrt(support lines outside the window)
PINNED_W = 2.0          # per pinned-evidence line that matches a support pattern
PINNED_CAP = 10.0       # cap on the pinned-evidence contribution
SPIKE_W = 40.0          # weight on (error-rate spike x attribution)
CONTRADICT_IN_W = 1.5   # per contradicting line inside the window
CONTRADICT_OUT_W = 1.0  # per contradicting line outside the window
SCORE_FLOOR = 0.5       # no cause scores below this (keeps shares defined)

# --- Per-cause rules, keyed by cause id ------------------------------------
# Patterns are case-insensitive substrings matched against log messages.
# A new incident template needs a new entry here; the rules are the
# scorer's knowledge, the bundle's log lines are its evidence.
RULES = {
    "C1": {
        "title": "Payment gateway timeout",
        "support_patterns": ["connect timeout", "upstream 504"],
        "contradict_patterns": ["(intermittent success)"],
    },
    "C2": {
        "title": "Database connection pool exhausted",
        "support_patterns": ["pool", "connection wait"],
        "contradict_patterns": ["within SLO"],
    },
    "C3": {
        "title": "Bad deploy v2.14.3",
        "support_patterns": ["config drift", "first ERROR after deploy",
                             "finished;"],
        "contradict_patterns": ["finished clean", "no errors from new code paths",
                                "health checks passing", "pre-dates deploy"],
    },
}


def _parse_ts(value):
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _matches(message, patterns):
    lowered = message.lower()
    return any(p.lower() in lowered for p in patterns)


def collapse_accounting(bundle):
    """Remaining/discarded accounting for the W2 collapse.

    Every log line is classified exactly once:
      kept      — evidence for some cause (matches a support or contradict
                  pattern, is a pinned/contradicting evidence id, or is an
                  in-window healthy line from a linked service).
      duplicate — discarded; same (service, message) as an earlier line.
      stale     — discarded; timestamp before the incident window.
      unrelated — discarded; everything else.
    Priority is kept > duplicate > stale > unrelated, so the counts are
    deterministic. The counter W2 shows is "200 lines -> 3 causes" with
    these discard labels — NEVER a bare "0 log lines" state (that reads
    as "no logs"; see BUILD-CONSTRAINTS.md constraint 2).
    """
    lines = bundle["log_lines"]
    window_start = _parse_ts(bundle["incident_window"]["start"])
    window_end = _parse_ts(bundle["incident_window"]["end"])
    evidence_ids = set()
    for cause in bundle["causes"]:
        evidence_ids.update(cause["pinned_evidence"])
        evidence_ids.update(cause["contradicting_evidence"])
    all_support = [p for r in RULES.values() for p in r["support_patterns"]]
    all_contradict = [p for r in RULES.values() for p in r["contradict_patterns"]]
    linked_services = {c["linked_service"] for c in bundle["causes"]}

    def in_window(ln):
        t = _parse_ts(ln["timestamp"])
        return window_start <= t < window_end

    kept = 0
    discarded = {"duplicate": 0, "stale": 0, "unrelated": 0}
    seen = set()
    for ln in lines:
        is_evidence = (
            ln["id"] in evidence_ids
            or _matches(ln["message"], all_support)
            or _matches(ln["message"], all_contradict)
            or (ln["service"] in linked_services and ln["level"] == "INFO"
                and "ok" in ln["message"].lower() and in_window(ln))
        )
        if is_evidence:
            kept += 1
        else:
            key = (ln["service"], ln["message"])
            if key in seen:
                discarded["duplicate"] += 1
            elif _parse_ts(ln["timestamp"]) < window_start:
                discarded["stale"] += 1
            else:
                discarded["unrelated"] += 1
        seen.add((ln["service"], ln["message"]))
    return {
        "total_lines": len(lines),
        "kept_lines": kept,
        "discarded": discarded,
        "causes": len(bundle["causes"]),
        "label": "%d lines -> %d causes" % (len(lines), len(bundle["causes"])),
    }


def score_bundle(bundle):
    """Rank the bundle's causes. Returns a JSON-serialisable dict."""
    lines = bundle["log_lines"]
    by_id = {ln["id"]: ln for ln in lines}
    window_start = _parse_ts(bundle["incident_window"]["start"])
    window_end = _parse_ts(bundle["incident_window"]["end"])
    baseline_start = window_start - timedelta(minutes=2)

    def in_window(ln):
        t = _parse_ts(ln["timestamp"])
        return window_start <= t < window_end

    def in_baseline(ln):
        t = _parse_ts(ln["timestamp"])
        return baseline_start <= t < window_start

    def error_rate(service, predicate):
        svc_lines = [ln for ln in lines
                     if ln["service"] == service and predicate(ln)]
        if not svc_lines:
            return 0.0, 0
        errors = sum(1 for ln in svc_lines if ln["level"] == "ERROR")
        return errors / len(svc_lines), errors

    ranked = []
    for cause in bundle["causes"]:
        rules = RULES[cause["id"]]
        linked = cause["linked_service"]

        support_in = [ln for ln in lines
                      if _matches(ln["message"], rules["support_patterns"])
                      and in_window(ln)]
        support_out = [ln for ln in lines
                       if _matches(ln["message"], rules["support_patterns"])
                       and not in_window(ln)]

        # Contradicting lines: in-window healthy lines from the linked
        # service, plus any line matching a contradict pattern.
        contradict_ids = set()
        for ln in lines:
            if (ln["service"] == linked and ln["level"] == "INFO"
                    and "ok" in ln["message"].lower() and in_window(ln)):
                contradict_ids.add(ln["id"])
            if _matches(ln["message"], rules["contradict_patterns"]):
                contradict_ids.add(ln["id"])
        contradict_in = [by_id[i] for i in contradict_ids if in_window(by_id[i])]
        contradict_out = [by_id[i] for i in contradict_ids
                          if not in_window(by_id[i])]

        # Pinned evidence that exists and matches a support pattern.
        pinned_existing = [pid for pid in cause["pinned_evidence"] if pid in by_id]
        pinned_matched = [pid for pid in pinned_existing
                          if _matches(by_id[pid]["message"],
                                      rules["support_patterns"])]

        # Error-rate spike for the linked service, attributed to this cause.
        err_w, _ = error_rate(linked, in_window)
        err_b, _ = error_rate(linked, in_baseline)
        spike = max(0.0, err_w - err_b)
        window_errors = [ln for ln in lines
                         if ln["service"] == linked and in_window(ln)
                         and ln["level"] == "ERROR"]
        matching_errors = [ln for ln in window_errors
                           if _matches(ln["message"], rules["support_patterns"])]
        attribution = (len(matching_errors) / len(window_errors)
                       if window_errors else 0.0)

        support_score = (SUPPORT_IN_W * math.sqrt(len(support_in))
                         + SUPPORT_OUT_W * math.sqrt(len(support_out)))
        pinned_score = min(PINNED_CAP, PINNED_W * len(pinned_matched))
        spike_score = SPIKE_W * spike * attribution
        contradict_score = (CONTRADICT_IN_W * len(contradict_in)
                            + CONTRADICT_OUT_W * len(contradict_out))
        total = max(SCORE_FLOOR,
                    support_score + pinned_score + spike_score - contradict_score)

        ranked.append({
            "cause_id": cause["id"],
            "title": cause["title"],
            "score": round(total, 2),
            "support": len(support_in) + len(support_out),
            "support_in_window": len(support_in),
            "contradict": len(contradict_in) + len(contradict_out),
            "contradict_in_window": len(contradict_in),
            "pinned_evidence": sorted(pinned_existing),
            "pinned_matched": len(pinned_matched),
            "spike": round(spike, 3),
            "spike_attribution": round(attribution, 3),
            "breakdown": {
                "support": round(support_score, 2),
                "pinned": round(pinned_score, 2),
                "spike": round(spike_score, 2),
                "contradict": round(-contradict_score, 2),
            },
        })

    total_score = sum(r["score"] for r in ranked)
    for r in ranked:
        r["share_percent"] = round(100.0 * r["score"] / total_score, 1)
    # Highest score first; cause id breaks ties -> deterministic.
    ranked.sort(key=lambda r: (-r["score"], r["cause_id"]))
    for i, r in enumerate(ranked, start=1):
        r["rank"] = i

    return {
        "incident_id": bundle["incident_id"],
        "format_version": bundle["format_version"],
        "seed": bundle["seed"],
        "ranking": ranked,
        "collapse": collapse_accounting(bundle),
        "method": "deterministic rules: support patterns + pinned evidence + "
                  "error-rate spike x attribution - contradicting lines",
    }


def main(argv):
    if len(argv) != 2:
        print("usage: python3 scorer.py <incident-bundle.json>", file=sys.stderr)
        return 2
    with open(argv[1]) as f:
        bundle = json.load(f)
    result = score_bundle(bundle)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
