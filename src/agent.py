"""
agent.py
=========

SentinelAgent: triages a security alert in one of two modes.

- mode="stateless": reasons using only the alert itself.
- mode="memory": first calls Hindsight's `recall` for relevant history,
  then reasons using the alert + recalled context, and reports exactly
  which recalled memories (if any) changed the verdict.

Reasoning backend is pluggable (`src/llm_client.py`-style callable). By
default this repo uses a deterministic heuristic reasoner so the whole
system runs with zero external LLM key beyond whatever Hindsight itself
needs — see docs/LIMITATIONS.md for why, and how to swap in a real LLM call.

After every triage, `record_feedback()` retains the analyst's verdict back
into memory (through the Memory Defense checkpoint), which is how the
system's judgment compounds over time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from . import memory_defense
from .hindsight_wrapper import MemoryBackend, MemoryResult


@dataclass
class Alert:
    alert_id: str
    kind: str              # e.g. "phishing_email", "network_ioc"
    summary: str            # short human-readable description
    indicators: dict[str, Any] = field(default_factory=dict)  # IPs, domains, sender, etc.
    raw: str = ""            # raw payload text (untrusted)


@dataclass
class Verdict:
    alert_id: str
    mode: str
    verdict: str             # "benign" | "suspicious" | "malicious"
    confidence: float
    rationale: str
    used_memories: list[MemoryResult] = field(default_factory=list)


# --- Default heuristic reasoner --------------------------------------------

_SUSPICIOUS_KEYWORDS = [
    "urgent", "verify your account", "wire transfer", "gift card",
    "password expires", "click here", "invoice overdue", "confirm your identity",
]

_MALICIOUS_INDICATOR_HINTS = [
    "known-bad", "blacklisted", "confirmed malicious", "c2", "malware",
]


def heuristic_reasoner(alert: Alert, memory_context: str) -> tuple[str, float, str]:
    """
    A transparent, fully offline stand-in for an LLM call. Returns
    (verdict, confidence, rationale). Swap this out for a real LLM by
    passing a different `reasoner` callable into SentinelAgent.
    """
    text = f"{alert.summary} {alert.raw}".lower()
    score = 0.15  # baseline suspicion

    hits = [kw for kw in _SUSPICIOUS_KEYWORDS if kw in text]
    score += 0.12 * len(hits)

    # Normalize hyphens to spaces so "confirmed-malicious" and
    # "confirmed malicious" both match the same keyword hint.
    mem_lower = memory_context.lower().replace("-", " ")
    memory_hits = [h for h in _MALICIOUS_INDICATOR_HINTS if h in mem_lower]
    memory_says_benign = "confirmed benign" in mem_lower or "false positive" in mem_lower

    rationale_parts = []
    if hits:
        rationale_parts.append(f"alert text matches known phishing patterns: {hits}")
    if memory_hits:
        score += 0.35
        rationale_parts.append(
            f"recalled memory links this indicator to prior confirmed incidents: {memory_hits}"
        )
    if memory_says_benign and not memory_hits:
        score -= 0.3
        rationale_parts.append(
            "recalled memory shows this pattern was previously confirmed as a false positive"
        )

    score = max(0.0, min(1.0, score))

    if score >= 0.6:
        verdict = "malicious"
    elif score >= 0.3:
        verdict = "suspicious"
    else:
        verdict = "benign"

    if not rationale_parts:
        rationale_parts.append("no strong indicators found in the alert text alone")

    rationale = "; ".join(rationale_parts)
    return verdict, round(score, 2), rationale


Reasoner = Callable[[Alert, str], tuple[str, float, str]]


class SentinelAgent:
    def __init__(
        self,
        backend: MemoryBackend,
        bank_id: str,
        reasoner: Reasoner = heuristic_reasoner,
        auto_retain_threshold: float = 0.6,
    ):
        self.backend = backend
        self.bank_id = bank_id
        self.reasoner = reasoner
        self.auto_retain_threshold = auto_retain_threshold

    def close(self) -> None:
        if hasattr(self.backend, "close"):
            self.backend.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # -- Triage --------------------------------------------------------

    def triage(self, alert: Alert, mode: str = "memory", top_k: int = 5) -> Verdict:
        assert mode in ("stateless", "memory")

        used_memories: list[MemoryResult] = []
        memory_context = ""

        if mode == "memory":
            query = self._build_recall_query(alert)
            raw_memories = self.backend.recall(self.bank_id, query, top_k=top_k)
            seen_texts = set()
            used_memories = []
            for m in raw_memories:
                cleaned = m.content.strip()
                if cleaned and cleaned not in seen_texts:
                    seen_texts.add(cleaned)
                    used_memories.append(m)
            memory_context = "\n".join(f"- {m.content}" for m in used_memories)

        verdict, confidence, rationale = self.reasoner(alert, memory_context)

        if mode == "memory" and used_memories:
            rationale += " [informed by recalled memory — see used_memories]"

        return Verdict(
            alert_id=alert.alert_id,
            mode=mode,
            verdict=verdict,
            confidence=confidence,
            rationale=rationale,
            used_memories=used_memories,
        )

    @staticmethod
    def _build_recall_query(alert: Alert) -> str:
        indicator_terms = " ".join(str(v) for v in alert.indicators.values())
        return f"{alert.kind} {alert.summary} {indicator_terms}".strip()

    # -- Feedback loop ---------------------------------------------------

    def record_feedback(
        self,
        alert: Alert,
        analyst_verdict: str,
        analyst_note: str,
        source: str = "analyst_verdict",
        known_conflicts: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Push an analyst's verdict back into memory, through the Memory
        Defense checkpoint. This is how the agent "learns" — a future
        recall of a similar alert will surface this note.
        """
        content = (
            f"Alert {alert.alert_id} ({alert.kind}, indicators={alert.indicators}): "
            f"analyst verdict = {analyst_verdict}. Note: {analyst_note}"
        )
        assessment = memory_defense.score(
            content=content,
            source=source,
            conflicting_high_confidence_beliefs=known_conflicts,
            auto_retain_threshold=self.auto_retain_threshold,
        )

        result: dict[str, Any] = {
            "assessment": assessment,
            "retained": False,
        }

        if assessment.decision == memory_defense.Decision.AUTO_RETAIN:
            self.backend.retain(
                bank_id=self.bank_id,
                content=content,
                context="analyst_feedback",
                metadata={
                    "alert_id": alert.alert_id,
                    "kind": alert.kind,
                    "source": source,
                    "trust_score": assessment.trust_score,
                },
            )
            result["retained"] = True
        # PENDING_APPROVAL and REJECTED are intentionally NOT retained here.
        # A real deployment would surface PENDING_APPROVAL items in an
        # analyst review queue; see docs/SECURITY.md.

        return result

    def retain_raw_alert_context(self, alert: Alert) -> dict[str, Any]:
        """
        Optionally retain a summary of the raw alert itself (e.g. for
        cross-incident correlation), subject to the same trust check —
        raw, unattributed alert payloads get a much lower base trust score
        than an authenticated analyst verdict (see memory_defense.SOURCE_TRUST).
        """
        content = f"Observed alert {alert.alert_id} ({alert.kind}): {alert.summary}"
        assessment = memory_defense.score(
            content=content,
            source="raw_alert_payload",
            auto_retain_threshold=self.auto_retain_threshold,
        )
        retained = False
        if assessment.decision == memory_defense.Decision.AUTO_RETAIN:
            self.backend.retain(
                bank_id=self.bank_id,
                content=content,
                context="observed_alert",
                metadata={"alert_id": alert.alert_id, "kind": alert.kind},
            )
            retained = True
        return {"assessment": assessment, "retained": retained}
