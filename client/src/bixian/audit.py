"""Audit: one append-only row per decision, on by default, zero configuration.

Why it exists (docs/design-handoff.md section 5):

1. It answers "why did the agent do that?" months later -- a probability, the
   threshold it was compared against, the model and temperature behind it, and
   what was actually done.
2. It is the DATA SOURCE for re-estimating thresholds. Without this log the
   thresholds can never be re-fit, so the log is not compliance overhead -- it is
   the raw material for the next step.

It is written by the CALLER (this CLI), because the service is a third-party
component and because only the caller knows the final action taken.

Privacy: only a sha256 of the state and its length are stored by default. Set
BIXIAN_AUDIT_TEXT=1 to store a truncated, opt-in summary. Nothing is sent
anywhere -- the file is local.
"""

import hashlib
import json
import os
import pathlib
import time
import uuid

REQUIRED_FIELDS = (
    "ts",
    "request_id",
    "type",
    "decision",
    "origin",
    "policy",
    "on_fail",
)

SUMMARY_CHARS = 240
REDACTIONS = ("sk-", "Bearer ", "token=", "password=", "api_key=")


def audit_path():
    env = os.environ.get("BIXIAN_AUDIT")
    if env:
        return pathlib.Path(env)
    return pathlib.Path.home() / ".bixian" / "audit.jsonl"


def state_digest(state):
    blob = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest(), len(blob)


def summarize(state):
    """Opt-in only: a truncated, redacted preview. Never on by default."""
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    for needle in REDACTIONS:
        while needle in text:
            head, _, tail = text.partition(needle)
            cut = tail.find(" ")
            text = head + needle + "[redacted]" + (tail[cut:] if cut != -1 else "")
    return text[:SUMMARY_CHARS]


def new_request_id():
    return uuid.uuid4().hex[:16]


def make_row(state, qtype, decision, ctx):
    sha, length = state_digest(state)
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "request_id": ctx.get("request_id") or new_request_id(),
        "workload": ctx.get("workload"),
        "type": qtype,
        "state_sha256": sha,
        "state_len": length,
        "model": ctx.get("model"),
        "checkpoint_temperature": ctx.get("temperature"),
        "p": ctx.get("p"),
        "peak": ctx.get("peak"),
        "margin": ctx.get("margin"),
        "threshold": ctx.get("threshold"),
        "policy": ctx.get("policy"),
        "on_fail": ctx.get("on_fail"),
        "decision": decision,
        "origin": ctx.get("origin"),
        "consumer": ctx.get("consumer") or "cli",
        "action_taken": ctx.get("action_taken"),
        "latency_ms": ctx.get("ms"),
        "server_ms": ctx.get("server_ms"),
        "cached": ctx.get("cached"),
        "reason": ctx.get("reason"),
    }
    if os.environ.get("BIXIAN_AUDIT_TEXT") == "1":
        row["state_preview"] = summarize(state)
    return {k: v for k, v in row.items() if v is not None}


def record(row, path=None):
    """Append one row. An unwritable audit log must never break a decision."""
    try:
        path = pathlib.Path(path) if path else audit_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass
