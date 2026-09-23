"""The command line: one line on stdout, and the exit code IS the verdict.

    0  YES / answered
    1  NO
    2  UNDECIDED -- service down, timeout, malformed answer, a flat
       distribution, or a gate the evidence did not clear.
       Callers MUST fall back to their own model. Nothing is ever guessed.

Global flags work on either side of the subcommand (`decide --json noul ...` and
`decide noul ... --json` are both valid), because an agent writing the second
form is the normal case.
"""

import argparse
import json
import os
import pathlib
import sys
import time

from . import __version__, audit, cache, client, policy, wire
from .errors import Undecided

DEFAULT_URL = "http://127.0.0.1:8009/v1/systemone"
EXIT_YES, EXIT_NO, EXIT_UNDECIDED = 0, 1, 2

SPEC = {
    "tool": "decide",
    "version": __version__,
    "transport": "POST {base}/v1/systemone",
    "discovery": "GET {base}/v1/models",
    "request": {
        "state": "str",
        "model": "str (optional)",
        "questions": {
            "<id>": {
                "type": "choice|noul|score",
                "instructions": "str",
                "criteria": "choice: {label: description} | score: [low, ..., high] | noul: omitted",
            }
        },
    },
    "response": {"model": "str", "answers": {"<id>": "answer"}, "usage": "object", "latency_ms": "float"},
    "answer_shapes": {
        "noul": {"type": "noul", "noul": "P(yes) in [0,1]"},
        "choice": {"type": "choice", "choice": "str", "probabilities": "{label: p}", "confidence": "top1-top2 MARGIN, not a probability"},
        "score": {"type": "score", "score": "CONTINUOUS float", "legend": "{index: label}", "probabilities": "{index: p}", "confidence": "margin"},
    },
    "exit_codes": {"0": "YES / answered", "1": "NO", "2": "UNDECIDED - fall back, never guess"},
    "policy_file": "policies/default.json",
    "policy_schema": {
        "named": {"<policy>": {"threshold": "float", "on_fail": "fail_open|fail_closed"}},
        "classes": {"<action class>": "<policy>"},
        "unknown_class": "<policy> -- must be the strictest one",
    },
    "audit_schema": {
        "file": str(audit.audit_path()),
        "fields": list(audit.REQUIRED_FIELDS)
        + ["workload", "state_sha256", "state_len", "model", "checkpoint_temperature",
           "p", "peak", "margin", "threshold", "action_taken", "latency_ms", "cached", "reason"],
        "text_preview": "off by default; BIXIAN_AUDIT_TEXT=1 opt-in, truncated + redacted",
    },
    "env": {
        "DECIDE_URL": "service endpoint",
        "DECIDE_MODEL": "model name sent in the request (e.g. kev-latest)",
        "DECIDE_TIMEOUT": "seconds, default 3",
        "DECIDE_KEY": "bearer token, optional",
        "BIXIAN_POLICY": "policy file path",
        "BIXIAN_AUDIT": "audit jsonl path",
        "BIXIAN_CACHE": "cache jsonl path",
    },
}


def _add_common(parser, suppress):
    def d(value=None):
        return argparse.SUPPRESS if suppress else value

    parser.add_argument("--url", default=d(os.environ.get("DECIDE_URL") or DEFAULT_URL),
                        help="decision service base URL or endpoint")
    parser.add_argument("--model", default=d(os.environ.get("DECIDE_MODEL") or ""),
                        help="model name for the request (e.g. kev-latest)")
    parser.add_argument("--timeout", type=float,
                        default=d(float(os.environ.get("DECIDE_TIMEOUT") or client.DEFAULT_TIMEOUT)))
    parser.add_argument("--policy", default=d(None),
                        help="policy name (explore|default|irreversible) or an action class")
    parser.add_argument("--threshold", type=float, default=d(0.5),
                        help="plain decision boundary; a --policy can only make it stricter")
    parser.add_argument("--origin", choices=["trusted", "untrusted"], default=d("untrusted"),
                        help="where the state came from; irreversible gates require 'trusted'")
    parser.add_argument("--workload", default=d(None), help="name from workloads/workloads.jsonl")
    parser.add_argument("--json", action="store_true", default=d(False),
                        help="print one JSON object instead of the bare verdict")
    parser.add_argument("--no-cache", action="store_true", default=d(False))
    parser.add_argument("--no-audit", action="store_true", default=d(False))
    return parser


