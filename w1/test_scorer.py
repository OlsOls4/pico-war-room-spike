#!/usr/bin/env python3
"""Tests for the W1 frozen bundle + deterministic scorer.

Stdlib unittest only (no jsonschema lib — schema validation is hand-rolled
in test_bundle_validates_against_schema).

Run:  python3 -m unittest test_scorer -v
       python3 test_scorer.py

Perturbation checks (the build gate from the spike plan):
  A. Neutralising the 41 pay-gw timeout lines MUST move the ranking
     (the top cause flips to the db-pool decoy).
  B. Removing the db-pool evidence must NOT flip the top cause.
  C. Shifting the deploy timestamp is logged before/after (informational).
"""
import copy
import json
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE_PATH = os.path.join(HERE, "incident-4815.json")
SCHEMA_PATH = os.path.join(HERE, "incident-bundle.schema.json")
SCORER_PATH = os.path.join(HERE, "scorer.py")

from scorer import score_bundle  # noqa: E402

SERVICES = {"checkout", "pay-gw", "inventory", "shipping", "db", "auth"}
LEVELS = {"INFO", "WARN", "ERROR"}
STATES = {"ok", "degraded", "down", "recovering"}
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ID_RE = re.compile(r"^L\d{3}$")


def load_bundle():
    with open(BUNDLE_PATH) as f:
        return json.load(f)


def ranking_summary(result):
    """[(cause_id, score, share), ...] in rank order — for logging."""
    return [(r["cause_id"], r["score"], r["share_percent"])
            for r in result["ranking"]]


class TestBundleSchema(unittest.TestCase):
    def test_bundle_validates_against_schema(self):
        """Hand-rolled validation of every rule in incident-bundle.schema.json."""
        with open(SCHEMA_PATH) as f:
            schema = json.load(f)
        self.assertEqual(schema["properties"]["format_version"]["const"], "1.0.0")
        b = load_bundle()

        # Top-level required keys, no extras.
        required = set(schema["required"])
        self.assertEqual(set(b), required)
        self.assertEqual(b["format_version"], "1.0.0")
        self.assertRegex(b["incident_id"], r"^INC-[0-9]+$")
        self.assertIsInstance(b["seed"], int)
        self.assertTrue(b["title"])
        self.assertTrue(TS_RE.match(b["incident_window"]["start"]))
        self.assertTrue(TS_RE.match(b["incident_window"]["end"]))
        self.assertLess(b["incident_window"]["start"], b["incident_window"]["end"])

        # Log lines: exactly 200, sorted, well-formed.
        lines = b["log_lines"]
        self.assertEqual(len(lines), 200)
        self.assertEqual([ln["id"] for ln in lines],
                         ["L%03d" % i for i in range(1, 201)])
        prev = ""
        for ln in lines:
            self.assertEqual(set(ln), {"id", "timestamp", "service", "level", "message"})
            self.assertRegex(ln["id"], r"^L[0-9]{3}$")
            self.assertTrue(TS_RE.match(ln["timestamp"]))
            self.assertGreaterEqual(ln["timestamp"], prev)
            prev = ln["timestamp"]
            self.assertIn(ln["service"], SERVICES)
            self.assertIn(ln["level"], LEVELS)
            self.assertTrue(ln["message"])
        line_ids = {ln["id"] for ln in lines}

        # Causes: exactly 3, unique ids, evidence ids must exist.
        causes = b["causes"]
        self.assertEqual(len(causes), 3)
        self.assertEqual(len({c["id"] for c in causes}), 3)
        for c in causes:
            self.assertEqual(set(c), {"id", "title", "linked_service",
                                      "support_count", "contradict_count",
                                      "pinned_evidence", "contradicting_evidence"})
            self.assertIn(c["linked_service"], SERVICES)
            self.assertGreaterEqual(c["support_count"], 0)
            self.assertGreaterEqual(c["contradict_count"], 0)
            for pid in c["pinned_evidence"] + c["contradicting_evidence"]:
                self.assertIn(pid, line_ids, "evidence id %s not in log_lines" % pid)

        # Service states: per-minute snapshots 03:14–03:21, valid states.
        for svc, states in b["service_states"].items():
            self.assertIn(svc, SERVICES)
            self.assertEqual(len(states), 8)
            for s in states:
                self.assertEqual(set(s), {"timestamp", "state"})
                self.assertIn(s["state"], STATES)
            stamps = [s["timestamp"] for s in states]
            self.assertEqual(stamps, sorted(stamps))
            self.assertTrue(stamps[0].endswith("T03:14:00Z"))
            self.assertTrue(stamps[-1].endswith("T03:21:00Z"))

        # Fix + ground truth reference.
        fix = b["fix"]
        self.assertEqual(set(fix), {"title", "steps", "expected_effect"})
        self.assertTrue(fix["title"])
        self.assertGreaterEqual(len(fix["steps"]), 1)
        self.assertIn("61%", fix["expected_effect"])
        self.assertIn(b["ground_truth_cause_id"], {c["id"] for c in causes})

    def test_bundle_counts_match_scorer_counts(self):
        """The bundle's authored support/contradict counts must agree with
        what the scorer derives from the log lines."""
        b = load_bundle()
        result = score_bundle(b)
        by_id = {r["cause_id"]: r for r in result["ranking"]}
        for c in b["causes"]:
            self.assertEqual(by_id[c["id"]]["support"], c["support_count"],
                             c["id"])
            self.assertEqual(by_id[c["id"]]["contradict"], c["contradict_count"],
                             c["id"])

    def test_pinned_evidence_is_real(self):
        """Every pinned evidence id is a real log line, and the key ones
        are the lines the task spec names."""
        b = load_bundle()
        by_id = {ln["id"]: ln for ln in b["log_lines"]}
        timeouts = [ln for ln in b["log_lines"]
                    if "connect timeout after 30s" in ln["message"]]
        self.assertEqual(len(timeouts), 41)
        self.assertEqual(timeouts[0]["timestamp"], "2026-11-26T03:14:07Z")
        c1 = next(c for c in b["causes"] if c["id"] == "C1")
        for pid in c1["pinned_evidence"]:
            self.assertIn(pid, by_id)
        pinned_msgs = {by_id[pid]["message"] for pid in c1["pinned_evidence"]}
        self.assertTrue(any("upstream 504" in m for m in pinned_msgs))
        self.assertTrue(any("config drift" in m for m in pinned_msgs))


