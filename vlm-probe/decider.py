"""One decision, asked of a local VLM: an enumeration (and usually a picture) in, one option
out with its confidence. Nothing else.

This is the *provider* half of the seam, and it is deliberately ignorant of who calls it. It
never sees a window, a tool, a listing format or a file layout of the caller's: the request is a
question plus a numbered list of options, and the answer is one of those options' own `id` with
a probability. Any caller that can build that list -- a desktop driver, a form filler, a test
harness -- uses the same three commands.

Contract v1 (one JSON object in, one JSON object out):

    request   {"intent":  "...",                        # what the caller is trying to do
               "options": [{"id": <any>, "label": "...", "box": [x0, y0, x1, y1]}],
               "image":   "<png path>"}                 # optional; with it, every option needs a box
    response  {"contract": 1, "decision": "YES" | "UNDECIDED",
               "id": <the chosen option's id>, "index": 1..K,
               "p": 0.97, "confidence": 0.95, "threshold": 0.5, "policy": "default",
               "options": [{"index": 1, "id": ..., "label": "...", "p": 0.97}, ...],
               "marked": "<png path>",                  # what the model actually looked at
               "ms": 1180.4, "diag": {...}}

Rules that come from measurements (docs/VLM_DECISION_PROBE.md), not from taste:

  * **At most 9 options.** The readout is a restricted softmax over the option tokens at the last
    position, so every option has to be ONE token: the slots are the tokens for "1".."9". A
    longer list is the caller's problem to shorten -- this service refuses rather than trimming,
    because which candidates are worth asking about is a judgment about the caller's world.
  * **We number, the caller identifies.** The boxes are drawn over with our own 1..K (the model
    reads a number off the picture, it never sees the caller's labels), and every answer carries
    the caller's `id` back, so neither side has to know the other's numbering.
  * **UNDECIDED is a hand-off, not an answer.** Below the policy threshold nothing is reported as
    a choice (the caller looks at `marked` itself or asks a bigger model). "Not sure it is safe"
    and "it is not safe" stay different sentences -- see bixian/policy.py.
  * **The picture is optional.** With no `image` the same question is asked of the labels alone
    (a text-only forward; plumbing verified, accuracy not measured -- the measured 34/34 numbers
    are all with a picture).

Three commands, one contract:

    python decider.py check                      # are the weights on this disk? (no torch: ms)
    python decider.py ask --request req.json     # one request per process (pays the load each time)
    python decider.py serve --port 8111          # resident: weights loaded once, HTTP on loopback

`serve` is the shape a caller wants when it asks more than once: the load measures 29 s and the
forward 1.0-1.3 s, so a per-call process would spend 98% of its life loading weights.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import pathlib
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
DEFAULT_MODEL = HERE.parent / "models" / "qwen3vl-4b"
CONTRACT = 1
MAX_OPTIONS = 9  # the single-token slots, see the docstring; vlm_pick.MAX_K agrees


def model_present(model_dir) -> bool:
    """Whether the weights are on this disk -- the switch a caller's auto-detect reads.

    A directory with a `config.json` and at least one shard: an empty folder named like a
    model is exactly the case this has to answer "no" to (the text backend's weights were
    deleted on 2026-09-23 and `archive/kev_text.py start` now fails by design).
    """
    path = pathlib.Path(model_dir)
    if not (path / "config.json").is_file():
        return False
    return any(path.glob("*.safetensors")) or any(path.glob("*.bin"))


def bad_request(req) -> str | None:
    """Why this request cannot be answered at all (None = it can)."""
    if not isinstance(req, dict):
        return "the request must be a JSON object"
    if not str(req.get("intent") or "").strip():
        return "intent is required (the question, in the options' own language)"
    options = req.get("options")
    if not isinstance(options, list) or not options:
        return "options must be a non-empty list"
    if len(options) > MAX_OPTIONS:
        return (f"{len(options)} options is more than the {MAX_OPTIONS} single-token slots — "
                "the caller shortens the list, this end does not guess which ones matter")
    image = req.get("image")
    if image and not pathlib.Path(str(image)).is_file():
        return f"no such image: {image}"
    for i, opt in enumerate(options, 1):
        if not isinstance(opt, dict):
            return f"option {i} is not an object"
        if "id" not in opt:
            return f"option {i} has no id (the id is what comes back; it is the caller's to choose)"
        if image:
            box = opt.get("box")
            if not isinstance(box, list) or len(box) != 4 or not all(
                isinstance(v, (int, float)) for v in box
            ):
                return f"option {i} needs a box [x0, y0, x1, y1] when an image is given"
            if box[2] <= box[0] or box[3] <= box[1]:
                return f"option {i} has an empty box {box}"
    return None


class Decider:
    """The model plus the policy: what one answer costs, and what it means.

    The load is deliberately separated from the answering (a `serve` warms it in a thread while
    `/health` already answers), and an image-less request takes the same path with no picture.
    """

    def __init__(self, model=DEFAULT_MODEL, edge=1536, four_bit=True,
                 policy="default", policy_file=None):
        self.model = pathlib.Path(model)
        self.edge = edge
        self.four_bit = four_bit
        self.policy = policy
        self.policy_file = policy_file
        self._lock = threading.Lock()
        self._reader = None
        self._vlm = None
        self.load_s = 0.0
        self.load_error = ""
        self.requests = 0
        self.last_used = time.time()

    def load(self):
        """Load the weights (once), or raise the reason they cannot be loaded."""
        with self._lock:
            if self._reader is not None:
                return self._reader
            if self.load_error:
                raise RuntimeError(self.load_error)
            t0 = time.perf_counter()
            try:
                import vlm_pick  # torch, transformers and the 4-bit config arrive with it
                self._vlm = vlm_pick
                self._reader = vlm_pick.Reader(
                    str(self.model), four_bit=self.four_bit, edge=self.edge
                )
            except Exception as exc:  # noqa: BLE001 - the reason is the whole point
                self.load_error = f"{type(exc).__name__}: {exc}"
                raise
            self.load_s = time.perf_counter() - t0
        return self._reader

    def answered(self) -> bool:
        return self._reader is not None

    def answer(self, req: dict) -> dict:
        """One request -> one response. `ValueError` means the request itself is wrong."""
        problem = bad_request(req)
        if problem:
            raise ValueError(problem)
        reader = self.load()
        vlm = self._vlm
        options = req["options"]
        rows = [
            {"n": i, "name": str(opt.get("label") or ""), "rect": tuple(opt.get("box") or ())}
            for i, opt in enumerate(options, 1)
        ]
        image = req.get("image")
        img = None
        marked = ""
        if image:
            img = vlm.annotate(str(image), rows, origin=(0, 0))
            # The picture the model looked at is kept every time: an UNDECIDED answer is handed
            # up to a bigger model, and it has to see the SAME numbering to be mapped back.
            marked = str(pathlib.Path(str(image)).with_suffix(".marked.png"))
            img.save(marked, "PNG")
        t0 = time.perf_counter()
        probs, diag = reader.ask(img, str(req["intent"]), len(rows))
        order, top, peak, confidence, decision, entry = vlm.decide(
            probs, len(rows), self.policy, self.policy_file
        )
        self.requests += 1
        self.last_used = time.time()
        payload = {
            "contract": CONTRACT,
            "decision": "YES" if decision == "YES" else "UNDECIDED",
            "id": options[top - 1]["id"],
            "index": top,
            "p": peak,
            "confidence": confidence,
            "threshold": entry["threshold"],
            "policy": entry["policy"],
            "options": [
                {"index": lab, "id": options[lab - 1]["id"], "label": rows[lab - 1]["name"], "p": p}
                for lab, p in order
            ],
            "ms": round((time.perf_counter() - t0) * 1000, 1),
            "diag": diag,
        }
        if marked:
            payload["marked"] = marked
        return payload

    def health(self, loading: bool) -> dict:
        return {
            "contract": CONTRACT,
            "ok": True,
            "model": str(self.model),
            "model_present": model_present(self.model),
            "loaded": self.answered(),
            "loading": loading,
            "load_s": round(self.load_s, 1),
            "load_error": self.load_error,
            "k": MAX_OPTIONS,
            "edge": self.edge,
            "four_bit": self.four_bit,
            "policy": self.policy,
            "requests": self.requests,
            "idle_s": round(time.time() - self.last_used, 1),
        }


# ── the resident shape: HTTP on loopback, the weights loaded once ───────────
def _handler_for(decider: Decider, state: dict, stop):
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "bixian-decider/1"

        def log_message(self, fmt, *args):  # noqa: ARG002 - one line per call is noise
            return

        def _send(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's spelling
            if self.path.split("?")[0] == "/health":
                self._send(200, decider.health(state["loading"]))
            else:
                self._send(404, {"contract": CONTRACT, "error": f"no such path: {self.path}"})

        def do_POST(self):  # noqa: N802
            route = self.path.split("?")[0]
            if route == "/shutdown":
                self._send(200, {"contract": CONTRACT, "ok": True, "stopping": True})
                threading.Thread(target=stop, daemon=True).start()
                return
            if route != "/decide":
                self._send(404, {"contract": CONTRACT, "error": f"no such path: {self.path}"})
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
                req = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, TypeError) as exc:
                self._send(400, {"contract": CONTRACT, "error": f"unreadable body: {exc}"})
                return
            try:
                self._send(200, decider.answer(req))
            except ValueError as exc:
                self._send(400, {"contract": CONTRACT, "error": str(exc)})
            except Exception as exc:  # noqa: BLE001
                state["error"] = f"{type(exc).__name__}: {exc}"
                self._send(503, {"contract": CONTRACT, "error": state["error"]})

    return Handler


def serve(args) -> int:
    from http.server import ThreadingHTTPServer

    decider = Decider(args.model, edge=args.edge, four_bit=args.four_bit,
                      policy=args.policy, policy_file=args.policy_file)
    state = {"loading": False, "error": ""}
    if not model_present(decider.model):
        # Start anyway: /health is exactly where "the weights are not here" has to be readable,
        # and refusing to start would make that look like a connection problem.
        print(f"decider: no model at {decider.model} — /health reports it, /decide will fail",
              file=sys.stderr, flush=True)
    else:
        state["loading"] = True

        def warm() -> None:
            try:
                decider.load()
                print(f"decider: weights loaded in {decider.load_s:.1f}s", file=sys.stderr, flush=True)
            except Exception as exc:  # noqa: BLE001
                state["error"] = f"{type(exc).__name__}: {exc}"
                print(f"decider: load failed: {state['error']}", file=sys.stderr, flush=True)
            finally:
                state["loading"] = False

        threading.Thread(target=warm, daemon=True).start()

    # `shutdown()` has to be called from another thread than `serve_forever()`'s, and stopping
    # has to go through it: `os._exit` would skip the socket's close and leave the port bound.
    server_holder = {}

    def stop() -> None:
        with contextlib.suppress(KeyError):
            threading.Thread(target=server_holder["server"].shutdown, daemon=True).start()

    server = ThreadingHTTPServer((args.host, args.port), _handler_for(decider, state, stop))
    server_holder["server"] = server
    print(f"decider: listening on http://{args.host}:{args.port} "
          f"(k={MAX_OPTIONS}, edge={args.edge}, {'4-bit' if args.four_bit else 'bf16'}, "
          f"idle-exit {args.idle}s)", file=sys.stderr, flush=True)

    if args.idle > 0:
        def watch() -> None:
            while True:
                time.sleep(5)
                if not state["loading"] and decider.health(False)["idle_s"] > args.idle:
                    print(f"decider: idle for {args.idle}s — stopping (the card goes back)",
                          file=sys.stderr, flush=True)
                    server.shutdown()
                    return

        threading.Thread(target=watch, daemon=True).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def ask(args) -> int:
    raw = sys.stdin.read() if args.request in ("-", "") else pathlib.Path(args.request).read_text(
        encoding="utf-8")
    try:
        req = json.loads(raw)
    except ValueError as exc:
        print(json.dumps({"contract": CONTRACT, "error": f"unreadable request: {exc}"}))
        return 1
    decider = Decider(args.model, edge=args.edge, four_bit=args.four_bit,
                      policy=args.policy, policy_file=args.policy_file)
    try:
        payload = decider.answer(req)
    except ValueError as exc:
        print(json.dumps({"contract": CONTRACT, "error": str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"contract": CONTRACT, "error": f"{type(exc).__name__}: {exc}"},
                         ensure_ascii=False))
        return 1
    if decider.answered():
        print(f"loaded in {decider.load_s:.1f}s", file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if payload["decision"] == "YES" else 2


def check(args) -> int:
    present = model_present(args.model)
    print(json.dumps({
        "contract": CONTRACT,
        "available": present,
        "model": str(pathlib.Path(args.model)),
        "model_present": present,
        "k": MAX_OPTIONS,
    }, ensure_ascii=False))
    return 0 if present else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="decider.py", description=__doc__.splitlines()[0])
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--model", default=str(DEFAULT_MODEL), help="the weights directory")
    common.add_argument("--edge", type=int, default=1536, help="long edge; 1536 is the measured knee")
    common.add_argument("--no-4bit", dest="four_bit", action="store_false",
                        help="load bf16 (11.4 GiB, ~2x slower) instead of 4-bit nf4")
    common.add_argument("--policy", default="default", help="bixian policy name (policies/default.json)")
    common.add_argument("--policy-file", default=None)
    subs = ap.add_subparsers(dest="command", required=True)
    subs.add_parser("check", parents=[common], help="are the weights here? (no torch)")
    one = subs.add_parser("ask", parents=[common], help="one request per process")
    one.add_argument("--request", default="-", help="a request json file, or - for stdin")
    server = subs.add_parser("serve", parents=[common], help="resident HTTP on loopback")
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8111)
    server.add_argument("--idle", type=float, default=1800.0,
                        help="exit after this long with no request (0 = never; frees the card)")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "check":
        return check(args)
    if args.command == "ask":
        return ask(args)
    with contextlib.suppress(KeyboardInterrupt):
        return serve(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
