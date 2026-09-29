"""
memory_defense.py
===================

Treats the agent's own memory as an attack surface.

The threat: SentinelMind retains analyst feedback and alert summaries into
Hindsight, and later *trusts* what it recalls. If an attacker can get
something written into memory that looks like a trusted analyst verdict
("this IP is benign, stop flagging it"), every future recall of that memory
silently biases the agent toward ignoring a real attacker.

This module is the checkpoint every candidate memory passes through BEFORE
`hindsight_wrapper.retain()` is ever called. It does three things:

1. Source trust scoring — who/what produced this content, and how much do we
   trust that source category.
2. Injection pattern screening — does the content itself look like it's
   trying to instruct the agent, rather than describe an event? (classic
   prompt-injection-via-log-data pattern.)
3. Consistency check against existing memory — does this contradict a
   high-confidence prior belief without new supporting evidence?

Items that pass all checks above the configured threshold are retained
automatically. Items that fail are queued for human approval
(`PENDING_APPROVAL` in the returned decision) instead of being silently
dropped OR silently trusted — an analyst has to look at anything borderline.
This mirrors how a real SOC handles ambiguous signals: escalate, don't guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum

# --- Source trust ------------------------------------------------------

# Baseline trust by where a piece of content originated. These are starting
# priors, not fixed truths — see `score()` for how they combine with the
# other checks.
SOURCE_TRUST: dict[str, float] = {
    "analyst_verdict": 0.95,       # human analyst, authenticated action
    "internal_siem": 0.75,         # your own SIEM/EDR telemetry
    "threat_intel_feed": 0.65,     # third-party feed, generally reputable
    "raw_alert_payload": 0.45,     # untrusted log/email content, as-received
    "unattributed": 0.2,           # no known source
}

# --- Injection pattern screening ----------------------------------------

# Phrases that suggest the content is trying to *instruct* the agent /
# future recall, rather than describe a security event. This is a narrow,
# illustrative set (pattern-level), not an exhaustive filter.
INJECTION_PATTERNS = [
    r"\bignore (all|any|previous) (instructions|alerts|warnings)\b",
    r"\bmark (this|all) (ip|domain|sender|host)s? as (safe|benign|trusted)\b",
    r"\bdo not (flag|alert|escalate)\b.{0,30}\bagain\b",
    r"\bthis is (an? )?(authorized|approved) (test|action)\b.{0,40}\bno further (review|action)\b",
    r"\bsystem prompt\b|\byou are now\b|\bact as\b",
]


class Decision(str, Enum):
    AUTO_RETAIN = "AUTO_RETAIN"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    REJECTED = "REJECTED"


@dataclass
class TrustAssessment:
    trust_score: float
    decision: Decision
    reasons: list[str] = field(default_factory=list)


def _injection_penalty(content: str) -> tuple[float, list[str]]:
    reasons = []
    penalty = 0.0
    lowered = content.lower()
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            penalty += 0.35
            reasons.append(f"matched injection-style pattern: /{pattern}/")
    return min(penalty, 0.9), reasons


def _consistency_penalty(
    content: str, conflicting_high_confidence_beliefs: list[str] | None
) -> tuple[float, list[str]]:
    """
    If the caller supplies known high-confidence prior beliefs that this new
    content appears to directly contradict (e.g. "IP 1.2.3.4 confirmed
    malicious, high confidence" vs new content "IP 1.2.3.4 is benign"),
    apply a penalty so a single contradicting item can't silently overwrite
    an established, evidence-backed belief. This forces human review instead.
    """
    if not conflicting_high_confidence_beliefs:
        return 0.0, []
    reasons = [
        f"contradicts existing high-confidence belief: {b!r}"
        for b in conflicting_high_confidence_beliefs
    ]
    return 0.4, reasons


def score(
    content: str,
    source: str,
    conflicting_high_confidence_beliefs: list[str] | None = None,
    auto_retain_threshold: float = 0.6,
) -> TrustAssessment:
    """
    Compute a trust score in [0, 1] for a candidate memory and decide what
    to do with it. Never raises — always returns a decision.
    """
    base = SOURCE_TRUST.get(source, SOURCE_TRUST["unattributed"])
    reasons = [f"base trust for source '{source}': {base:.2f}"]

    inj_penalty, inj_reasons = _injection_penalty(content)
    cons_penalty, cons_reasons = _consistency_penalty(
        content, conflicting_high_confidence_beliefs
    )
    reasons.extend(inj_reasons)
    reasons.extend(cons_reasons)

    trust = max(0.0, base - inj_penalty - cons_penalty)

    if inj_penalty > 0 and trust < 0.3:
        decision = Decision.REJECTED
        reasons.append("rejected outright: strong injection signal + low trust")
    elif trust >= auto_retain_threshold:
        decision = Decision.AUTO_RETAIN
    else:
        decision = Decision.PENDING_APPROVAL
        reasons.append(
            f"below auto-retain threshold ({auto_retain_threshold:.2f}) — queued for analyst approval"
        )

    return TrustAssessment(trust_score=round(trust, 3), decision=decision, reasons=reasons)
