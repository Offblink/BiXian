"""Two-tier eyes: the small eyes answer, and when they are not sure they hand the picture up.

Tier 1 -- the small eyes. The local 4B VLM (`vlm_pick.py`), 4-bit @1536: ~1.4 s and 5.8 GiB per
question once the weights are loaded. It is cheap, and it is right most of the time.

Tier 2 -- the big eyes. The model that called us. This script never guesses for it: when tier 1
answers UNDECIDED (peak below the policy threshold -- see `bixian/policies/default.json`) we
print the SAME picture tier 1 looked at, numbered exactly the same way, plus the option list that
maps a number back onto athand's numbering, and exit 3. `vlm_pick` writes that picture on every
run (`<listing>.som.png`), so both tiers really do look at one image.

The caller's protocol, in full:

    tier 1 said YES      -> stdout {"tier":1,"n":<athand number>,...}   exit 0 -> click --target n
    tier 1 said UNDECIDED -> stdout {"tier":2,"decision":"ESCALATE","som":<png>,"options":[...]}
                             exit 3 -> read <png> yourself, answer with an "our" number,
                                       map it through "options" to an athand number, click that.
    no candidates / error -> exit 2

The point of the split is that "not sure" must cost a bigger model, never a coin flip: the small
eyes' UNDECIDED is a hand-off, not an answer.

One-shot (weights load, one question):

    python eyes.py --listing "$TEMP/athand/657094.json" --frame shot.png --intent "点击发送按钮"

Holding the weights for a loop (what a driver should do; the load costs more than the forward):

    python eyes.py --loop --k 9 --policy default        # then one json request per stdin line
"""
import argparse
import contextlib
import json
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
PICK = HERE / "vlm_pick.py"


class SmallEyes:
    """Tier 1, held open: one long-lived `vlm_pick.py --loop` child, weights loaded once."""

    def __init__(self, k=9, policy="default", policy_file=None):
        argv = [sys.executable, str(PICK), "--loop", "--k", str(k), "--policy", policy]
        if policy_file:
            argv += ["--policy-file", str(policy_file)]
        self.child = subprocess.Popen(argv, cwd=str(HERE), stdin=subprocess.PIPE,
                                      stdout=subprocess.PIPE, stderr=None,
                                      text=True, encoding="utf-8", bufsize=1)

    def ask(self, req):
        # The child runs with cwd=HERE, so a path the caller wrote relative to *its* cwd must be
        # made absolute before it crosses the process boundary. Measured 2026-09-23: from the
        # project root, `--listing vlm-probe\cases\x.json` reached the child as
        # `vlm-probe\vlm-probe\cases\x.json` -> "no candidates"/"no frame image" -> needless
        # ESCALATE, while the exact same command worked from inside vlm-probe\.
        req = {k: (str(pathlib.Path(v).resolve()) if k in ("listing", "frame") and v else v)
               for k, v in req.items()}
        self.child.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
        self.child.stdin.flush()
        line = self.child.stdout.readline()
        if not line:
            return {"decision": "UNDECIDED", "reason": "the small eyes exited"}
        return json.loads(line)

    def close(self):
        with contextlib.suppress(Exception):
            self.child.terminate()


def answer(result):
    """Turn one vlm_pick result into the tiered answer the caller acts on."""
    if result.get("decision") == "YES":
        return {"tier": 1, "decision": "YES", "n": result["n"], "name": result.get("name", ""),
                "p": result.get("p"), "confidence": result.get("confidence"),
                "threshold": result.get("threshold"), "policy": result.get("policy")}, 0
    if result.get("reason") in ("no candidates in the listing", "no frame image"):
        return {"tier": 1, "decision": "UNDECIDED", "reason": result["reason"]}, 2
    # A real "not sure": hand the picture up, with everything needed to answer it.
    return {
        "tier": 2, "decision": "ESCALATE",
        "reason": "small eyes below threshold, or it declined to answer",
        "som": result.get("som"), "options": result.get("options", []),
        "guess": {"n": result.get("n"), "name": result.get("name", "")},
        "p": result.get("p"), "confidence": result.get("confidence"),
        "threshold": result.get("threshold"), "policy": result.get("policy"),
        "candidates": result.get("candidates", []),
    }, 3


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listing")
    ap.add_argument("--intent")
    ap.add_argument("--frame")
    ap.add_argument("--k", type=int, default=9)
    ap.add_argument("--policy", default="default")
    ap.add_argument("--policy-file", default=None)
    ap.add_argument("--loop", action="store_true", help="one request per stdin json line")
    a = ap.parse_args()

    eyes = SmallEyes(a.k, a.policy, a.policy_file)
    try:
        if a.loop:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue
                try:
                    out, code = answer(eyes.ask(json.loads(line)))
                except Exception as exc:  # noqa: BLE001
                    out, code = {"tier": 1, "decision": "UNDECIDED", "reason": str(exc)}, 2
                print(json.dumps(out, ensure_ascii=False), flush=True)
                if code == 3:
                    print("  -> escalate: read %s and answer with an option number"
                          % out.get("som"), file=sys.stderr)
            return 0
        if not (a.listing and a.intent):
            ap.error("--listing and --intent are required unless --loop is given")
        req = {"listing": a.listing, "intent": a.intent}
        if a.frame:
            req["frame"] = a.frame
        out, code = answer(eyes.ask(req))
        print(json.dumps(out, ensure_ascii=False))
        if code == 3:
            print("-> escalate: read %s and answer with an option number (see \"options\")" % out.get("som"),
                  file=sys.stderr)
        return code
    finally:
        eyes.close()


if __name__ == "__main__":
    sys.exit(main())
