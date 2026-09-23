"""Pick ONE number out of an athand listing with a local VLM -- the missing middle step.

athand already owns both ends of the loop (`targets` numbers the candidates, `verify` checks
frames and values, `ESCALATED` stops after three dead attempts) and deliberately leaves the
middle to the caller. This file is that middle, and nothing else: it clicks nothing, it
touches no window, it prints a number or UNDECIDED.

Where the text path (`pick_l0.py`, kev at :8009) needs the candidates to have names, this one
looks at the picture, so unnamed shapes (canvas buttons, icon-only toolbars) become answerable.
The trade is memory and time: one forward at the default 1536 long edge measures 1068 ms / 5.8 GiB
in 4-bit on this box, against 1950 ms / 11.4 GiB in bf16 for the same 34/34 on the 4-desktop bench
(docs/VLM_DECISION_PROBE.md §七).

Three choices that are deliberate:

  * **We number the candidates ourselves.** The readout is a restricted softmax over the option
    tokens at the last position, so every option has to be a single token -- and athand's own
    numbering runs 1..N with N in the dozens on a real desktop (`27. Blinvo` is two tokens).
    Drawing our own 1..K over the frame keeps the slot single-token no matter what athand
    numbered things, and K stays small enough for a 4B to choose among at all. The mapping back
    to athand's numbers is printed with every answer.
  * **No pre-filter on names.** Dropping unnamed candidates is right for the text path and wrong
    here: the box is visible. Only off-window candidates and a source whitelist are dropped.
  * **Fail-open.** A peak below the policy threshold prints UNDECIDED (exit 2) and the caller
    keeps its old path -- no argmax of a flat distribution is ever acted on.

One-shot:

    python vlm_pick.py --listing cases/fixture-som.json --intent "点击发送按钮" --expect 12

Holding the weights for a loop (the model load costs more than the forward):

    echo '{"listing":"cases\\fixture-som.json","intent":"点击发送按钮"}' | python vlm_pick.py --loop
"""
import argparse
import contextlib
import json
import pathlib
import sys
import time

import torch
from PIL import Image, ImageDraw, ImageFont
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

DECIDE_SRC = pathlib.Path(__file__).resolve().parents[1] / "client" / "src"
sys.path.insert(0, str(DECIDE_SRC))

from bixian import policy  # noqa: E402

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_MODEL = HERE.parent / "models" / "qwen3vl-4b"
FONT = r"C:\Windows\Fonts\msyh.ttc"          # the UI text is Chinese; msyh survives the downscale
MAX_K = 9                                    # single-token slots: "1".."9"
SYSTEM_PROMPT = (
    "Apply the question to the state. Choose exactly one of the listed options. "
    "Respond with only its number, with no explanation or reasoning."
)


MIN_SIDE_PX = 8      # a 2x2 speck has nothing to click on
MAX_AREA_SHARE = 0.5  # the client area itself is not a control


def plausible(box, win, source: str) -> bool:
    """True when a candidate is a control worth drawing a number on.

    athand's own listing carries the window and its render host as `a11y` candidates (a real one
    had `cls=Qt51514QWindowIcon`, 2x2 px, and a render subwindow covering the whole client area).
    Boxing those draws a number over the entire UI -- measured on a real frame of WeChat: the
    model then spent its whole probability on the two junk boxes (0.58 / 0.42) and left the actual
    target row at 1e-6. Nothing there was about the model; the option set was poisoned.

    Large is only disqualifying for `a11y`: a big *shape* cut out of the picture is a legitimate
    target (a document area, a canvas), while the window's own frame never is.
    """
    if len(box) != 4:
        return False
    width, height = box[2] - box[0], box[3] - box[1]
    if width < MIN_SIDE_PX or height < MIN_SIDE_PX:
        return False
    if source == "a11y" and len(win) == 4 and win[2] > win[0] and win[3] > win[1]:
        share = (width * height) / ((win[2] - win[0]) * (win[3] - win[1]))
        if share >= MAX_AREA_SHARE:
            return False
    return True


