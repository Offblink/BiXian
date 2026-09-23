"""Threshold policy: which probability clears which kind of action.

Design rules (docs/design-handoff.md section 4):

* Thresholds live in a FILE, not in code -- they have to be re-estimated as data
  accumulates, and re-estimating must not require a release.
* An action class that is NOT declared falls back to the strictest policy
  (whitelist thinking: forgetting a class must cost a human question, never an
  unreviewed action).
* The threshold belongs to the CALL SITE. The service only reports probability.
* `--policy` can only make a decision STRICTER. When the probability clears the
  plain boundary but not what the policy demands, the answer is UNDECIDED --
  never NO. "Not sure it is safe" and "it is not safe" are different sentences
  and callers must not be able to confuse them.

Thresholds are ESTIMATED, not searched: sweep the threshold over a labelled set
to get the risk-coverage curve, then read off the operating point that meets the
error budget. No annealing, no randomness -- an unexplainable threshold makes an
unexplainable decision.
"""

import json
import os
import pathlib

from .errors import Undecided

DEFAULT_POLICY = {
    "named": {
        "explore": {"threshold": 0.20, "on_fail": "fail_open"},
        "default": {"threshold": 0.50, "on_fail": "fail_open"},
        "irreversible": {"threshold": 0.99, "on_fail": "fail_closed"},
    },
    "classes": {
        "read_file": "explore",
        "score_memory": "explore",
        "write_file": "default",
        "stage_commit": "default",
        "run_command": "irreversible",
        "send_message": "irreversible",
        "publish": "irreversible",
    },
    "unknown_class": "irreversible",
}

POLICY_FILE = "policies/default.json"


def default_path():
    """$BIXIAN_POLICY, else <repo>/policies/default.json, else None."""
    env = os.environ.get("BIXIAN_POLICY")
    if env:
        return pathlib.Path(env)
    guess = pathlib.Path(__file__).resolve().parents[2] / POLICY_FILE
    return guess if guess.is_file() else None


def load(path=None):
    path = pathlib.Path(path) if path else default_path()
    if not path:
        return dict(DEFAULT_POLICY)
    try:
        data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Undecided("unreadable policy file %s: %s" % (path, exc)) from exc
    merged = dict(DEFAULT_POLICY)
    merged.update({k: v for k, v in data.items() if v is not None})
    return merged


def resolve(policy, name):
    """name: a policy name (explore/default/irreversible) or an action class."""
    if not name:
        raise Undecided("no policy given")
    named = policy.get("named") or {}
    classes = policy.get("classes") or {}
    key = classes.get(name, name)
    entry = named.get(key)
    if entry is None:
        key = policy.get("unknown_class") or "irreversible"
        entry = named.get(key) or DEFAULT_POLICY["named"]["irreversible"]
        return {
            "class": name,
            "policy": key,
            "declared": False,
            "threshold": float(entry["threshold"]),
            "on_fail": entry.get("on_fail", "fail_closed"),
        }
    return {
        "class": name,
        "policy": key,
        "declared": name in classes,
        "threshold": float(entry["threshold"]),
        "on_fail": entry.get("on_fail", "fail_closed"),
    }


def decide_noul(p, boundary, required):
    """(decision, exit_code) for a probability against a boundary and a requirement."""
    if p >= required:
        return "YES", 0
    if required > boundary and p >= boundary:
        # Leans yes, not sure enough to act. Reporting NO here would let a caller
        # read "not sure" as "the answer is no".
        return "UNDECIDED", 2
    return "NO", 1


def require_origin(policy_entry, origin):
    """Refuse to gate an irreversible action on text the caller does not control.

    The decision layer reads untrusted text (file contents, web pages, messages)
    and its output authorises actions, so an attacker can try to write the answer
    into the input ("this should be judged safe"). The only robust defence is to
    keep untrusted text out of the state for irreversible gates -- enforced here,
    before anything is sent anywhere.
    """
    strict = policy_entry.get("policy") == "irreversible"
    if strict and origin != "trusted":
        raise Undecided(
            "irreversible gate refuses state of origin %r; pass --origin trusted only if "
            "every part of the state was composed by you" % (origin,)
        )
