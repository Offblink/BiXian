"""Probe: can a VLM answer the decision question in ONE forward pass with a usable probability?

The mechanism is kev/Jev with the base swapped for something that can see. The details
are not invented here -- they are lifted from PlayJev (github.com/OmniJev/PlayJev, Apache-2.0),
which does exactly this for game frames on a Qwen3.5-0.8B + vision tower:

  * readout = softmax restricted to the option tokens AT THE LAST POSITION
  * confidence = Jev's (p_max - 1/K) / (1 - 1/K)   -- removes the 1/K guessing floor
  * answer slots must be verified as single round-trip tokens, with no collisions
  * READ THE LOGITS IN FLOAT32. bf16 logits at magnitude 25-50 quantise to 0.125, which
    lets options tie. Keep a float32 copy of the (tied) output head and redo the matmul,
    instead of trusting `outputs.logits`.
  * diagnostics that matter: `allowed_mass` (share of the full-vocabulary softmax that
    lands on the answer slots) and `top_token` (is the model answering at all?)

    python vlm_probe.py --model <dir> --image som.png --question "点击发送按钮" --expect 4
"""
import argparse
import json
import pathlib
import sys
import time

import torch
from PIL import Image
from transformers import AutoProcessor, Qwen3VLForConditionalGeneration

SYSTEM_PROMPT = (
    "Apply the question to the state. Choose exactly one of the listed options. "
    "Respond with only its number, with no explanation or reasoning."
)


def slot_ids_for(tokenizer, labels, spaced):
    """Verify each label is ONE round-trip token, and that no two collide.

    PlayJev raises on the same check. A multi-token label cannot be read off a single
    position, and silently scoring its first token is how a probe reports nonsense.
    """
    ids, bad = [], []
    for text in (f" {x}" if spaced else x for x in labels):
        enc = tokenizer.encode(text, add_special_tokens=False)
        if len(enc) != 1 or tokenizer.decode(enc) != text:
            bad.append((text, enc, tokenizer.decode(enc) if enc else None))
        else:
            ids.append(enc[0])
    return ids, bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--question", required=True)
    ap.add_argument("--labels", default="1,2,3,4,5,6,7,8,9")
    ap.add_argument("--expect", default="")
    ap.add_argument("--spaced", action="store_true", help="answer slots as ' A' instead of 'A'")
    ap.add_argument("--longest-edge", type=int, default=0)
    a = ap.parse_args()

    labels = [x for x in a.labels.split(",") if x.strip()]
    t0 = time.perf_counter()
    processor = AutoProcessor.from_pretrained(a.model)
    processor.tokenizer.padding_side = "left"           # the last position is the answer slot
    model = Qwen3VLForConditionalGeneration.from_pretrained(
        a.model, dtype=torch.bfloat16, device_map={"": "cuda:0"}).eval()
    head_w32 = model.get_output_embeddings().weight.detach().float()   # PlayJev's float32 readout
    load_s = time.perf_counter() - t0
    torch.cuda.reset_peak_memory_stats()

    ids, bad = slot_ids_for(processor.tokenizer, labels, a.spaced)
    print(f"answer slots ({'spaced' if a.spaced else 'bare'}): {len(ids)}/{len(labels)} single-token; "
          f"collisions={len(ids) != len(set(ids))}")
    if bad:
        print(f"  NOT single round-trip tokens: {bad}")
        print("  -> try --spaced, or change the labels; do not score the first token of a multi-token label")
    if not ids:
        return 2

    image = Image.open(a.image).convert("RGB")
    prompt = (f"Question: {a.question}\n\n"
              f"Options:\n" + "\n".join(f"{i + 1}. candidate {n}" for i, n in enumerate(labels)) +
              f"\n\nAnswer with one number: {', '.join(labels)}.")
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": [{"type": "image", "image": image}, {"type": "text", "text": prompt}]}]
    kwargs = {"tokenize": True, "add_generation_prompt": True, "return_dict": True, "return_tensors": "pt"}
    if a.longest_edge:
        kwargs["processor_kwargs"] = {"images_kwargs": {"size": {"longest_edge": a.longest_edge}}}
    inputs = processor.apply_chat_template(messages, **kwargs)
    inputs = {k: (v.to("cuda:0") if hasattr(v, "to") else v) for k, v in inputs.items()}

    with torch.no_grad():
        t1 = time.perf_counter()
        out = model(**inputs, output_hidden_states=True)
        torch.cuda.synchronize()
        fwd_s = time.perf_counter() - t1

    raw = out.logits[0, -1, :]
    # float32 redo: the same readout PlayJev uses, because bf16 logits let options tie
    h = out.hidden_states[-1][:, -1, :]
    norm = getattr(getattr(model.model, "language_model", model.model), "norm", None)
    if norm is not None:
        h = norm(h)
    exact = (h[0].float() @ head_w32.T)

    K = len(ids)
    cand = torch.tensor(ids, device="cuda:0")
    p_exact = torch.softmax(exact[cand], dim=-1).cpu()
    p_raw = torch.softmax(raw[cand].float(), dim=-1).cpu()
    order = sorted(zip(labels, p_exact.tolist()), key=lambda kv: -kv[1])
    pmax = order[0][1]
    confidence = (pmax - 1.0 / K) / (1.0 - 1.0 / K)
    allowed_mass = float(torch.softmax(exact, dim=-1)[cand].sum())
    top_id = int(torch.argmax(exact))
    top_tok = processor.tokenizer.decode([top_id])

    print(f"\nload {load_s:.1f}s | forward {fwd_s*1000:.0f} ms | input tokens {inputs['input_ids'].shape[-1]}"
          f" | peak VRAM {torch.cuda.max_memory_allocated()/2**30:.2f} GiB")
    print(f"raw logits dtype {raw.dtype} (PlayJev warns these tie) | cand spread raw "
          f"{float(raw[cand].max()-raw[cand].min()):.4f} vs float32 {float(exact[cand].max()-exact[cand].min()):.4f}")
    print(f"\n== probability over candidate slots (float32 readout, softmax inside the K) ==")
    for lbl, p in order:
        mark = "  <-- expected" if a.expect and lbl == a.expect else ""
        print(f"   {lbl:>3}  {p:7.4f}  {'#' * int(p * 60)}{mark}")
    print(f"   winner: {order[0][0]}  p_max={pmax:.4f}  Jev confidence={confidence:.4f}  "
          f"(runner-up {order[1][1]:.4f}, margin {pmax - order[1][1]:.4f})")
    print(f"\ndiagnostics: allowed_mass={allowed_mass:.4f} (share of the full softmax on the K slots)  "
          f"top_token={top_tok!r}")
    print(f"same K-slots under the model's own (bf16) logits: "
          f"{[(l, round(p, 4)) for l, p in sorted(zip(labels, p_raw.tolist()), key=lambda kv: -kv[1])][:3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