def load_listing(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def shortlist(data, intent, sources=None, k=8):
    """Candidates worth asking about, in athand's own order.

    Order is kept (the model should see the screen the way a person does) -- literal overlap is
    only used to decide WHO gets asked when there are more than `k` of them. Candidates outside
    the window cannot be acted on, so they never enter.
    """
    win = tuple(data.get("rect") or ())
    rows = []
    for rec in data.get("targets") or []:
        n = rec.get("n")
        if not isinstance(n, int) or n < 1:
            continue
        src = rec.get("source") or "a11y"
        if sources and src not in sources:
            continue
        box = tuple(rec.get("rect") or ())
        if win and len(box) == 4 and (box[2] <= win[0] or box[0] >= win[2]
                                      or box[3] <= win[1] or box[1] >= win[3]):
            continue
        if not plausible(box, win, src):
            continue
        rows.append({"n": n, "name": (rec.get("name") or "").strip(),
                     "cls": rec.get("cls") or "", "source": src, "rect": box})
    if len(rows) > k:
        trimmed = sorted(rows, key=lambda r: (-overlap(intent, r["name"]), r["n"]))[:k]
        rows = sorted(trimmed, key=lambda r: r["n"])
    return rows


def overlap(intent, name):
    if len(name) < 2:
        return 0
    a = {intent[i:i + 2] for i in range(len(intent) - 1)}
    b = {name[i:i + 2] for i in range(len(name) - 1)}
    return len(a & b)


def annotate(frame, rows, origin=(0, 0)):
    """Red box + number at its top-left, athand's own tool face -- but numbered 1..K by us.

    The number's size follows the box height (clamped): a fixed 40 px patch swamps a 26 px toolbar
    button, covers its label, and on a real desktop would smear over its neighbours.

    `origin` is the window's own top-left. athand's rectangles are in **screen** coordinates while
    the picture is the window, so drawing them raw throws every box off by the window's position --
    measured on a real WeChat window at (718,402): all nine numbers landed in the chat pane instead
    of on the rows they named, and the model then answered from a picture that did not match the
    option list at all.
    """
    img = Image.open(frame).convert("RGB")
    d = ImageDraw.Draw(img)
    for i, r in enumerate(rows, start=1):
        x0, y0, x1, y1 = r["rect"]
        x0, y0, x1, y1 = x0 - origin[0], y0 - origin[1], x1 - origin[0], y1 - origin[1]
        font = _font(int(min(44, max(13, (y1 - y0) - 4))))
        d.rectangle((x0, y0, x1, y1), outline=(220, 40, 40), width=3)
        box = d.textbbox((0, 0), str(i), font=font)
        pw, ph = box[2] - box[0] + 8, box[3] - box[1] + 6
        d.rectangle((x0 - 2, y0 - 2, x0 + pw, y0 + ph), fill=(255, 255, 255),
                    outline=(220, 40, 40), width=2)
        d.text((x0 + 2, y0 - 1), str(i), font=font, fill=(200, 20, 20))
    return img


def _font(size):
    try:
        return ImageFont.truetype(FONT, size)
    except OSError:
        return ImageFont.load_default()


def scaled(img, long_edge):
    if not long_edge or max(img.size) <= long_edge:
        return img
    s = long_edge / max(img.size)
    return img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.BILINEAR)


