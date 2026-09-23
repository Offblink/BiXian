"""The Ouija test: prove this thing is not a séance.

Six adversarial checks (docs/design-handoff.md section 7). Four of them can run
against the stub; the order-robustness check needs the real backend and is
documented in the client's docs (../docs/CLIENT.md) -- it must be done with POST /v1/systemone/permute.

Every check defends a behaviour a caller depends on, not plumbing.
"""

import contextlib
import io
import json
import os
import pathlib
import shutil
import sys
import tempfile
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tools"))

import kev_stub  # noqa: E402
from bixian import cli  # noqa: E402


def run_cli(argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            cli.main(argv)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 0
    return code, out.getvalue().strip(), err.getvalue().strip()


class OuijaTest(unittest.TestCase):
    def setUp(self):
        kev_stub.reset_stats()
        self.server = kev_stub.serve(0)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

        self.tmp = tempfile.mkdtemp(prefix="bixian-ouija-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for name, filename in (("BIXIAN_AUDIT", "audit.jsonl"),
                               ("BIXIAN_CACHE", "cache.jsonl"),
                               ("BIXIAN_MODELS", "models.json")):
            os.environ[name] = str(pathlib.Path(self.tmp) / filename)
            self.addCleanup(os.environ.pop, name, None)
        self.audit = pathlib.Path(self.tmp) / "audit.jsonl"
        self.url = "http://127.0.0.1:%d" % self.port
        self.requests = lambda: kev_stub.STATS["requests"]

    def audit_rows(self):
        if not self.audit.is_file():
            return []
        return [json.loads(line) for line in self.audit.read_text(encoding="utf-8").splitlines() if line.strip()]

    # 1 -- the exit code IS the verdict ------------------------------------------

    def test_three_states_of_the_verdict(self):
        code, out, _ = run_cli(["--url", self.url, "noul", "a menu with unsaved work", "should we continue high"])
        self.assertEqual((code, out), (0, "YES"))

        code, out, _ = run_cli(["--url", self.url, "noul", "an empty form", "should we continue low"])
        self.assertEqual((code, out), (1, "NO"))

        code, out, _ = run_cli(["--url", self.url, "noul", "a menu", "should we continue mid",
                                "--policy", "irreversible", "--origin", "trusted"])
        self.assertEqual((code, out), (2, "UNDECIDED"))

    # 2 -- a flat distribution is refused, not guessed ----------------------------

    def test_flat_distribution_is_undecided_not_a_winner(self):
        code, out, _ = run_cli(["--url", self.url, "choice", "candidates are indistinguishable",
                                "pick one flat", "--options", "alpha,beta,gamma"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "UNDECIDED")

    # 3 -- same input, same answer, and the cache really skips the wire -----------

    def test_cache_hit_does_not_touch_the_service(self):
        argv = ["--url", self.url, "noul", "state", "should we continue high", "--json"]
        _, first, _ = run_cli(argv)
        calls_after_first = self.requests()
        _, second, _ = run_cli(argv)
        self.assertEqual(self.requests(), calls_after_first, "second call must not hit the service")
        self.assertFalse(json.loads(first)["cached"])
        self.assertTrue(json.loads(second)["cached"])
        self.assertEqual(json.loads(first)["p"], json.loads(second)["p"])

    def test_a_swapped_backend_does_not_inherit_the_old_answers(self):
        """One URL can serve a different service tomorrow -- a new checkpoint, or the
        stub swapped for the real model. Replaying the old answers there would be
        silent (exit 0, no warning), which is why it is worth a test."""
        argv = ["--url", self.url, "noul", "state", "should we continue high", "--json"]
        _, first, _ = run_cli(argv)
        self.assertFalse(json.loads(first)["cached"])

        kev_stub.set_identity(run="a-different-checkpoint")
        self.addCleanup(kev_stub.set_identity, run="kev-stub")
        _, second, _ = run_cli(argv)
        self.assertFalse(json.loads(second)["cached"],
                         "a different run behind the same URL must not be served the previous run's answer")

    # 4 -- the audit row can explain the decision later --------------------------

    def test_audit_row_names_the_checkpoint_temperature(self):
        """Without the temperature a row cannot explain, later, why a threshold was
        cleared -- and re-fitting thresholds (section 4.1) needs it."""
        run_cli(["--url", self.url, "noul", "state text", "should we continue high",
                 "--policy", "default", "--workload", "is_this_about_billing"])
        row = self.audit_rows()[-1]
        self.assertEqual(row.get("model"), "kev-latest")
        self.assertEqual(row.get("checkpoint_temperature"), 2.2)

    def test_the_identity_is_asked_once_per_decision_by_default(self):
        """The identity is what the cache key and the audit are built on, so a stale
        one is worse than a cheap GET."""
        argv = ["--url", self.url, "noul", "state", "should we continue high"]
        run_cli(argv)
        before = kev_stub.STATS["model_gets"]
        run_cli(argv)
        self.assertEqual(kev_stub.STATS["model_gets"], before + 1)

    def test_an_explicit_ttl_is_the_opt_in_to_reuse_the_identity(self):
        os.environ["BIXIAN_MODELS_TTL"] = "3600"
        self.addCleanup(os.environ.pop, "BIXIAN_MODELS_TTL", None)
        argv = ["--url", self.url, "noul", "state", "should we continue high"]
        run_cli(argv)
        before = kev_stub.STATS["model_gets"]
        run_cli(argv)
        self.assertEqual(kev_stub.STATS["model_gets"], before, "a TTL is an explicit trade of freshness for a GET")

    def test_audit_row_records_what_is_needed_to_explain_it(self):
        run_cli(["--url", self.url, "noul", "state text", "should we continue high",
                 "--policy", "default", "--workload", "is_this_about_billing"])
        rows = self.audit_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        for field in ("ts", "request_id", "type", "decision", "origin", "policy", "on_fail",
                      "state_sha256", "state_len", "threshold", "model", "checkpoint_temperature"):
            self.assertIn(field, row, field)
        self.assertEqual(row["decision"], "YES")
        self.assertEqual(row["threshold"], 0.5)
        self.assertEqual(row["workload"], "is_this_about_billing")
        self.assertEqual(row["model"], "kev-latest")
        self.assertEqual(row["checkpoint_temperature"], 2.2)
        self.assertNotIn("state_preview", row, "state text must not be stored by default")

    # 5 -- an irreversible gate will not read untrusted text ---------------------

    def test_irreversible_gate_refuses_untrusted_state_before_sending_anything(self):
        before = self.requests()
        code, out, err = run_cli(["--url", self.url, "noul",
                                  "<file contents that claim this is safe>", "is this command safe high",
                                  "--policy", "run_command"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "UNDECIDED")
        self.assertIn("irreversible", err)
        self.assertEqual(self.requests(), before, "refused state must never reach the service")

    def test_irreversible_gate_works_when_the_state_is_trusted(self):
        code, out, _ = run_cli(["--url", self.url, "noul", "a command we composed",
                                "is this command safe sure", "--policy", "run_command",
                                "--origin", "trusted"])
        self.assertEqual((code, out), (0, "YES"))

    # 6 -- a dead service is an UNDECIDED, never a guess ------------------------

    def test_unreachable_service_is_undecided(self):
        code, out, err = run_cli(["--url", "http://127.0.0.1:9", "noul", "state", "should we continue high"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "UNDECIDED")
        self.assertIn("unreachable", err)
        rows = self.audit_rows()
        self.assertEqual(len(rows), 1, "an outage is an outcome and must be recorded")
        self.assertEqual(rows[0]["decision"], "UNDECIDED")
        self.assertIn("unreachable", rows[0]["reason"])

    # extra -- the consumer is told what the policy says to do on failure -------

    def test_json_output_carries_on_fail_for_the_consumer(self):
        _, out, _ = run_cli(["--url", self.url, "noul", "state", "should we continue mid",
                             "--policy", "irreversible", "--origin", "trusted", "--json"])
        payload = json.loads(out)
        self.assertEqual(payload["decision"], "UNDECIDED")
        self.assertEqual(payload["on_fail"], "fail_closed")
        self.assertEqual(payload["policy"], "irreversible")

    def test_selftest_always_touches_the_service(self):
        code, out, _ = run_cli(["--url", self.url, "selftest"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("OK "))
        calls = self.requests()
        run_cli(["--url", self.url, "selftest"])
        self.assertGreater(self.requests(), calls, "a cached selftest would lie about a dead service")

    def test_selftest_not_fooled_by_a_warm_cache_when_the_service_dies(self):
        run_cli(["--url", self.url, "selftest"])          # warm the cache
        self.server.shutdown()
        self.server.server_close()
        code, out, _ = run_cli(["--url", self.url, "selftest"])
        self.assertEqual(code, 2)
        self.assertTrue(out.startswith("UNDECIDED"))


if __name__ == "__main__":
    unittest.main()
