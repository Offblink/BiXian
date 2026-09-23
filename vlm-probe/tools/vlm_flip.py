"""Does the VLM's raw readout flip when the candidate list is rotated?

AnyJev's headline finding is not about accuracy: raw next-token readout flips its answer when the
option order changes in 0.227 of cases, and does it *at 1.00 confidence*, so you cannot threshold on
it. This measures the same thing on a VLM, and then applies their L0 fix (cyclic-shift
marginalization: every candidate occupies every position once, combined in log space) to see whether
it goes away.

The candidate NUMBER is the candidate's identity here (it is what gets handed to athand), so what
rotates is where each number sits in the rendered option list -- position, not identity.

    python vlm_flip.py --model <dir> --image som.png --edge 1024 \\
        --cases "点击发送按钮=4;撤回刚才那条消息=6"
"""
import argparse
import math
import sys
import time

import torch
from PIL import Image
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

SYSTEM_PROMPT = (
    "Apply the question to the state. Choose exactly one of the listed options. "
    "Respond with only its number, with no explanation or reasoning."
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--edge", type=int, default=1024)
    ap.add_argument("--labels", default="1,2,3,4,5,6,7,8,9")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--combine", default="logmean", choices=("logmean", "mean"))
    a = ap.parse_args()

    labels = [x for x in a.labels.split(",") if x.strip()]
    cases = []
    for chunk in a.cases.split(";"):
        chunk = chunk.strip()
        if chunk:
            q, _, exp = chunk.rpartition("=")
            cases.append((q, exp))

    processor = AutoProcessor.from_pretrained(a.model)
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        a.model, dtype=torch.bfloat16, device_map={"": "cuda:0"}).eval()
    head_w32 = model.get_output_embeddings().weight.detach().float()
    norm = getattr(getattr(model.model, "language_model", model.model), "norm", None)
    ids = {lab: processor.tokenizer.encode(lab, add_special_tokens=False)[0] for lab in labels}
    native = Image.open(a.image).convert("RGB")
    if max(native.size) != a.edge:
        s = a.edge / max(native.size)
        img = native.resize((max(1, round(native.width * s)), max(1, round(native.height * s))), Image.BILINEAR)
    else:
        img = native
    print(f"model loaded | image {img.size} | labels {labels}")

    def one(question, order):
        prompt = (f"Question: {question}\n\nOptions:\n"
                  + "\n".join(f"candidate {lab}" for lab in order)
                  + f"\n\nAnswer with one number: {', '.join(order)}.")
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": [{"type": "image", "image": img},
                                                 {"type": "text", "text": prompt}]}]
        inputs = processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                               return_dict=True, return_tensors="pt")
        inputs = {k: (v.to("cuda:0") if hasattr(v, "to") else v) for k, v in inputs.items()}
        with torch.no_grad():
            out = model(**inputs, output_hidden_states=True)
        h = out.hidden_states[-1][:, -1, :]
        if norm is not None:
            h = norm(h)
        logits = h[0].float().detach() @ head_w32.T
        cand = torch.tensor([ids[lab] for lab in order], device="cuda:0")
        p = torch.softmax(logits[cand], dim=-1).cpu().tolist()
        return dict(zip(order, p))

    for question, expect in cases:
        K = len(labels)
        runs, logacc = [], {lab: 0.0 for lab in labels}
        t0 = time.perf_counter()
        for s in range(K):
            order = labels[s:] + labels[:s]                 # rotate where each number sits
            p = one(question, order)
            runs.append(p)
            for lab, v in p.items():
                logacc[lab] += math.log(max(v, 1e-12)) / K
        dt = (time.perf_counter() - t0) * 1000 / K
        winners = [max(p.items(), key=lambda kv: kv[1])[0] for p in runs]
        raw_flip = sum(w != winners[0] for w in winners[1:]) / (K - 1)
        raw_hit = winners[0] == expect
        if a.combine == "logmean":
            top = max(logacc.values())
            e = {k: math.exp(v - top) for k, v in logacc.items()}
            z = sum(e.values()); l0 = {k: v / z for k, v in e.items()}
        else:
            l0 = {lab: sum(r[lab] for r in runs) / K for lab in labels}
        l0_winner = max(l0.items(), key=lambda kv: kv[1])[0]
        print(f"\n«{question}»  期望 #{expect}   ({dt:.0f} ms/次 × {K})")
        for s, p in enumerate(runs):
            mark = " ".join(f"{k}:{v:.3f}" for k, v in sorted(p.items(), key=lambda kv: -kv[1])[:3])
            print(f"   轮转{s}: #{winners[s]}  (top3 {mark})")
        print(f"   raw  : 答案 #{winners[0]}  flip_rate={raw_flip:.3f}  {'HIT' if raw_hit else 'MISS'}")
        print(f"   L0   : 答案 #{l0_winner}  {'HIT' if l0_winner == expect else 'MISS'}   "
              f"(p={l0[l0_winner]:.4f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