class Reader:
    """One model, one forward per question, float32 readout over the option slots."""

    def __init__(self, model_dir, four_bit=True, edge=1024):
        self.edge = edge
        kwargs = {"dtype": torch.bfloat16, "device_map": {"": "cuda:0"}}
        if four_bit:
            from transformers import BitsAndBytesConfig
            kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16,
                bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True)
        t0 = time.perf_counter()
        self.processor = AutoProcessor.from_pretrained(str(model_dir))
        self.processor.tokenizer.padding_side = "left"
        self.model = Qwen3VLForConditionalGeneration.from_pretrained(str(model_dir), **kwargs).eval()
        self.load_s = time.perf_counter() - t0
        # bf16 logits quantise at magnitude 25-50 and let options tie: keep an fp32 copy of the
        # (tied) head and redo the matmul. In 4-bit this copy is already dequantised, so the
        # quantisation error is in it -- which is exactly why the accuracy battery was re-run.
        self.head_w32 = self.model.get_output_embeddings().weight.detach().float()
        self.slots, bad = [], []
        for i in range(1, MAX_K + 1):
            ids = self.processor.tokenizer.encode(str(i), add_special_tokens=False)
            (self.slots if len(ids) == 1 else bad).append(ids[0] if len(ids) == 1 else i)
        if bad:
            raise RuntimeError(f"number slots are not single tokens: {bad}")
        self.slots = torch.tensor(self.slots, device="cuda:0")
        self.norm = getattr(getattr(self.model.model, "language_model", self.model.model), "norm", None)

    def ask(self, img, intent, k):
        """One forward over one question.

        `img` may be None: the same restricted readout is asked of the labels alone, which is
        what a caller with an enumeration but no picture needs (`decider.py`).
        """
        labels = [str(i) for i in range(1, k + 1)]
        prompt = (f"Question: {intent}\n\nOptions:\n"
                  + "\n".join(f"{i}. candidate {lab}" for i, lab in enumerate(labels, start=1))
                  + f"\n\nAnswer with one number: {', '.join(labels)}.")
        content = ([{"type": "image", "image": scaled(img, self.edge)}] if img is not None else [])
        content.append({"type": "text", "text": prompt})
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": content}]
        inputs = self.processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                                    return_dict=True, return_tensors="pt")
        inputs = {k2: (v.to("cuda:0") if hasattr(v, "to") else v) for k2, v in inputs.items()}
        grid = inputs.get("image_grid_thw")
        vis = int(grid.prod().item()) // 4 if grid is not None else 0
        torch.cuda.empty_cache()
        with torch.no_grad():
            t0 = time.perf_counter()
            out = self.model(**inputs, output_hidden_states=True)
            torch.cuda.synchronize()
            ms = (time.perf_counter() - t0) * 1000
        h = out.hidden_states[-1][:, -1, :]
        if self.norm is not None:
            h = self.norm(h)
        exact = h[0].float().detach() @ self.head_w32.T
        cand = self.slots[:k]
        full = torch.softmax(exact, dim=-1)
        p = torch.softmax(exact[cand], dim=-1).cpu()
        top = int(full.argmax())
        probs = {i + 1: float(v) for i, v in enumerate(p.tolist())}
        diag = {
            "ms": ms,
            "vis_tok": vis,
            "in_tok": int(inputs["input_ids"].shape[-1]),
            "peak_gib": torch.cuda.max_memory_allocated() / 2**30,
            "allowed_mass": float(full[cand].sum()),
            "top_token": self.processor.tokenizer.decode([top]),
        }
        return probs, diag


def decide(probs, k, policy_name, policy_file=None):
    entry = policy.resolve(policy.load(policy_file), policy_name)
    order = sorted(probs.items(), key=lambda kv: -kv[1])
    top_label, peak = order[0]
    confidence = (peak - 1.0 / k) / (1.0 - 1.0 / k) if k > 1 else peak
    decision, _ = policy.decide_noul(peak, entry["threshold"], entry["threshold"])
    return order, top_label, peak, confidence, decision, entry


def render(rows, order, intent, diag, top_label, peak, confidence, decision, entry, ms_total):
    lines = []
    lines.append("asked %d candidates, forward %.0f ms (total %.0f ms), %d visual tokens, peak %.2f GiB"
                 % (len(rows), diag["ms"], ms_total, diag["vis_tok"], diag["peak_gib"]))
    for label, p in order:
        r = rows[label - 1]
        lines.append("   our #%d -> athand #%-4s %-20s p=%6.4f %s"
                     % (label, r["n"], (r["name"] or "(no name)")[:20], p, "#" * int(p * 50)))
    chosen = rows[top_label - 1]
    lines.append("intent %r" % intent)
    lines.append("picked athand #%s %s  p=%.4f  confidence=%.3f  threshold=%.2f (%s) -> %s"
                 % (chosen["n"], chosen["name"] or "(no name)", peak, confidence,
                    entry["threshold"], entry["policy"], decision))
    return "\n".join(lines), chosen


