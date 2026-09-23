"""Pick ONE number out of an athand listing, the way the ecosystem does it -- but local.

The architecture is not invented here. It is what every working Jev computer-use project
does (fastbrowse "picks instead of generating"; jev-browser-use "Jev clicks, Codex thinks
and verifies"; droidjev "AX -> typed pick -> adb"):

    index the screen into candidates  ->  pick one  ->  code verifies and executes

athand already owns the first and third parts (`targets` numbers the candidates; `verify`
checks frames/values; ESCALATED stops after three dead attempts). The middle part is
explicitly left to the caller, and that is the only thing this file adds.

Two deliberate choices about the readout, both from AnyJev's measured results:

  * **Cyclic-shift marginalization.** Ask the same K candidates in K rotations, so every
    candidate sits at every position once, and combine in log space. Raw readout flips its
    answer when options are reversed in 0.227 of cases -- and does it at 1.00 confidence.
  * **Prior correction, without labels.** Ask the same candidate set against an empty state
    to estimate the model's label prior, then divide it out: a question the model leans on
    regardless of content ("is this spam?" with the body replaced by N/A) is the case where
    a raw probability lies.

Together these are AnyJev's L0: no labels, and auto-decidable traffic goes from 7.7% to
47.7% in their benchmark. What it does NOT do is fix the model's knowledge -- if the pick
is wrong on merit, this makes it wrong more consistently.

    python pick_l0.py --listing "%TEMP%\\athand\\4653616.json" --intent "点击发送按钮"

Prints the number to hand to athand (`click --target N`), or UNDECIDED so the caller keeps
its old path. It clicks nothing itself.
"""
import argparse
import json
import math
import pathlib
import sys
import time

DECIDE_SRC = pathlib.Path(__file__).resolve().parents[2] / "client" / "src"
sys.path.insert(0, str(DECIDE_SRC))

from bixian import client, policy, wire        # noqa: E402
from bixian.errors import Undecided            # noqa: E402

EMPTY_STATE = "N/A"          # the "no content" state the prior is measured against


def shortlist(listing_path, intent, sources=None, limit=8, order="original"):
    """The candidates worth asking about, filtered deterministically.

    A 4B model asked to choose among 44 anonymous shapes is being set up to fail; among six
    named ones it has a chance. Filtering here is free and keeps athand's own numbering.
    """
    data = json.loads(pathlib.Path(listing_path).read_text(encoding="utf-8"))
    rect = tuple(data.get("rect") or ())
    rows = []
    for rec in data.get("targets") or []:
        name = (rec.get("name") or "").strip()
        if not name:                                  # an unnamed shape needs eyes, not text
            continue
        src = rec.get("source") or "a11y"
        if sources and src not in sources:
            continue
        box = tuple(rec.get("rect") or ())
        if rect and len(box) == 4 and (box[2] <= rect[0] or box[0] >= rect[2]
                                       or box[3] <= rect[1] or box[1] >= rect[3]):
            continue                                  # off-window: cannot be acted on anyway
        rows.append({"n": rec.get("n"), "name": name, "cls": rec.get("cls") or "", "source": src})
    return data, rows


def overlap(intent, name):
    a = {intent[i:i + 2] for i in range(len(intent) - 1)}
    b = {name[i:i + 2] for i in range(len(name) - 1)}
    return len(a & b)


def ask(url, model, state, rows, instructions, timeout, key=None):
    """One request: the K candidates as a choice question, in the given order (rows is the order)."""
    criteria = {str(r["n"]): "%s (%s, %s)" % (r["name"], r["cls"] or "no class", r["source"]) for r in rows}
    payload = {"state": state, "questions": {"q": wire.q_choice(instructions, criteria)}}
    if model:
        payload["model"] = model
    data = client.post(url, payload, timeout, key=key)
    probs = data["answers"]["q"].get("probabilities") or {}
    return {int(k): float(v) for k, v in probs.items()}


