"""pick -- choose ONE candidate number out of an athand listing, locally.

athand hands numbers out and never chooses between them: coordinates are refused
on purpose (a model's coordinates missed by 15-68 px; picking a number hit 3/3),
and "there is nobody to ask" is why the same target failing three times prints
ESCALATED instead of a question. So the choice lands on the caller, and today the
caller has two extremes:

  * substring match, first hit   -- instant, and silently wrong when two rows match
                                    (`_find_label` returns hits[0])
  * read the numbered PNG        -- accurate, and one large-model round trip per step

This is the middle rung. The candidates ARE text (a11y names, OCR text, labels the
caller gave), and "which of these N is the thing I mean" is exactly the narrow,
repetitive, cheap-to-be-wrong judgement the local decision service exists for.

    python pick.py --listing "%TEMP%\\athand\\4653616.json" --intent "send the file to Blinvo"

Prints the number to act on (or UNDECIDED, and the caller keeps its old path: read
the frame, or ask a bigger model). Nothing here clicks anything.
"""

import argparse
import json
import pathlib
import statistics
import sys
import time

DECIDE_SRC = pathlib.Path(__file__).resolve().parents[2] / "client" / "src"
sys.path.insert(0, str(DECIDE_SRC))

from bixian import client, policy, wire        # noqa: E402
from bixian.errors import Undecided            # noqa: E402

DEFAULT_URL = "http://127.0.0.1:8009"
DEFAULT_MODEL = "kev-latest"


def load_candidates(listing_path, intent, sources, visible_only=True, limit=8):
    """The listing as athand wrote it, reduced to the candidates worth asking about.

    Filtering here is deterministic and free, and it is the same thing a person does
    before reading a list: drop what cannot be the target (no name, a source the
    caller does not want), and keep the ones whose text has some of the intent in it.
    A 4B model asked to choose between 44 anonymous shapes is being set up to fail;
    asked to choose between 6 named ones it has a chance.
    """
    data = json.loads(pathlib.Path(listing_path).read_text(encoding="utf-8"))
    rect = tuple(data.get("rect") or ())
    rows = []
    for rec in data.get("targets") or []:
        name = (rec.get("name") or "").strip()
        if not name:
            continue                       # an unnamed shape needs eyes, not a text model
        if sources and (rec.get("source") or "a11y") not in sources:
            continue
        box = tuple(rec.get("rect") or ())
        if visible_only and rect and len(box) == 4:
            if box[2] <= rect[0] or box[0] >= rect[2] or box[3] <= rect[1] or box[1] >= rect[3]:
                continue                   # scrolled out of the window: cannot be clicked anyway
        rows.append({"n": rec.get("n"), "name": name, "cls": rec.get("cls") or "",
                     "source": rec.get("source") or "a11y", "rect": box, "overlap": overlap(intent, name)})
    rows.sort(key=lambda r: (-r["overlap"], r["rect"][1] if len(r["rect"]) == 4 else 0))
    return data, rows[:limit], len(rows)


def overlap(intent, name):
    """Cheap literal overlap between the intent and a candidate name.

    Character bigrams, because this is Chinese as often as English and words are not
    separated. It only orders the shortlist; the model still makes the choice.
    """
    a = {intent[i:i + 2] for i in range(len(intent) - 1)}
    b = {name[i:i + 2] for i in range(len(name) - 1)}
    return len(a & b)


def build_question(intent, window_title, rows):
    table = "\n".join(
        "%d. %s (%s, %s)" % (r["n"], r["name"], r["cls"] or "no class", r["source"]) for r in rows)
    state = ("Window: %s\n\nTask: %s\n\nNumbered controls in this window:\n%s" % (window_title, intent, table))
    criteria = {str(r["n"]): "%s (%s, %s)" % (r["name"], r["cls"] or "no class", r["source"]) for r in rows}
    question = wire.q_choice(
        "Which numbered control is the one this task has to operate?", criteria)
    return state, question, criteria


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listing", required=True)
    ap.add_argument("--intent", required=True)
    ap.add_argument("--source", default="", help="comma list: a11y,ocr,shape (default: all)")
    ap.add_argument("--max", type=int, default=8)
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--policy", default="default")
    ap.add_argument("--all", action="store_true", help="keep candidates with no literal overlap")
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args()

    sources = {s.strip() for s in a.source.split(",") if s.strip()} or None
    data, rows, total = load_candidates(a.listing, a.intent, sources, limit=1000)
    if not rows:
        print("UNDECIDED no named candidates in this listing (a picture-only listing needs eyes)")
        return 2
    rows = rows[:a.max]
    state, question, criteria = build_question(a.intent, data.get("title") or "", rows)

    entry = policy.resolve(policy.load(), a.policy)
    boundary = entry["threshold"]
    print("%d/%d candidates kept, asking %s (threshold %.2f)" % (len(rows), total, a.policy, boundary))
    for r in rows:
        print("   #%-3s %-28s %-22s %s" % (r["n"], r["name"][:28], (r["cls"] or "-")[:22], r["source"]))
    timings, last = [], None
    for _ in range(a.repeat):
        t0 = time.perf_counter()
        try:
            answer = client.post(a.url, {"state": state, "model": a.model, "questions": {"q": question}})
        except Undecided as exc:
            print("UNDECIDED service: %s" % exc)
            return 2
        timings.append((time.perf_counter() - t0) * 1000)
        last = answer
    got = wire.read_choice(last["answers"]["q"], [str(r["n"]) for r in rows])
    peak = got["peak"]
    decision, code = policy.decide_noul(peak, boundary, boundary)
    name = {str(r["n"]): r["name"] for r in rows}.get(got["choice"], "?")
    print("\npicked   #%s  %s" % (got["choice"], name))
    print("peak     %.3f   margin %.3f   %s" % (peak, got["margin"], decision))
    print("server   %.0f ms   (client wall %s)" % (
        last.get("latency_ms") or 0, [round(t) for t in timings]))
    if len(timings) > 1:
        print("median   %.0f ms" % statistics.median(timings))
    if decision != "YES":
        print("-> UNDECIDED: the caller keeps its old path (read the frame, or a bigger model)")
        return 2
    print("-> act on #%s" % got["choice"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
