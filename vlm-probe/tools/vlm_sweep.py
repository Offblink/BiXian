"""One model load, several input resolutions: where does Qwen3-VL-4B stop spilling?

Measured once already: the native 2240x1400 frame becomes 3080 visual tokens and peaks at
14.55 GiB on a 12.23 GiB card, so the driver spills to host memory and one forward takes 133 s.
Visual token count is set by the input resolution (patch 16, spatial merge 2 => one token per
32x32 pixels), so the question is which resolution fits, how fast it is, and whether the answer
survives the downscale -- the "vision sweet spot" of 1568 from the earlier grounding work is a
prior, not a guarantee, and small controls are exactly what downscaling destroys.

    python vlm_sweep.py --model <dir> --image som.png --question "点击发送按钮" --expect 4

Per edge: input tokens, visual tokens, forward ms, peak VRAM, winner, p_max, and the sha of the
readout so two resolutions can be compared without pretending the numbers are identical.
"""
import argparse
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--image", required=True)
    ap.add_argument("--question", required=True)
    ap.add_argument("--labels", default="1,2,3,4,5,6,7,8,9")
    ap.add_argument("--expect", default="")
    ap.add_argument("--load-in-4bit", action="store_true", help="quantize the weights (activations stay bf16)")
    ap.add_argument("--edges", default="768,1024,1568", help="0 = native, no resize")
    a = ap.parse_args()

    labels = [x for x in a.labels.split(",") if x.strip()]
    processor = AutoProcessor.from_pretrained(a.model)
    processor.tokenizer.padding_side = "left"
    t0 = time.perf_counter()
    if a.load_in_4bit:
        from transformers import BitsAndBytesConfig
        qcfg = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_compute_dtype=torch.bfloat16,
                                  bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True)
        model = Qwen3VLForConditionalGeneration.from_pretrained(
            a.model, quantization_config=qcfg, dtype=torch.bfloat16, device_map={"": "cuda:0"}).eval()
    else:
        model = Qwen3VLForConditionalGeneration.from_pretrained(
            a.model, dtype=torch.bfloat16, device_map={"": "cuda:0"}).eval()
    head_w32 = model.get_output_embeddings().weight.detach().float()
    load_s = time.perf_counter() - t0
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    print(f"load {load_s:.1f}s | card limit {torch.cuda.get_device_properties(0).total_memory/2**30:.2f} GiB")

    ids = []
    for text in labels:
        enc = processor.tokenizer.encode(text, add_special_tokens=False)
        if len(enc) != 1:
            sys.exit(f"label {text!r} is not a single token: {enc}")
        ids.append(enc[0])
    native = Image.open(a.image).convert("RGB")

    def scaled(long_edge):
        """Resize ourselves, the way athand's _to_image does: the processor's own `size`
        dict took the picture down to a 2x2 patch grid last run (vis_tok=1), so the pixels
        going in are our decision, not an argument whose units we guessed wrong."""
        if not long_edge or max(native.size) == long_edge:
            return native
        s = long_edge / max(native.size)
        return native.resize((max(1, round(native.width * s)), max(1, round(native.height * s))), Image.BILINEAR)
    prompt = (f"Question: {a.question}\n\nOptions:\n"
              + "\n".join(f"{i + 1}. candidate {n}" for i, n in enumerate(labels))
              + f"\n\nAnswer with one number: {', '.join(labels)}.")


    print(f"\n{'edge':>6} {'img':>10} {'in_tok':>7} {'vis_tok':>8} {'fwd_ms':>9} {'peak_GiB':>9}  winner  p_max   result")
    for edge_s in a.edges.split(","):
        edge = int(edge_s)
        image = scaled(edge)
        messages = [{"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": [{"type": "image", "image": image},
                                                 {"type": "text", "text": prompt}]}]
        inputs = processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                               return_dict=True, return_tensors="pt")
        inputs = {k: (v.to("cuda:0") if hasattr(v, "to") else v) for k, v in inputs.items()}
        grid = inputs.get("image_grid_thw")
        vis = int(grid.prod().item()) // 4 if grid is not None else 0
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        with torch.no_grad():
            try:
                t1 = time.perf_counter()
                out = model(**inputs, output_hidden_states=True)
                torch.cuda.synchronize()
                dt = (time.perf_counter() - t1) * 1000
            except torch.cuda.OutOfMemoryError as exc:
                print(f"{edge:>6} {str(image.size):>10} {int(inputs['input_ids'].shape[-1]):>7} {vis:>8}  OOM: {str(exc)[:60]}")
                torch.cuda.empty_cache()
                continue
        h = out.hidden_states[-1][:, -1, :]
        norm = getattr(getattr(model.model, "language_model", model.model), "norm", None)
        if norm is not None:
            h = norm(h)
        exact = h[0].float().detach() @ head_w32.T
        cand = torch.tensor(ids, device="cuda:0")
        p = torch.softmax(exact[cand], dim=-1).cpu()
        order = sorted(zip(labels, p.tolist()), key=lambda kv: -kv[1])
        peak = torch.cuda.max_memory_allocated() / 2**30
        mark = ""
        if a.expect:
            mark = "HIT" if order[0][0] == a.expect else f"MISS(want {a.expect})"
        flag = "  <-- SPILL" if peak > 11.6 else ""
        print(f"{edge:>6} {str(image.size):>10} {int(inputs['input_ids'].shape[-1]):>7} {vis:>8} {dt:>9.0f} {peak:>9.2f}  "
              f"#{order[0][0]:<6} {order[0][1]:.4f}  {mark}{flag}")
        del out
        torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