def cyclic_shift(url, model, state, rows, instructions, timeout, key=None, max_perm=None):
    """K rotations, averaged in log space: every candidate occupies every position once.

    This is the correction AnyJev calls L0 and it costs K prefills; the state prefix is
    identical across them, so the model re-encodes the same state K times -- the price of
    not having a position-biased answer.
    """
    n = len(rows) if max_perm is None else min(len(rows), max_perm)
    logsum = {r["n"]: 0.0 for r in rows}
    for i in range(n):
        rotated = rows[i:] + rows[:i]
        probs = ask(url, model, state, rotated, instructions, timeout, key)
        for k, p in probs.items():
            logsum[k] += math.log(max(p, 1e-9))
    avg = {k: v / n for k, v in logsum.items()}
    top = max(avg.values())
    exp = {k: math.exp(v - top) for k, v in avg.items()}
    z = sum(exp.values())
    return {k: v / z for k, v in exp.items()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listing", required=True)
    ap.add_argument("--intent", required=True)
    ap.add_argument("--source", default="")
    ap.add_argument("--max", type=int, default=8, help="shortlist size (shrink the candidate set, keep numbering)")
    ap.add_argument("--perms", type=int, default=0, help="rotations; 0 = all K, cap it to save time")
    ap.add_argument("--url", default="http://127.0.0.1:8009")
    ap.add_argument("--model", default="kev-latest")
    ap.add_argument("--policy", default="default")
    ap.add_argument("--no-prior", action="store_true", help="skip the label-prior correction")
    ap.add_argument("--expect", default="")
    a = ap.parse_args()

    sources = {s.strip() for s in a.source.split(",") if s.strip()} or None
    data, rows = shortlist(a.listing, a.intent, sources)
    if not rows:
        print("UNDECIDED no named candidates (a picture-only listing needs eyes)"); return 2
    # keep the literal-overlap ranking only to choose WHO gets asked; the model keeps athand's numbers
    if len(rows) > a.max:
        rows = sorted(rows, key=lambda r: (-overlap(a.intent, r["name"]), r["n"]))[: a.max]
    title = data.get("title") or ""
    state = "Window: %s\n\nTask: %s\n\nNumbered controls in this window:\n%s" % (
        title, a.intent,
        "\n".join("%d. %s (%s, %s)" % (r["n"], r["name"], r["cls"] or "no class", r["source"]) for r in rows))
    instructions = "Which numbered control is the one this task has to operate?"

    entry = policy.resolve(policy.load(), a.policy)
    threshold = entry["threshold"]
    t0 = time.perf_counter()
    try:
        probs = cyclic_shift(a.url, a.model, state, rows, instructions, 10.0, os_key(), a.perms or None)
        if not a.no_prior:
            prior = ask(a.url, a.model, EMPTY_STATE, rows, instructions, 10.0, os_key())
            probs = {k: probs[k] / max(prior.get(k, 1e-9), 1e-9) for k in probs}
            z = sum(probs.values()); probs = {k: v / z for k, v in probs.items()}
    except Undecided as exc:
        print("UNDECIDED service: %s" % exc); return 2
    dt = time.perf_counter() - t0

    order = sorted(probs.items(), key=lambda kv: -kv[1])
    names = {r["n"]: r["name"] for r in rows}
    print("asked %d candidates, %d rotations%s, %.0f ms" % (
        len(rows), a.perms or len(rows), "" if a.no_prior else " + prior", dt * 1000))
    for n, p in order:
        print("   #%-3s %-24s %7.4f  %s" % (n, names[n][:24], p, "#" * int(p * 50)))
    top_n, top_p = order[0]
    peak = top_p
    decision, code = policy.decide_noul(peak, threshold, threshold)
    print("\npicked #%s %s   p=%.4f  threshold=%.2f -> %s" % (
        top_n, names[top_n], peak, threshold, decision))
    if a.expect:
        print("expected #%s -> %s" % (a.expect, "HIT" if str(top_n) == a.expect else "MISS"))
    if decision != "YES":
        print("-> UNDECIDED: the caller keeps its own path (re-list, read the frame, or a bigger model)")
        return 2
    print("-> athand: click --target %s" % top_n)
    return 0


def os_key():
    import os
    return os.environ.get("DECIDE_KEY")


if __name__ == "__main__":
    sys.exit(main())
