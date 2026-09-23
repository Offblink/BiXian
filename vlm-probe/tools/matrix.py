"""Model x resolution matrix: which combination is actually best for the pick.

Two measurement rules, both learned the hard way:

  * **One resolution per process.** Several resolutions inside one process do not measure the
    same thing -- 4-bit @1024 read 884 ms after native had run, 368 ms on its own. So every
    (weights, edge) pair gets its own `vlm_battery.py` child, all images inside it.
  * **Three desktops, not one.** som.png alone is 7 questions on one image: the same question
    flips hit/miss across resolutions, which is noise. `make_desktops.py` adds an English menu
    desktop and a Chinese dialog desktop, 24 questions in total.

Every row carries the two numbers that decide this: accuracy, and how often a WRONG pick came in
above the policy threshold (0.5) -- the failure mode the fail-open design cannot catch.

    python matrix.py --model ..\\models\\qwen3vl-4b --config both --tag 4b
    python matrix.py --model ..\\models\\qwen3vl-8b --config 4bit --tag 8b
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
SCRATCH = pathlib.Path(r"C:\tmp\scratch\athand-bixian")
SUITES = ("som.png:som.cases;d_notepad.som.png:d_notepad.cases;"
          "d_settings.som.png:d_settings.cases;d_browser.som.png:d_browser.cases")

# bf16 has no headroom above ~1536 on this card (1568 peaks at 11.49 of 11.94 GiB and the native
# frame spills to host memory -> 133 s/forward), so it only gets the sane half.
EDGES = {
    "4bit": [768, 1024, 1280, 1536, 1792, 2240],
    "bf16": [768, 1024, 1280, 1536],
}


def run_edge(python, model, edge, four_bit, suites, timeout):
    cmd = [python, str(HERE / "vlm_battery.py"), "--model", model, "--edge", str(edge),
           "--suites", suites]
    if four_bit:
        cmd.append("--4bit")
    proc = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                          timeout=timeout)
    out = proc.stdout or ""
    for line in reversed(out.splitlines()):
        if line.startswith("RESULT "):
            row = json.loads(line[len("RESULT "):])
            row["returncode"] = proc.returncode
            row["config"] = "4bit" if four_bit else "bf16"
            return row, out
    return ({"edge": edge, "config": "4bit" if four_bit else "bf16", "returncode": proc.returncode,
             "error": "no RESULT", "tail": out[-400:], "stderr": (proc.stderr or "")[-300:]}, out)


def per_suite(row):
    out = {}
    for case in row.get("cases", []):
        hit, total = out.get(case["suite"], (0, 0))
        out[case["suite"]] = (hit + int(case["hit"]), total + 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--config", default="both", choices=["4bit", "bf16", "both"])
    ap.add_argument("--suites", default=SUITES)
    ap.add_argument("--edges", default="", help="override the per-config default list")
    ap.add_argument("--tag", default="")
    ap.add_argument("--timeout", type=int, default=2400)
    a = ap.parse_args()

    tag = a.tag or pathlib.Path(a.model).name
    rows = []
    for cfg in (["4bit", "bf16"] if a.config == "both" else [a.config]):
        for edge in ([int(x) for x in a.edges.split(",") if x.strip()] or EDGES[cfg]):
            print(f"== {tag} {cfg} edge={edge}", flush=True)
            row, out = run_edge(sys.executable, a.model, edge, cfg == "4bit", a.suites, a.timeout)
            rows.append(row)
            if row.get("error") and not row.get("total"):
                print(f"   {row['error']}  {row.get('tail', '')[-160:]}", flush=True)
                continue
            print("   %d/%d  中位 %d ms (min %d, max %d)  峰值 %.2f GiB  高置信错 %d%s"
                  % (row["hits"], row["total"], row["median_ms"], row["min_ms"], row["max_ms"],
                     row["peak_gib"], row["confident_wrong"],
                     "   " + row["error"] if row.get("error") else ""), flush=True)
            for suite, (hit, total) in sorted(per_suite(row).items()):
                print("      %-12s %d/%d" % (suite, hit, total), flush=True)

    ok = [r for r in rows if r.get("total")]
    print("\n== ranking (accuracy desc, then median forward asc) ==", flush=True)
    print("   %-5s %-5s %7s %9s %9s %6s  per-suite" % ("cfg", "edge", "acc", "median", "peak", "conf-wrong"), flush=True)
    for r in sorted(ok, key=lambda r: (-r["hits"], r["median_ms"])):
        suites = " ".join("%s %d/%d" % (s, h, t) for s, (h, t) in sorted(per_suite(r).items()))
        print("   %-5s %-5d %4d/%d %7d ms %7.2f GiB %6d  %s"
              % (r["config"], r["edge"], r["hits"], r["total"], r["median_ms"], r["peak_gib"],
                 r["confident_wrong"], suites), flush=True)
    for r in rows:
        if not r.get("total"):
            print("   %-5s %-5d %s" % (r["config"], r["edge"], r.get("error")), flush=True)

    dest = SCRATCH / f"matrix-{tag}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nrows -> {dest}", flush=True)
    for cfg in ("4bit", "bf16"):
        rs = [r for r in ok if r["config"] == cfg]
        if rs:
            best = max(rs, key=lambda r: (r["hits"], r["confident_wrong"] == 0, -r["median_ms"]))
            print("%s: best %d/%d @edge %d (%d ms, %.2f GiB, %d 高置信错)"
                  % (cfg, best["hits"], best["total"], best["edge"], best["median_ms"],
                     best["peak_gib"], best["confident_wrong"]), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
