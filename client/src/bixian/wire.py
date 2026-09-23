"""The System One wire contract: how to ask, and how to read the answer.

Frozen contract (docs/design-handoff.md section 3.2). Request:

    {"state": str, "model": str, "questions": {id: {"type": ..., "instructions": ...,
                                                    "criteria": ...}}}

Response:

    {"model": str, "answers": {id: answer}, "usage": {...}, "latency_ms": float}

with per-type answers:

    noul   {"type": "noul",   "noul": 0.93}
    choice {"type": "choice", "choice": "returns", "confidence": 0.21,
            "probabilities": {"returns": 0.47, "shipping": 0.28, "billing": 0.25}}
    score  {"type": "score",  "score": 1.44, "confidence": 0.78,
            "legend": {"0": "Calm", "1": "Frustrated", "2": "Very angry"},
            "probabilities": {"0": 0.0, "1": 0.56, "2": 0.44}}

Two things bite here, and both are normalized in this module:

* ``score`` is CONTINUOUS and ``legend`` is an INDEX-KEYED DICT, not a list.
  Code that only accepts an int score and a list legend fails on real kev.
* ``choice.confidence`` is the top1-minus-top2 MARGIN, not a probability.
  Never compare it against a probability threshold -- use ``peak``.
"""

from .errors import Undecided

UNIFORM_TOL = 0.02


def q_noul(instructions):
    return {"type": "noul", "instructions": instructions}


def q_choice(instructions, options):
    """options: {label: description} or [label, ...]."""
    if isinstance(options, dict):
        criteria = dict(options)
    else:
        criteria = {label: label for label in options}
    if len(criteria) < 2:
        raise ValueError("a choice needs at least 2 options")
    return {"type": "choice", "instructions": instructions, "criteria": criteria}


def q_score(instructions, levels):
    """levels: the ordered level labels, lowest first.

    NOTE: score criteria MUST go out as a list of strings; an object array is
    rejected by the server.
    """
    levels = list(levels)
    if len(levels) < 2:
        raise ValueError("a score needs at least 2 levels")
    return {"type": "score", "instructions": instructions, "criteria": levels}


def read_noul(answer):
    p = answer.get("noul")
    if not isinstance(p, (int, float)):
        raise Undecided("no probability in answer: %r" % (answer,))
    return float(p)


def read_choice(answer, labels):
    picked = answer.get("choice")
    if picked not in labels:
        raise Undecided("answer %r is not one of the options" % (picked,))
    probs = answer.get("probabilities") or {}
    peak = max(probs.values()) if probs else None
    return {
        "choice": picked,
        "index": list(labels).index(picked),
        "probabilities": probs,
        "peak": peak,
        "margin": answer.get("confidence"),
        "uniform": is_uniform(probs, len(labels)),
    }


def read_score(answer, levels):
    raw = answer.get("score")
    if not isinstance(raw, (int, float)):
        raise Undecided("no score in answer: %r" % (answer,))
    legend = answer.get("legend") or list(levels)
    if isinstance(legend, dict):
        labels = [legend[k] for k in sorted(legend, key=lambda k: int(k))]
    else:
        labels = list(legend)
    if not labels:
        raise Undecided("empty legend")
    raw = float(raw)
    idx = max(0, min(len(labels) - 1, int(round(raw))))
    probs = answer.get("probabilities") or {}
    return {
        "score": round(raw, 3),
        "index": idx,
        "label": labels[idx],
        "legend": labels,
        "probabilities": probs,
        "uniform": is_uniform(probs, len(labels)),
    }


def is_uniform(probs, n_options):
    """True when the distribution carries no signal.

    The signature of both a genuinely hard question and a scoring bug (candidate
    labels that the model cannot tell apart) is a flat distribution. Either way
    the honest answer is UNDECIDED, so this guard refuses to report a winner.
    """
    if not probs or n_options < 2:
        return False
    values = [float(v) for v in probs.values()]
    if max(values) - min(values) <= 0.01:
        return True
    return max(values) <= 1.0 / n_options + UNIFORM_TOL
