import importlib.metadata as m
import sys

import torch

print("python", sys.version.split()[0])
print("torch", torch.__version__, "cuda", torch.cuda.is_available())
import transformers

print("transformers", transformers.__version__)
for pkg in ("bitsandbytes", "torchvision", "accelerate"):
    try:
        print(pkg, m.version(pkg))
    except Exception as exc:  # noqa: BLE001
        print(pkg, "MISSING", exc)
