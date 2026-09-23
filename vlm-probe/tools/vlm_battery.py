"""One model load, one resolution, every suite: the child process of matrix.py.

Split out of matrix.py because of a measured trap: several resolutions inside one process do not
measure the same thing (4-bit @1024 read 884 ms after native had run, 368 ms on its own). So one
process per (weights, resolution), all images inside it, one RESULT line of json for the parent.

    python vlm_battery.py --model ..\\models\\qwen3vl-4b --edge 1024 --4bit \
        --suites "som.png:som.cases;d_notepad.som.png:d_notepad.cases"

Suite spec is `image:cases[:K]`; K defaults to 9 (the option slots are the tokens for 1..9).
The readout is vlm_pick.Reader -- the same code the seam uses, so a number here means something
about the thing that will actually run.
"""
import argparse
import json
import pathlib
import statistics
import sys
import time

import torch
from PIL import Image

HERE = pathlib.Path(__file__).resolve().parent
if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))      # vlm_pick.py lives at the probe root, not in tools/

import vlm_pick  # noqa: E402

CASES = HERE.parent / "cases"   # suite specs name images/cases relative to here; keep them short
THRESHOLD = 0.5          # policies/default.json "default"; a wrong pick above it is not catchable


def resolve(p):
    """A suite/image path is relative to `cases/` (absolute paths pass through)."""
    p = pathlib.Path(p)
    return p if p.is_absolute() else CASES / p


def read_cases(path):
    cases = []
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            intent, _, idx = line.rpartition("=")
            cases.append((intent.strip(), int(idx)))
    return cases


def run_suite(reader, image, cases, k):
    img = Image.open(image).convert("RGB")
    rows = []
    for intent, expect in cases:
        probs, diag = reader.ask(img, intent, k)
        top = max(probs, key=probs.get)
        rows.append({"suite": pathlib.Path(image).stem, "q": intent, "expect": expect,
                     "picked": top, "hit": top == expect, "p": probs[top], "ms": diag["ms"]})
        print("   %s #%s p=%.4f %5.0f ms  «%s»%s"
              % ("HIT " if top == expect else "MISS", top, probs[top], diag["ms"], intent,
                 "" if top == expect else "  期望 #%d" % expect), flush=True)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--edge", type=int, default=1024)
    ap.add_argument("--4bit", dest="four_bit", action="store_true")
    ap.add_argument("--suites", default="")
    ap.add_argument("--json", default="")
    a = ap.parse_args()

    suites = []
    for spec in a.suites.split(";"):
        spec = spec.strip()
        if not spec:
            continue
        parts = spec.split(":")
        image = resolve(parts[0])
        cases = read_cases(resolve(parts[1]))
        k = int(parts[2]) if len(parts) > 2 else 9
        suites.append((image, cases, k))
    if not suites:
        sys.exit("no suites")

    reader = vlm_pick.Reader(a.model, four_bit=a.four_bit, edge=a.edge)
    print("loaded %.1fs | edge %d | %s | suites %d | card %.2f GiB"
          % (reader.load_s, a.edge, "4-bit" if a.four_bit else "bf16", len(suites),
             torch.cuda.get_device_properties(0).total_memory / 2**30), flush=True)

    # one throwaway forward: the first one pays CUDA warm-up (measured 552 ms against a 190 ms median)
    img0 = Image.open(resolve(suites[0][0])).convert("RGB")
    reader.ask(img0, suites[0][1][0][0], suites[0][2])

    rows = []
    oom = False
    for image, cases, k in suites:
        print(f"== {image} ({len(cases)} questions)", flush=True)
        try:
            rows += run_suite(reader, resolve(image), cases, k)
        except torch.cuda.OutOfMemoryError as exc:
            oom = True
            print("   OOM: %s" % str(exc)[:120], flush=True)
            break

    ms = [r["ms"] for r in rows]
    hits = sum(r["hit"] for r in rows)
    confident_wrong = [r for r in rows if not r["hit"] and r["p"] >= THRESHOLD]
    result = {
        "edge": a.edge,
        "four_bit": a.four_bit,
        "hits": hits,
        "total": len(rows),
        "median_ms": int(statistics.median(ms)),
        "min_ms": int(min(ms)),
        "max_ms": int(max(ms)),
        "peak_gib": round(torch.cuda.max_memory_allocated() / 2**30, 2),
        "confident_wrong": len(confident_wrong),
        "error": "OOM" if oom else "",
        "misses": [{"suite": r["suite"], "q": r["q"], "expect": r["expect"], "picked": r["picked"],
                    "p": round(r["p"], 4)} for r in rows if not r["hit"]],
        "cases": rows,
    }
    print("TOTAL %d/%d  中位 %d ms  峰值 %.2f GiB  高置信错 %d"
          % (hits, len(rows), result["median_ms"], result["peak_gib"], result["confident_wrong"]), flush=True)
    print("RESULT " + json.dumps(result, ensure_ascii=False), flush=True)
    if a.json:
        pathlib.Path(a.json).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