def build_parser():
    ap = argparse.ArgumentParser(prog="decide", description=__doc__.splitlines()[0])
    ap.add_argument("--version", action="version", version="decide " + __version__)
    _add_common(ap, suppress=False)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def sub_parser(name, help_text):
        return _add_common(sub.add_parser(name, help=help_text), suppress=True)

    n = sub_parser("noul", "probability of yes; exit 0 = YES, 1 = NO")
    n.add_argument("state")
    n.add_argument("question")
    n.set_defaults(fn=cmd_noul)

    c = sub_parser("choice", "pick one of N options")
    c.add_argument("state")
    c.add_argument("question")
    c.add_argument("--options", required=True, help="comma-separated labels")
    c.add_argument("--index", action="store_true", help="print the 1-based number instead of the label")
    c.set_defaults(fn=cmd_choice)

    s = sub_parser("score", "rate on an ordered scale")
    s.add_argument("state")
    s.add_argument("question")
    s.add_argument("--levels", required=True, help="comma-separated, lowest first")
    s.add_argument("--index", action="store_true", help="print the 0-based bucket only")
    s.set_defaults(fn=cmd_score)

    sub_parser("ask", "raw request on stdin, raw JSON out").set_defaults(fn=cmd_ask)
    sub_parser("spec", "machine-readable contract").set_defaults(fn=cmd_spec)
    sub_parser("selftest", "prove the service is wired").set_defaults(fn=cmd_selftest)
    return ap


def _models_ttl():
    """Seconds a cached identity row may be reused; 0 (the default) asks every time."""
    try:
        return float(os.environ.get("BIXIAN_MODELS_TTL", "0"))
    except ValueError:
        return 0.0


def _identity(info):
    """What makes one backend different from another one behind the same URL."""
    return (info or {}).get("run") or (info or {}).get("model")


