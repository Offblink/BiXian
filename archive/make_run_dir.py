"""Build the local run directory kev.serve actually loads.

Why this exists: `--run <dir>` accepts a plain directory, but the checkpoint's `head.pt`
records the base model as a Hub id ("Qwen/Qwen3.5-4B-Base") plus a revision, and
`kev.checkpoint` hands both straight to `transformers`. This box already has the base
weights on disk (fetched with Flower from ModelScope), so the run copy points `base` at
that directory and clears `base_revision`, which is what transformers wants for a local
path. Nothing upstream is modified: the patched copy lives in models/kev-4b-local/.

    ..\\vlm\\.venv\\Scripts\\python.exe make_run_dir.py   # 在 archive\\ 里，从项目根跑
"""
import json
import pathlib
import shutil
import sys

import torch

ROOT = pathlib.Path(__file__).resolve().parents[1]   # 在 archive\ 里，项目根在上层
SRC = ROOT / "models" / "kev-4b"
BASE = (ROOT / "models" / "base").resolve()
DST = ROOT / "models" / "kev-4b-local"


def main():
    if not (SRC / "head.pt").is_file():
        sys.exit(f"missing {SRC / 'head.pt'}")
    if not (BASE / "config.json").is_file():
        sys.exit(f"missing {BASE / 'config.json'}")

    if DST.exists():
        shutil.rmtree(DST)
    DST.mkdir(parents=True)
    for p in SRC.iterdir():
        if p.is_file() and p.name != "head.pt":
            shutil.copy2(p, DST / p.name)

    meta = torch.load(SRC / "head.pt", map_location="cpu", weights_only=False)
    print("upstream head.pt:", json.dumps({k: v for k, v in meta.items()
                                           if k in ("base", "base_revision", "lora", "head_dim",
                                                    "temperature", "option_isolation", "weights_dtype")},
                                          ensure_ascii=False))
    meta["base"] = str(BASE)
    meta["base_revision"] = None
    torch.save(meta, DST / "head.pt")
    print("wrote", DST / "head.pt")
    print("files:", sorted(p.name for p in DST.iterdir()))


if __name__ == "__main__":
    main()
