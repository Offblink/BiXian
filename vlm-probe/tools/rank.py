"""Combine every matrix-*.json into one ranking: which model x resolution to actually use.

matrix.py ranks inside one model (its children each load one model); this reads all the dumps and
puts them on the same axes -- accuracy, then the uncatchable failure (a wrong pick above the
policy threshold), then latency, then memory.

    python rank.py                # every C:\\tmp\\scratch\\athand-bixian\\matrix-*.json
    python rank.py --tag 4b       # just one
"""
import argparse
import json
import pathlib
import sys

SCRATCH = pathlib.Path(r"C:\tmp\scratch\athand-bixian")


def load(tag=None):
    rows = []
    for path in sorted(SCRATCH.glob(f"matrix-{tag}.json" if tag else "matrix-*.json")):
        model = path.stem[len("matrix-"):]
        for row in json.loads(path.read_text(encoding="utf-8")):
            if row.get("total"):
                row["model"] = model
                rows.append(row)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="")
    ap.add_argument("--threshold", type=float, default=0.5)
    a = ap.parse_args()
    rows = load(a.tag or None)
    if not rows:
        sys.exit("no matrix dumps yet")
    rows = [r for r in rows if r.get("edge")]

    print("%-9s %-5s %-5s %7s %9s %9s %6s" % ("model", "cfg", "edge", "acc", "median", "peak", "conf-wrong"))
    for r in sorted(rows, key=lambda r: (-r["hits"], r["confident_wrong"], r["median_ms"], r["peak_gib"])):
        print("%-9s %-5s %-5d %4d/%d %7d ms %7.2f GiB %6d"
              % (r["model"], r["config"], r["edge"], r["hits"], r["total"], r["median_ms"],
                 r["peak_gib"], r["confident_wrong"]))

    perfect = [r for r in rows if r["hits"] == r["total"] and not r["confident_wrong"]]
    if perfect:
        best = min(perfect, key=lambda r: (r["median_ms"], r["peak_gib"]))
        print("\n== 全对且无高置信错，里最快的组合 ==")
        print("   %s %s edge=%d  %d/%d  %d ms  %.2f GiB"
              % (best["model"], best["config"], best["edge"], best["hits"], best["total"],
                 best["median_ms"], best["peak_gib"]))
    slowest_ok = max(rows, key=lambda r: (r["hits"], -r["confident_wrong"]))
    print("\n== 准确率最高（并列时取高置信错最少、再取最快）==")
    print("   %s %s edge=%d  %d/%d  %d ms  %.2f GiB  高置信错 %d"
          % (slowest_ok["model"], slowest_ok["config"], slowest_ok["edge"], slowest_ok["hits"],
             slowest_ok["total"], slowest_ok["median_ms"], slowest_ok["peak_gib"],
             slowest_ok["confident_wrong"]))
    for r in rows:
        for miss in r.get("misses", []):
            print("   miss %-9s %-5s %-5d %-12s #%d p=%.4f  «%s»"
                  % (r["model"], r["config"], r["edge"], miss["suite"], miss["picked"], miss["p"],
                     miss["q"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
