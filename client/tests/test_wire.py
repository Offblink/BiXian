"""Wire normalization: the two shapes that break naive clients.

The fixtures below are verbatim kev responses (docs/design-handoff.md section 3.2).
"""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bixian import wire  # noqa: E402
from bixian.errors import Undecided  # noqa: E402

KEV_CHOICE = {
    "type": "choice",
    "choice": "returns",
    "confidence": 0.21,
    "probabilities": {"returns": 0.47, "shipping": 0.28, "billing": 0.25},
}

KEV_SCORE = {
    "type": "score",
    "score": 1.44,
    "confidence": 0.78,
    "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
    "probabilities": {"0": 0.00, "1": 0.56, "2": 0.44},
}


class TestScore(unittest.TestCase):
    def test_continuous_score_with_index_keyed_legend(self):
        got = wire.read_score(KEV_SCORE, ["Calm", "Frustrated", "Very angry"])
        self.assertEqual(got["index"], 1)
        self.assertEqual(got["label"], "Frustrated")
        self.assertEqual(got["score"], 1.44)
        self.assertEqual(got["legend"], ["Calm", "Frustrated", "Very angry"])

    def test_score_clamped_into_the_legend(self):
        got = wire.read_score(dict(KEV_SCORE, score=7.9), ["a", "b", "c"])
        self.assertEqual(got["index"], 2)

    def test_list_legend_still_accepted(self):
        got = wire.read_score({"type": "score", "score": 2, "legend": ["a", "b", "c"]}, ["a", "b", "c"])
        self.assertEqual(got["label"], "c")

    def test_criteria_go_out_as_strings(self):
        question = wire.q_score("how angry", ["Calm", "Frustrated"])
        self.assertEqual(question["criteria"], ["Calm", "Frustrated"])


class TestChoice(unittest.TestCase):
    def test_peak_is_the_probability_and_confidence_is_the_margin(self):
        got = wire.read_choice(KEV_CHOICE, ["returns", "shipping", "billing"])
        self.assertEqual(got["peak"], 0.47)
        self.assertEqual(got["margin"], 0.21)
        self.assertNotEqual(got["peak"], got["margin"])

    def test_unknown_choice_is_refused(self):
        with self.assertRaises(Undecided):
            wire.read_choice(dict(KEV_CHOICE, choice="nonsense"), ["returns", "shipping"])


class TestUniform(unittest.TestCase):
    def test_flat_distribution_is_flagged(self):
        flat = {"a": 0.3333, "b": 0.3333, "c": 0.3333}
        self.assertTrue(wire.is_uniform(flat, 3))

    def test_two_way_tie_is_flagged(self):
        self.assertTrue(wire.is_uniform({"yes": 0.5, "no": 0.5}, 2))

    def test_a_real_answer_is_not_flagged(self):
        self.assertFalse(wire.is_uniform({"a": 0.90, "b": 0.07, "c": 0.03}, 3))

    def test_flat_kev_score_is_flagged(self):
        answer = {"type": "score", "score": 0.44, "legend": {"0": "x", "1": "y"},
                  "probabilities": {"0": 0.5, "1": 0.5}}
        self.assertTrue(wire.read_score(answer, ["x", "y"])["uniform"])


class TestNoul(unittest.TestCase):
    def test_probability_survives(self):
        self.assertEqual(wire.read_noul({"type": "noul", "noul": 0.93}), 0.93)

    def test_missing_probability_is_undecided_not_zero(self):
        with self.assertRaises(Undecided):
            wire.read_noul({"type": "noul"})


if __name__ == "__main__":
    unittest.main()
