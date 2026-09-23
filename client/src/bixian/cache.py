"""Deterministic cache: same input, same answer, no network.

The key covers (url, payload, model) but deliberately NOT the threshold, because
thresholds are compared locally -- changing a threshold must not invalidate
answers. The flip side is that the threshold must be recorded in the audit row,
otherwise an old decision cannot be explained (see audit.py).
"""

import hashlib
import json
import os
import pathlib


def cache_path():
    env = os.environ.get("BIXIAN_CACHE")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".bixian" / "cache.jsonl"


def make_key(url, payload, model=None, backend=None):
    """The key covers (url, payload, model, backend identity) but deliberately NOT
    the threshold, because thresholds are compared locally -- changing a threshold
    must not invalidate answers. The flip side is that the threshold must be
    recorded in the audit row, otherwise an old decision cannot be explained
    (see audit.py).

    `backend` is the identity the service reports for itself (kev: the run it
    loaded). It belongs in the key because the URL is not the service: pointing
    the same 127.0.0.1:8009 at a stub, then at the real model, then at a
    retrained checkpoint is a normal thing to do, and every one of those would
    otherwise be served the first one's answers. That failure is silent -- exit
    code 0, no warning -- which is precisely the class of bug the rest of this
    design is built to avoid (docs/design-handoff.md section 11.7). Callers that
    cannot learn the identity pass None and get the old, weaker key.
    """
    blob = json.dumps([url, payload, model or "", backend or ""], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def get(key, path=None):
    try:
        path = pathlib.Path(path) if path else cache_path()
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("k") == key:
                    return row
    except OSError:
        return None
    return None


def put(key, answer, meta, path=None):
    try:
        path = pathlib.Path(path) if path else cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"k": key, "a": answer, "meta": meta}, ensure_ascii=False) + "\n")
    except OSError:
        pass