class TestScorer(unittest.TestCase):
    def test_exactly_three_causes_ranked_top_is_payment_gateway(self):
        b = load_bundle()
        result = score_bundle(b)
        self.assertEqual(len(result["ranking"]), 3)
        self.assertEqual([r["rank"] for r in result["ranking"]], [1, 2, 3])
        top = result["ranking"][0]
        self.assertEqual(top["cause_id"], "C1")
        self.assertEqual(top["title"], "Payment gateway timeout")
        # Shares match the task spec (82 / 11 / 7) within tolerance.
        shares = {r["cause_id"]: r["share_percent"] for r in result["ranking"]}
        self.assertAlmostEqual(shares["C1"], 82, delta=4)
        self.assertAlmostEqual(shares["C2"], 11, delta=4)
        self.assertAlmostEqual(shares["C3"], 7, delta=4)
        self.assertGreater(shares["C1"], shares["C2"])
        self.assertGreater(shares["C2"], shares["C3"])
        self.assertAlmostEqual(sum(shares.values()), 100, delta=0.5)

    def test_scorer_is_deterministic(self):
        """Same bundle -> byte-identical output, across runs."""
        b = load_bundle()
        out1 = json.dumps(score_bundle(b), indent=2, sort_keys=True)
        out2 = json.dumps(score_bundle(copy.deepcopy(b)), indent=2, sort_keys=True)
        self.assertEqual(out1, out2)

    def test_scorer_never_reads_ground_truth(self):
        """Deleting or changing ground_truth_cause_id must not change output.
        Also: the scorer source must not reference the field at all."""
        with open(SCORER_PATH) as f:
            source = f.read()
        # The module docstring may name the field to explain the rule;
        # the executable code must not reference it. Strip the docstring.
        code = re.sub(r'""".*?"""', '', source, count=1, flags=re.S)
        self.assertNotIn("ground_truth", code)

        b = load_bundle()
        expected = json.dumps(score_bundle(b), sort_keys=True)

        no_truth = copy.deepcopy(b)
        del no_truth["ground_truth_cause_id"]
        self.assertEqual(json.dumps(score_bundle(no_truth), sort_keys=True),
                         expected)

        wrong_truth = copy.deepcopy(b)
        wrong_truth["ground_truth_cause_id"] = "C2"
        self.assertEqual(json.dumps(score_bundle(wrong_truth), sort_keys=True),
                         expected)

    def test_perturbation_timeout_lines_flip_ranking(self):
        """PERTURBATION A (must move the ranking): neutralise all 41
        pay-gw connect-timeout lines -> the payment-gateway cause collapses
        and the db-pool decoy becomes top."""
        b = load_bundle()
        before = score_bundle(b)
        print("\nPerturbation A (neutralise 41 timeout lines):")
        print("  before:", ranking_summary(before))

        perturbed = copy.deepcopy(b)
        n = 0
        for ln in perturbed["log_lines"]:
            if "connect timeout after 30s" in ln["message"]:
                ln["message"] = "pay-gw ok 200 36ms"
                ln["level"] = "INFO"
                n += 1
        self.assertEqual(n, 41)
        after = score_bundle(perturbed)
        print("  after: ", ranking_summary(after))

        self.assertNotEqual(ranking_summary(before), ranking_summary(after))
        self.assertNotEqual(after["ranking"][0]["cause_id"], "C1")
        self.assertEqual(after["ranking"][0]["cause_id"], "C2")

    def test_perturbation_db_pool_evidence_keeps_top_cause(self):
        """PERTURBATION B (robustness): removing the db-pool evidence must
        NOT flip the top cause."""
        b = load_bundle()
        before = score_bundle(b)
        print("\nPerturbation B (remove db-pool evidence):")
        print("  before:", ranking_summary(before))

        perturbed = copy.deepcopy(b)
        n = 0
        for ln in perturbed["log_lines"]:
            if ln["service"] == "db" and (
                    "pool" in ln["message"] or "connection wait" in ln["message"]):
                ln["message"] = "db query ok 11ms"
                ln["level"] = "INFO"
                n += 1
        self.assertEqual(n, 4)
        after = score_bundle(perturbed)
        print("  after: ", ranking_summary(after))

        self.assertEqual(after["ranking"][0]["cause_id"], "C1")
        # The db-pool cause itself must collapse (its evidence is gone),
        # which may reorder 2nd/3rd — only the top cause is required to hold.
        c2_before = next(r for r in before["ranking"] if r["cause_id"] == "C2")
        c2_after = next(r for r in after["ranking"] if r["cause_id"] == "C2")
        self.assertLess(c2_after["score"], c2_before["score"])

    def test_perturbation_deploy_timestamp_logged(self):
        """PERTURBATION C (informational): shifting the deploy line from
        03:13:58 to inside the window (03:14:30). Logged before/after;
        no flip is asserted — the point is the scorer responds to its input."""
        b = load_bundle()
        before = score_bundle(b)
        print("\nPerturbation C (deploy 03:13:58 -> 03:14:30):")
        print("  before:", ranking_summary(before))

        perturbed = copy.deepcopy(b)
        moved = 0
        for ln in perturbed["log_lines"]:
            if ln["message"].startswith("pay-gw deploy v2.14.3 finished;"):
                ln["timestamp"] = "2026-11-26T03:14:30Z"
                moved += 1
        self.assertEqual(moved, 1)
        after = score_bundle(perturbed)
        print("  after: ", ranking_summary(after))
        # The scorer must at least register the change in the deploy cause.
        c3_before = next(r for r in before["ranking"] if r["cause_id"] == "C3")
        c3_after = next(r for r in after["ranking"] if r["cause_id"] == "C3")
        self.assertNotEqual(c3_before["score"], c3_after["score"])

    def test_collapse_accounting_sums_and_never_zero(self):
        """W2 input contract (BUILD-CONSTRAINTS.md constraint 2): the
        collapse accounting must always read as remaining/discarded counts,
        never a bare '0 log lines' state."""
        from scorer import collapse_accounting
        b = load_bundle()
        acc = collapse_accounting(b)
        print("\nCollapse accounting:", acc)
        self.assertEqual(acc["total_lines"], 200)
        self.assertEqual(acc["kept_lines"]
                         + sum(acc["discarded"].values()), 200)
        self.assertEqual(set(acc["discarded"]),
                         {"duplicate", "stale", "unrelated"})
        self.assertGreater(acc["kept_lines"], 0)
        self.assertEqual(acc["causes"], 3)
        self.assertEqual(acc["label"], "200 lines -> 3 causes")
        self.assertNotIn("0 log lines", acc["label"])
        # Same accounting rides on the scorer output.
        self.assertEqual(score_bundle(b)["collapse"], acc)

    def test_reject_rerank_w3_contract(self):
        """W3 reject path: dropping a cause from a copy of the bundle and
        re-scoring must re-rank the survivors (shares renormalise, top
        cause holds). This is the data contract W3's visible grab relies on."""
        b = load_bundle()
        reduced = copy.deepcopy(b)
        reduced["causes"] = [c for c in reduced["causes"] if c["id"] != "C3"]
        result = score_bundle(reduced)
        print("\nReject C3, re-ranked:",
              ranking_summary(result))
        self.assertEqual(len(result["ranking"]), 2)
        self.assertEqual(result["ranking"][0]["cause_id"], "C1")
        self.assertAlmostEqual(
            sum(r["share_percent"] for r in result["ranking"]), 100, delta=0.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
