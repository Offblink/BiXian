class Undecided(Exception):
    """No decision was obtained.

    Raised for: service unreachable, timeout, malformed answer, a degenerate
    (uniform) distribution, or a policy gate that the evidence did not clear.

    Callers MUST treat this as "no decision" and fall back to their own model.
    Nothing is ever guessed -- see docs/design-handoff.md section 0.
    """