def run_once(reader, req, args):
    """One request: listing + intent (+ optional frame) -> (result dict, human text)."""
    data = load_listing(req["listing"])
    rows = shortlist(data, req["intent"],
                     {s.strip() for s in (args.source or "").split(",") if s.strip()} or None, args.k)
    if not rows:
        return {"decision": "UNDECIDED", "reason": "no candidates in the listing"}, "UNDECIDED no candidates"
    if len(rows) > MAX_K:
        return ({"decision": "UNDECIDED", "reason": "more than %d candidates" % MAX_K},
                "UNDECIDED %d candidates is more than the %d single-token slots" % (len(rows), MAX_K))
    frame = req.get("frame") or data.get("frame")
    if frame and not pathlib.Path(frame).is_file():
        # A listing names its frame as a sibling (`"frame": "bare.png"`): resolve it next to the
        # listing, so `--listing cases\x.json` finds `cases/bare.png` whatever the cwd is.
        beside = pathlib.Path(req["listing"]).parent / frame
        if beside.is_file():
            frame = beside
    if not frame or not pathlib.Path(frame).is_file():
        return ({"decision": "UNDECIDED", "reason": "no frame image"},
                "UNDECIDED no frame image (pass --frame; the listing points at %r)" % frame)
    img = annotate(frame, rows, origin=tuple(data.get("rect") or (0, 0))[:2])
    # The picture the model saw is saved every run: when the answer is UNDECIDED the caller
    # escalates to a bigger model, and it must look at the SAME numbering or the answer cannot
    # be mapped back onto athand's numbers. Deterministic path: <listing>.som.png.
    som_path = str(pathlib.Path(req["listing"]).with_suffix(".som.png"))
    with contextlib.suppress(OSError):
        img.save(som_path)
    if args.dump_som:
        img.save(args.dump_som)
    t0 = time.perf_counter()
    probs, diag = reader.ask(img, req["intent"], len(rows))
    order, top_label, peak, confidence, decision, entry = decide(probs, len(rows), args.policy, args.policy_file)
    ms_total = (time.perf_counter() - t0) * 1000
    text, chosen = render(rows, order, req["intent"], diag, top_label, peak, confidence, decision, entry, ms_total)
    result = {
        "decision": "YES" if decision == "YES" else "UNDECIDED",
        "n": chosen["n"], "name": chosen["name"], "p": peak, "confidence": confidence,
        "threshold": entry["threshold"], "policy": entry["policy"],
        "candidates": [{"our": lab, "n": rows[lab - 1]["n"], "name": rows[lab - 1]["name"], "p": p}
                       for lab, p in order],
        # Everything a second-tier model needs to answer the same question itself: the picture
        # (numbered exactly as we numbered the options) and the option list to map its answer back.
        "som": som_path,
        "options": [{"our": i, "n": r["n"], "name": r["name"]} for i, r in enumerate(rows, 1)],
        "diag": diag,
    }
    if decision != "YES":
        text += "\n-> UNDECIDED: caller keeps its own path (re-list, read the frame, or a bigger model)"
    else:
        text += "\n-> athand: click --target %s" % chosen["n"]
    if args.expect:
        text += "\nexpected #%s -> %s" % (args.expect, "HIT" if str(chosen["n"]) == args.expect else "MISS")
    return result, text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--listing", help="athand listing json")
    ap.add_argument("--intent", help="what the task is, in the candidates' language")
    ap.add_argument("--frame", help="the screenshot to annotate (default: listing's own frame)")
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--edge", type=int, default=1536,
                    help="long edge; 0 = native. 1536 is the measured knee: 25/25 on the "
                         "3-desktop bench, while 1280 already loses the 14 px tier (probe doc §七)")
    ap.add_argument("--no-4bit", dest="four_bit", action="store_false",
                    help="load bf16 instead: 10.4-11.4 GiB peak, 2x slower at 1536")
    ap.add_argument("--k", type=int, default=8, help="at most this many candidates get asked about")
    ap.add_argument("--source", default="", help="comma list of athand sources to keep (a11y,ocr,shape)")
    ap.add_argument("--policy", default="default", help="policy name or action class (see policies/default.json)")
    ap.add_argument("--policy-file", default=None)
    ap.add_argument("--expect", default="")
    ap.add_argument("--dump-som", default="", help="write the annotated image we actually fed the model")
    ap.add_argument("--loop", action="store_true",
                    help="read one request per stdin line (json: listing/intent/frame), keep the weights loaded")
    a = ap.parse_args()
    if a.k > MAX_K:
        ap.error(f"--k is at most {MAX_K}: the option slots are the tokens for 1..{MAX_K}")

    reader = Reader(a.model, four_bit=a.four_bit, edge=a.edge)
    print("loaded in %.1fs | edge %d | %s | card %.2f GiB"
          % (reader.load_s, a.edge, "4-bit" if a.four_bit else "bf16",
             torch.cuda.get_device_properties(0).total_memory / 2**30), file=sys.stderr)

    if a.loop:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                result, text = run_once(reader, json.loads(line), a)
            except Exception as exc:  # noqa: BLE001
                result, text = {"decision": "UNDECIDED", "reason": str(exc)}, "UNDECIDED %s" % exc
            print(text, file=sys.stderr)
            print(json.dumps(result, ensure_ascii=False), flush=True)
        return 0

    if not a.listing or not a.intent:
        ap.error("--listing and --intent are required unless --loop is given")
    result, text = run_once(reader, {"listing": a.listing, "intent": a.intent, "frame": a.frame}, a)
    print(text)
    return 0 if result["decision"] == "YES" else 2


if __name__ == "__main__":
    sys.exit(main())
