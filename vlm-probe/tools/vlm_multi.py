"""Several questions, one model load: does the readout survive the downscale, or just this one case?

The single-case probe hit #4 at p=1.0000 on the native frame -- encouraging, and also exactly the
kind of single data point that misleads. This runs a small battery per resolution and reports the
hit rate, so "it works" has to survive more than one question (and the decoys are deliberately
close: 发送 / 发送文件 / 撤回).

    python vlm_multi.py --model <dir> --image som.png --edges 768,1024,1568 \\
        --cases "点击发送按钮=4;把文件发给 Blinvo=3;查看家庭群的消息=7;撤回刚才那条消息=6;打开搜索框=2"
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
    ap.add_argument("--edges", default="768,1024,1568")
    ap.add_argument("--cases", required=True, help='"question=expected;#..." separated by ;')
    ap.add_argument("--labels", default="1,2,3,4,5,6,7,8,9")
    ap.add_argument("--load-in-4bit", action="store_true")
    a = ap.parse_args()

    labels = [x for x in a.labels.split(",") if x.strip()]
    cases = []
    for chunk in a.cases.split(";"):
        chunk = chunk.strip()
        if chunk:
            q, _, exp = chunk.rpartition("=")
            cases.append((q, exp))

    processor = AutoProcessor.from_pretrained(a.model)
    processor.tokenizer.padding_side = "left"
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
    ids = []
    for text in labels:
        enc = processor.tokenizer.encode(text, add_special_tokens=False)
        if len(enc) != 1:
            sys.exit(f"label {text!r} is not a single token")
        ids.append(enc[0])
    cand = torch.tensor(ids, device="cuda:0")
    norm = getattr(getattr(model.model, "language_model", model.model), "norm", None)
    native = Image.open(a.image).convert("RGB")
    total_peak = torch.cuda.max_memory_allocated() / 2**30
    print(f"loaded | static {total_peak:.2f} GiB | card {torch.cuda.get_device_properties(0).total_memory/2**30:.2f} GiB")

    for edge_s in a.edges.split(","):
        edge = int(edge_s)
        if edge and max(native.size) != edge:
            s = edge / max(native.size)
            img = native.resize((max(1, round(native.width * s)), max(1, round(native.height * s))), Image.BILINEAR)
        else:
            img = native
        hits, times = 0, []
        print(f"\n== edge={edge}  image={img.size}")
        for q, expect in cases:
            prompt = (f"Question: {q}\n\nOptions:\n"
                      + "\n".join(f"{i + 1}. candidate {n}" for i, n in enumerate(labels))
                      + f"\n\nAnswer with one number: {', '.join(labels)}.")
            messages = [{"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": [{"type": "image", "image": img},
                                                     {"type": "text", "text": prompt}]}]
            inputs = processor.apply_chat_template(messages, tokenize=True, add_generation_prompt=True,
                                                   return_dict=True, return_tensors="pt")
            inputs = {k: (v.to("cuda:0") if hasattr(v, "to") else v) for k, v in inputs.items()}
            torch.cuda.reset_peak_memory_stats()
            with torch.no_grad():
                t0 = time.perf_counter()
                out = model(**inputs, output_hidden_states=True)
                torch.cuda.synchronize()
                dt = (time.perf_counter() - t0) * 1000
            h = out.hidden_states[-1][:, -1, :]
            if norm is not None:
                h = norm(h)
            exact = (h[0].float().detach() @ head_w32.T)[cand]
            p = torch.softmax(exact, dim=-1).cpu()
            order = sorted(zip(labels, p.tolist()), key=lambda kv: -kv[1])
            times.append(dt)
            ok = order[0][0] == expect
            hits += ok
            del out
            torch.cuda.empty_cache()
            print(f"   {'HIT ' if ok else 'MISS'} #{order[0][0]:<3} p={order[0][1]:.4f}  {dt:6.0f} ms  "
                  f"{'期望 #' + expect if not ok else ''}  «{q}»")
        med = sorted(times)[len(times) // 2] if times else 0
        print(f"   -> {hits}/{len(cases)} 正确，前向中位 {med:.0f} ms，峰值 {torch.cuda.max_memory_allocated()/2**30:.2f} GiB")


if __name__ == "__main__":
    main()