def _model_meta(args):
    """Model id, checkpoint temperature and run identity, from GET /v1/models.

    Once per decision by default (BIXIAN_MODELS_TTL=0). This row IS the backend's
    identity: it supplies the audit's model/temperature and it is part of the cache
    key. Caching it for an hour looked cheap until a backend was swapped behind the
    same URL: the new service was then asked nothing, answered with the old
    service's cached numbers, and the audit kept naming the old run. There is
    deliberately no in-process memo either -- that would move the same staleness
    into a long-lived process that imports this CLI. Set BIXIAN_MODELS_TTL=<seconds>
    to trade the freshness back for a saved GET; the sidecar then lives in
    ~/.bixian/models.json (or $BIXIAN_MODELS).
    """
    ttl = _models_ttl()
    sidecar = pathlib.Path(os.environ.get("BIXIAN_MODELS")
                           or (pathlib.Path.home() / ".bixian" / "models.json"))
    if ttl > 0:
        try:
            row = (json.loads(sidecar.read_text(encoding="utf-8")).get(args.url) or {})
            if time.time() - float(row.get("ts", 0)) < ttl:
                return row.get("info") or {}
        except (OSError, ValueError):
            pass
    info = client.model_info(args.url, args.timeout, key=os.environ.get("DECIDE_KEY"))
    if ttl > 0:
        try:
            sidecar.parent.mkdir(parents=True, exist_ok=True)
            blob = {}
            if sidecar.is_file():
                try:
                    blob = json.loads(sidecar.read_text(encoding="utf-8"))
                except ValueError:
                    blob = {}
            blob[args.url] = {"ts": time.time(), "info": info}
            sidecar.write_text(json.dumps(blob, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
    return info


def _ask(args, state, question, qid="q"):
    payload = {"state": state, "questions": {qid: question}}
    if args.model:
        payload["model"] = args.model
    info = _model_meta(args)          # the identity: into the cache key, and into the audit
    key = None
    if not args.no_cache:
        key = cache.make_key(args.url, payload, args.model, _identity(info))
        hit = cache.get(key)
        if hit is not None:
            return hit["a"], 0.0, True, hit.get("meta") or {}
    started = time.perf_counter()
    data = client.post(args.url, payload, args.timeout, key=os.environ.get("DECIDE_KEY"))
    ms = (time.perf_counter() - started) * 1000.0
    answer = data["answers"].get(qid)
    if not isinstance(answer, dict):
        raise Undecided("no answer for %r" % (qid,))
    meta = {"model": data.get("model"), "server_ms": data.get("latency_ms"), "info": info}
    meta = {k: v for k, v in meta.items() if v is not None}
    if key:
        cache.put(key, answer, meta)
    return answer, ms, False, meta


def _emit(args, line, detail, code, state, qtype, decision, ctx):
    if not args.no_audit:
        audit.record(audit.make_row(state, qtype, decision, ctx))
        detail.setdefault("audit", str(audit.audit_path()))
    if args.json:
        print(json.dumps(detail, ensure_ascii=False))
    else:
        print(line)
    sys.exit(code)


def _context(args, entry, meta, ms, cached, **extra):
    # `info` rides along with the answer: asking again here would pay a second GET
    # (and, worse, could record a different identity than the one in the cache key).
    info = meta.get("info") or _model_meta(args)
    ctx = {
        "workload": args.workload,
        "model": meta.get("model") or info.get("model"),
        "temperature": info.get("temperature"),
        "threshold": entry.get("threshold") if entry else None,
        "policy": entry.get("policy") if entry else None,
        "on_fail": entry.get("on_fail") if entry else "fail_closed",
        "origin": args.origin,
        "ms": round(ms, 1),
        "cached": cached,
    }
    ctx.update({k: v for k, v in extra.items() if v is not None})
    return ctx


def _entry(args):
    if not args.policy:
        # A plain call: no policy named, so the only rule is the boundary itself.
        # `fail_open` because the caller asked a question, not for an authorization
        # gate -- irreversible actions are expected to name a policy (see SKILL.md).
        return {"class": None, "policy": None, "declared": False,
                "threshold": args.threshold, "on_fail": "fail_open"}
    return policy.resolve(policy.load(), args.policy)


def cmd_noul(args):
    entry = _entry(args)
    policy.require_origin(entry, args.origin)
    boundary = args.threshold
    required = entry["threshold"] if args.policy else boundary
    answer, ms, cached, meta = _ask(args, args.state, wire.q_noul(args.question))
    p = wire.read_noul(answer)
    decision, code = policy.decide_noul(p, boundary, required)
    ctx = _context(args, entry, meta, ms, cached, p=round(p, 4))
    detail = {
        "type": "noul", "p": round(p, 4), "threshold": required, "boundary": boundary,
        "policy": entry["policy"], "on_fail": entry["on_fail"], "origin": args.origin,
        "decision": decision, "ms": round(ms, 1), "cached": cached,
    }
    detail.update({k: v for k, v in meta.items() if k in ("model", "server_ms")})
    _emit(args, decision, detail, code, args.state, "noul", decision, ctx)


def cmd_choice(args):
    options = [o.strip() for o in args.options.split(",") if o.strip()]
    if len(options) < 2:
        sys.exit("--options needs at least 2 labels")
    entry = _entry(args)
    policy.require_origin(entry, args.origin)
    answer, ms, cached, meta = _ask(args, args.state, wire.q_choice(args.question, options))
    got = wire.read_choice(answer, options)
    ctx = _context(args, entry, meta, ms, cached, peak=got["peak"], margin=got["margin"])
    detail = {
        "type": "choice", "choice": got["choice"], "index": got["index"] + 1,
        "peak": got["peak"], "margin": got["margin"], "probabilities": got["probabilities"],
        "threshold": entry["threshold"], "policy": entry["policy"], "on_fail": entry["on_fail"],
        "origin": args.origin, "ms": round(ms, 1), "cached": cached,
    }
    detail.update({k: v for k, v in meta.items() if k in ("model", "server_ms")})
    if got["uniform"]:
        _emit(args, "UNDECIDED", dict(detail, decision="UNDECIDED",
                                      reason="flat distribution: no signal, or options the model cannot tell apart"),
              EXIT_UNDECIDED, args.state, "choice", "UNDECIDED",
              dict(ctx, reason="flat distribution"))
    if args.policy and got["peak"] is not None and got["peak"] < entry["threshold"]:
        _emit(args, "UNDECIDED", dict(detail, decision="UNDECIDED", reason="peak below policy threshold"),
              EXIT_UNDECIDED, args.state, "choice", "UNDECIDED",
              dict(ctx, reason="peak below policy threshold"))
    _emit(args, str(got["index"] + 1) if args.index else got["choice"],
          dict(detail, decision="ANSWERED"), EXIT_YES, args.state, "choice", "ANSWERED", ctx)


def cmd_score(args):
    levels = [l.strip() for l in args.levels.split(",") if l.strip()]
    if len(levels) < 2:
        sys.exit("--levels needs at least 2 levels")
    entry = _entry(args)
    policy.require_origin(entry, args.origin)
    answer, ms, cached, meta = _ask(args, args.state, wire.q_score(args.question, levels))
    got = wire.read_score(answer, levels)
    ctx = _context(args, entry, meta, ms, cached, p=got["score"])
    detail = {
        "type": "score", "score": got["score"], "index": got["index"], "label": got["label"],
        "legend": got["legend"], "probabilities": got["probabilities"],
        "policy": entry["policy"], "on_fail": entry["on_fail"], "origin": args.origin,
        "ms": round(ms, 1), "cached": cached,
    }
    detail.update({k: v for k, v in meta.items() if k in ("model", "server_ms")})
    if got["uniform"]:
        _emit(args, "UNDECIDED", dict(detail, decision="UNDECIDED", reason="flat distribution"),
              EXIT_UNDECIDED, args.state, "score", "UNDECIDED", dict(ctx, reason="flat distribution"))
    line = str(got["index"]) if args.index else "%.2f\t%s" % (got["score"], got["label"])
    _emit(args, line, dict(detail, decision="ANSWERED"), EXIT_YES, args.state, "score", "ANSWERED", ctx)


def cmd_ask(args):
    payload = json.loads(sys.stdin.read())
    started = time.perf_counter()
    data = client.post(args.url, payload, args.timeout, key=os.environ.get("DECIDE_KEY"))
    ms = (time.perf_counter() - started) * 1000.0
    if not args.no_audit:
        state = payload.get("state", "")
        info = _model_meta(args)
        for qid, answer in (data.get("answers") or {}).items():
            kind = (answer or {}).get("type") or "unknown"
            value = answer.get(kind) if isinstance(answer, dict) else None
            audit.record(audit.make_row(
                state, kind, "ANSWERED",
                {"workload": args.workload, "model": data.get("model"),
                 "temperature": info.get("temperature"),
                 "server_ms": data.get("latency_ms"), "origin": args.origin,
                 "p": value if isinstance(value, (int, float)) else None,
                 "ms": round(ms, 1)}))
    print(json.dumps(dict(data, ms=round(ms, 1)), ensure_ascii=False))
    sys.exit(EXIT_YES)


def cmd_spec(args):
    print(json.dumps(dict(SPEC, endpoint=client.endpoint(args.url)), ensure_ascii=False, indent=2))
    sys.exit(EXIT_YES)


def cmd_selftest(args):
    # A cached selftest would report OK while the service is dead -- the one thing
    # it must never do. Bypass the cache and ask the service for real.
    args.no_cache = True
    started = time.perf_counter()
    try:
        answer, ms, cached, meta = _ask(args, "selftest", wire.q_noul("Is this a selftest?"))
        p = wire.read_noul(answer)
    except Undecided as exc:
        print("UNDECIDED url=%s reason=%s" % (client.endpoint(args.url), exc))
        sys.exit(EXIT_UNDECIDED)
    info = client.model_info(args.url, args.timeout, key=os.environ.get("DECIDE_KEY"))
    print("OK url=%s model=%s temperature=%s ms=%.0f noul=%s" % (
        client.endpoint(args.url),
        info.get("model") or meta.get("model"),
        info.get("temperature"),
        (time.perf_counter() - started) * 1000.0,
        p,
    ))
    sys.exit(EXIT_YES)


def _fail(args, exc):
    """A refusal or an outage is an outcome too: one line, exit 2, and a record.

    The one-line stdout contract and the audit promise hold on every path -- if
    the caller cannot tell "no decision" from a crash, the contract is worthless.
    """
    reason = str(exc)
    entry = None
    if getattr(args, "cmd", None) in ("noul", "choice", "score"):
        try:
            entry = _entry(args)
        except Undecided:
            entry = None
    ctx = {
        "workload": getattr(args, "workload", None),
        "origin": getattr(args, "origin", "untrusted"),
        "policy": (entry or {}).get("policy"),
        "on_fail": (entry or {}).get("on_fail", "fail_closed"),
        "threshold": (entry or {}).get("threshold"),
        "reason": reason,
    }
    if entry is not None and not getattr(args, "no_audit", False):
        audit.record(audit.make_row(getattr(args, "state", ""), args.cmd, "UNDECIDED", ctx))
    sys.stderr.write("decide: %s\n" % reason)
    if getattr(args, "json", False):
        print(json.dumps({
            "type": getattr(args, "cmd", None), "decision": "UNDECIDED", "reason": reason,
            "policy": ctx.get("policy"), "on_fail": ctx.get("on_fail"),
            "origin": ctx.get("origin"), "audit": str(audit.audit_path()),
        }, ensure_ascii=False))
    else:
        print("UNDECIDED")
    sys.exit(EXIT_UNDECIDED)


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except Undecided as exc:
        _fail(args, exc)
    except KeyboardInterrupt:
        sys.exit(130)
