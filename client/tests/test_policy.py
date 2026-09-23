"""Policy: thresholds, the strictness band, and the origin guard."""

import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bixian import policy  # noqa: E402
from bixian.errors import Undecided  # noqa: E402


class TestResolve(unittest.TestCase):
    def test_named_policies(self):
        pol = policy.load()
        self.assertEqual(policy.resolve(pol, "explore")["threshold"], 0.20)
        self.assertEqual(policy.resolve(pol, "irreversible")["on_fail"], "fail_closed")
        self.assertEqual(policy.resolve(pol, "run_command")["policy"], "irreversible")

    def test_unknown_class_falls_back_to_the_strictest_policy(self):
        entry = policy.resolve(policy.load(), "delete_everything")
        self.assertFalse(entry["declared"])
        self.assertEqual(entry["policy"], "irreversible")
        self.assertEqual(entry["on_fail"], "fail_closed")

    def test_shipped_policy_file_matches_the_builtin_defaults(self):
        shipped = json.loads((ROOT / policy.POLICY_FILE).read_text(encoding="utf-8"))
        self.assertEqual(shipped["unknown_class"], "irreversible")
        for name, entry in policy.DEFAULT_POLICY["named"].items():
            self.assertEqual(shipped["named"][name]["threshold"], entry["threshold"], name)
            self.assertEqual(shipped["named"][name]["on_fail"], entry["on_fail"], name)


class TestBand(unittest.TestCase):
    def test_over_the_bar(self):
        self.assertEqual(policy.decide_noul(0.83, 0.5, 0.5), ("YES", 0))

    def test_under_the_bar(self):
        self.assertEqual(policy.decide_noul(0.17, 0.5, 0.5), ("NO", 1))

    def test_strict_policy_reports_not_sure_instead_of_no(self):
        # 0.83 clears the plain boundary but not an irreversible requirement:
        # the honest answer is "cannot decide", NOT "no".
        self.assertEqual(policy.decide_noul(0.83, 0.5, 0.99), ("UNDECIDED", 2))

    def test_strict_policy_still_says_no_when_it_is_clearly_no(self):
        self.assertEqual(policy.decide_noul(0.17, 0.5, 0.99), ("NO", 1))


class TestOrigin(unittest.TestCase):
    def test_irreversible_refuses_untrusted_state(self):
        entry = policy.resolve(policy.load(), "run_command")
        with self.assertRaises(Undecided):
            policy.require_origin(entry, "untrusted")

    def test_irreversible_accepts_trusted_state(self):
        entry = policy.resolve(policy.load(), "run_command")
        policy.require_origin(entry, "trusted")

    def test_read_only_policy_does_not_care(self):
        entry = policy.resolve(policy.load(), "read_file")
        policy.require_origin(entry, "untrusted")


if __name__ == "__main__":
    unittest.main()
