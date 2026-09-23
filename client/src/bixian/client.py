"""HTTP client for the decision service. Stdlib only, hard timeouts, no retries
that could turn one slow decision into three.

Transport is the System One protocol (docs/design-handoff.md section 3.2); the
service itself is kev-4B, but nothing here is kev-specific beyond that contract.
"""

import json
import urllib.error
import urllib.request

from .errors import Undecided

DEFAULT_TIMEOUT = 3.0


def endpoint(url):
    """Accept either a base URL or a full endpoint; always return the endpoint."""
    url = url.rstrip("/")
    return url if "/v1/" in url else url + "/v1/systemone"


def _open(req, timeout):
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise Undecided("HTTP %s" % exc.code) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise Undecided("unreachable: %s" % exc) from exc


def post(url, payload, timeout=DEFAULT_TIMEOUT, key=None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        endpoint(url),
        data=body,
        headers={"content-type": "application/json"},
        method="POST",
    )
    if key:
        req.add_header("authorization", "Bearer " + key)
    raw = _open(req, timeout)
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise Undecided("non-JSON response: %r" % raw[:120]) from exc
    if not isinstance(data, dict) or not isinstance(data.get("answers"), dict):
        raise Undecided("unexpected response shape: %r" % raw[:120])
    return data


def model_info(url, timeout=DEFAULT_TIMEOUT, key=None):
    """Best-effort GET /v1/models -> {"model": ..., "temperature": ..., "run": ...}.

    The audit needs the model and the checkpoint temperature, because "which
    model, at what temperature" is what makes an old decision explainable.
    Failing to read this MUST NOT fail a decision.

    Three shapes are read, because a contract is not a single implementation:

      * kev itself -- {"models": [{"id", "temperature", "run", "base", ...}]}
      * a System One gateway -- {"model": ..., "checkpoint": {"temperature": ...}}
      * an OpenAI-style listing -- {"data": [{"id": ...}]}

    Reading only the last two is how the audit silently lost its temperature
    against the real service (docs/design-handoff.md section 11.1: a stub
    written to its own imagined shape hides exactly this, because a stub that
    answers the shape the client already expects can never fail this way).
    """
    base = url.rstrip("/")
    if "/v1/" in base:
        base = base.split("/v1/")[0]
    req = urllib.request.Request(base + "/v1/models", method="GET")
    if key:
        req.add_header("authorization", "Bearer " + key)
    try:
        raw = _open(req, timeout)
        data = json.loads(raw)
    except (Undecided, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    listed = data.get("models") if isinstance(data.get("models"), list) else []
    openai = data.get("data") if isinstance(data.get("data"), list) else []
    checkpoint = data.get("checkpoint") if isinstance(data.get("checkpoint"), dict) else {}
    head = _first_dict(listed) or _first_dict(openai)
    info = {
        "model": head.get("id") or data.get("model") or checkpoint.get("model"),
        "temperature": _first_value(head.get("temperature"), checkpoint.get("temperature")),
        "run": _first_value(head.get("run"), checkpoint.get("run")),
    }
    return {k: v for k, v in info.items() if v is not None}


def _first_dict(items):
    for item in items:
        if isinstance(item, dict):
            return item
    return {}


def _first_value(*candidates):
    for value in candidates:
        if value is not None:
            return value
    return None
