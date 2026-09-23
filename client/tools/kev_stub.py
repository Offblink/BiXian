#!/usr/bin/env python3
"""A rule-driven stand-in for the decision service -- for wiring and tests only.

It is NOT a model. Answers are picked from the instruction text so a test can
force a case deterministically:

    "high" -> confident yes / first option wins / top score bucket
    "low"  -> confident no
    "mid"  -> leans yes but not enough for a strict policy
    "flat" -> a UNIFORM distribution (the degenerate case the client must refuse)

It also answers GET /v1/models the way the real service does (the id, the run it
loaded and the fitted temperature all live in `models[0]`), and counts requests
at GET /__stats so tests can prove the cache did not hit the wire.

Run: python tools/kev_stub.py [port]     (port 0 picks a free port and prints it)
"""

import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATS = {"requests": 0, "model_gets": 0, "started": time.time()}

# What this stub claims to be. A test can point it at a different "run" to act out
# the case that mattered: the same URL serving a different backend.
IDENTITY = {"run": "kev-stub", "base": "kev-stub-base", "lora": 16, "temperature": 2.2}


def reset_stats():
    STATS["requests"] = 0
    STATS["model_gets"] = 0


def set_identity(run=None, base=None, lora=None, temperature=None):
    """Act out a swapped backend behind the same URL."""
    for name, value in (("run", run), ("base", base), ("lora", lora), ("temperature", temperature)):
        if value is not None:
            IDENTITY[name] = value


def _labels(criteria):
    if isinstance(criteria, dict):
        return list(criteria)
    return [c if isinstance(c, str) else (c or {}).get("label") for c in (criteria or [])]


def _spread(winner_index, n, top=0.90):
    rest = round((1.0 - top) / (n - 1), 4) if n > 1 else 0.0
    probs = {i: (top if i == winner_index else rest) for i in range(n)}
    total = sum(probs.values())
    return {str(k): round(v / total, 4) for k, v in probs.items()}


def answer_for(question):
    instructions = (question.get("instructions") or "").lower()
    kind = question.get("type")

    if kind == "noul":
        for keyword, value in (("sure", 0.995), ("high", 0.97), ("low", 0.03),
                               ("mid", 0.62), ("coin", 0.50), ("flat", 0.50)):
            if re.search(r"\b%s\b" % keyword, instructions):
                return {"type": "noul", "noul": value}
        return {"type": "noul", "noul": 0.50}

    if kind == "choice":
        labels = _labels(question.get("criteria"))
        n = len(labels)
        if not n:
            return {"type": "choice", "choice": None, "probabilities": {}, "confidence": 0.0}
        if re.search(r"\bflat\b", instructions):
            probs = {str(i): round(1.0 / n, 4) for i in range(n)}
            winner = 0
        else:
            winner = 0
            probs = _spread(winner, n, 0.90 if re.search(r"\bhigh\b", instructions) else 0.62)
        ranked = sorted(probs.values(), reverse=True)
        return {
            "type": "choice",
            "choice": labels[winner],
            "probabilities": {labels[int(k)]: v for k, v in probs.items()},
            "confidence": round(ranked[0] - (ranked[1] if n > 1 else 0.0), 4),
        }

    if kind == "score":
        levels = _labels(question.get("criteria"))
        n = len(levels)
        if not n:
            return {"type": "score", "score": 0.0, "legend": {}, "probabilities": {}, "confidence": 0.0}
        if re.search(r"\bflat\b", instructions):
            idx = 0
            probs = {str(i): round(1.0 / n, 4) for i in range(n)}
        elif re.search(r"\bhigh\b", instructions):
            idx = n - 1
            probs = _spread(idx, n, 0.90)
        else:
            idx = n // 2
            probs = _spread(idx, n, 0.62)
        ranked = sorted(probs.values(), reverse=True)
        return {
            "type": "score",
            "score": round(idx + 0.44, 2),
            "legend": {str(i): lvl for i, lvl in enumerate(levels)},
            "probabilities": probs,
            "confidence": round(ranked[0] - (ranked[1] if n > 1 else 0.0), 4),
        }

    raise ValueError("unknown question type %r" % (kind,))


class Handler(BaseHTTPRequestHandler):
    server_version = "kev-stub/0.0.1"

    def _send(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.rstrip("/")
        if path == "/v1/models":
            STATS["model_gets"] += 1
            # kev's own response shape, copied field-for-field from the real server
            # (docs/design-handoff.md section 11.1: a stub answering the shape the
            # client already expects can never catch a client that reads the real
            # one wrong -- and that is exactly what happened to the temperature).
            self._send({"models": [{
                "id": "kev-latest",
                "aliases": ["jev-latest"],
                "run": IDENTITY["run"],
                "base": IDENTITY["base"],
                "lora": IDENTITY["lora"],
                "device": "cpu",
                "temperature": IDENTITY["temperature"],
                "prefix_cache": {"size": 4, "min_state_tokens": 384, "hits": 0, "misses": 0, "cached_states": 0},
            }]})
        elif path == "/__stats":
            self._send({"requests": STATS["requests"], "uptime_s": round(time.time() - STATS["started"], 1)})
        else:
            self._send({"error": {"message": "not found"}}, 404)

    def do_POST(self):
        started = time.perf_counter()
        if self.path.rstrip("/") != "/v1/systemone":
            self._send({"error": {"message": "not found"}}, 404)
            return
        STATS["requests"] += 1
        length = int(self.headers.get("content-length") or 0)
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send({"error": {"message": "bad json"}}, 400)
            return
        answers = {}
        for qid, question in (request.get("questions") or {}).items():
            try:
                answers[qid] = answer_for(question)
            except ValueError as exc:
                self._send({"error": {"message": str(exc)}}, 400)
                return
        self._send({
            "model": request.get("model") or "kev-latest",
            "answers": answers,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "latency_ms": round((time.perf_counter() - started) * 1000, 2),
        })

    def log_message(self, *args):
        pass


def serve(port=8009, host="127.0.0.1"):
    httpd = ThreadingHTTPServer((host, port), Handler)
    return httpd


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8009
    httpd = serve(port)
    print("kev-stub listening on http://127.0.0.1:%d/v1/systemone" % httpd.server_address[1], flush=True)
    if port == 0:
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd
    httpd.serve_forever()


if __name__ == "__main__":
    main()
