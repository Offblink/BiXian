#!/usr/bin/env python3
"""Score the service on a frozen labelled set: accuracy, ECE, Brier, and the
share of decisions you could automate at a given error budget.

    python tools/eval.py evals/frozen/example.jsonl --url http://127.0.0.1:8009 --budget 0.05

Row shape (one JSON object per line):

    {"state": "...", "type": "noul|choice|score", "question": "...",
     "criteria": <same shape the wire wants> | ["low", ..., "high"], "truth": <label|index|0|1>}

Why this exists (docs/design-handoff.md section 4.1): thresholds are ESTIMATED,
not searched. This script produces the risk-coverage curve that a threshold is
read off from -- without it, thresholds are guesses.
"""

import argparse
import json
import math
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bixian import client, wire  # noqa: E402
from bixian.errors import Undecided  # noqa: E402


def load_rows(path):
    rows = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            rows.append(json.loads(line))
    return rows


def expected_labels(row):
    if row["type"] == "choice":
        return list(row["criteria"]) if isinstance(row["criteria"], dict) else list(row["criteria"])
    if row["type"] == "score":
        return list(row["criteria"])
    return ["no", "yes"]


def truth_index(row, labels):
    truth = row["truth"]
    if row["type"] == "noul":
        return 1 if truth in (1, "yes", "true", True) else 0
    if row["type"] == "choice":
        return labels.index(truth) if truth in labels else -1
    if isinstance(truth, int):
        return truth
    return labels.index(truth) if truth in labels else -1


def predict(url, row, timeout, key=None):
    payload = {"state": row["state"]}
    if row["type"] == "noul":
        payload["questions"] = {"q": wire.q_noul(row["question"])}
    elif row["type"] == "choice":
        payload["questions"] = {"q": wire.q_choice(row["question"], row["criteria"])}
    else:
        payload["questions"] = {"q": wire.q_score(row["question"], row["criteria"])}
    data = client.post(url, payload, timeout, key=key)
    return data["answers"]["q"]


def probs_for(row, answer, labels):
    """A probability vector over `labels`, plus the confidence we would threshold on."""
    if row["type"] == "noul":
        p = wire.read_noul(answer)
        return [1.0 - p, p], p
    if row["type"] == "choice":
        got = wire.read_choice(answer, labels)
        return [float(got["probabilities"].get(lb, 0.0)) for lb in labels], float(got["peak"] or 0.0)
    got = wire.read_score(answer, labels)
    probs = [float(got["probabilities"].get(str(i), 0.0)) for i in range(len(labels))]
    return probs, float(max(probs) if probs else 0.0)


def brier(probs, index):
    return sum((p - (1.0 if i == index else 0.0)) ** 2 for i, p in enumerate(probs))


def ece(pairs, bins=10):
    """pairs: (confidence, correct). Expected calibration error, equal-width bins."""
    if not pairs:
        return 0.0
    total = len(pairs)
    error = 0.0
    for b in range(bins):
        low, high = b / bins, (b + 1) / bins
        bucket = [(c, ok) for c, ok in pairs
                  if (c > low or (b == 0 and c >= low)) and c <= high]
        if not bucket:
            continue
        conf = sum(c for c, _ in bucket) / len(bucket)
        acc = sum(1 for _, ok in bucket if ok) / len(bucket)
        error += (len(bucket) / total) * abs(acc - conf)
    return error


def automation_share(pairs, budget):
    """Largest fraction of cases we can auto-accept while keeping error <= budget."""
    if not pairs:
        return 0.0
    best = 0.0
    ordered = sorted(pairs, key=lambda pair: -pair[0])
    for cut in range(1, len(ordered) + 1):
        window = ordered[:cut]
        error = 1.0 - sum(1 for _, ok in window if ok) / len(window)
        if error <= budget:
            best = cut / len(ordered)
        else:
            break
    return best


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("frozen_set")
    ap.add_argument("--url", default="http://127.0.0.1:8009/v1/systemone")
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--budget", type=float, default=0.05)
    ap.add_argument("--bins", type=int, default=10)
    args = ap.parse_args()

    rows = load_rows(args.frozen_set)
    if not rows:
        sys.exit("no rows in %s" % args.frozen_set)

    per_type = {}
    pairs = []
    skipped = 0
    for row in rows:
        labels = expected_labels(row)
        try:
            answer = predict(args.url, row, args.timeout)
        except Undecided as exc:
            skipped += 1
            sys.stderr.write("undecided: %s (%s)\n" % (exc, row.get("question", "")[:60]))
            continue
        try:
            probs, confidence = probs_for(row, answer, labels)
        except Undecided as exc:
            skipped += 1
            sys.stderr.write("unreadable answer: %s\n" % exc)
            continue
        index = truth_index(row, labels)
        if index < 0:
            skipped += 1
            sys.stderr.write("truth %r not in %r\n" % (row["truth"], labels))
            continue
        predicted = max(range(len(probs)), key=lambda i: probs[i]) if probs else -1
        correct = predicted == index
        pairs.append((confidence, correct))
        stats = per_type.setdefault(row["type"], {"n": 0, "correct": 0, "brier": 0.0})
        stats["n"] += 1
        stats["correct"] += 1 if correct else 0
        stats["brier"] += brier(probs, index)

    print("rows=%d scored=%d skipped=%d" % (len(rows), len(pairs), skipped))
    for kind, stats in sorted(per_type.items()):
        print("  %-7s n=%-4d accuracy=%.3f  brier=%.3f" % (
            kind, stats["n"], stats["correct"] / stats["n"], stats["brier"] / stats["n"]))
    print("overall   accuracy=%.3f  ece=%.3f  automation@%g=%.2f" % (
        sum(1 for _, ok in pairs if ok) / len(pairs) if pairs else math.nan,
        ece(pairs, args.bins),
        args.budget,
        automation_share(pairs, args.budget),
    ))
    print("NOTE: thresholds are read off this table, not searched "
          "(docs/design-handoff.md section 4.1). Re-run after any model or temperature change.")


if __name__ == "__main__":
    main()
