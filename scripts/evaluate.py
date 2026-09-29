"""
evaluate.py
============

Reproducible comparison of stateless vs Hindsight-enabled triage over the
sample alert set in data/sample_alerts.json.

Every number this script prints is computed live in this run — nothing is
hardcoded or pre-recorded. See docs/EVALUATION.md for what each metric means
and its limitations (small sample size, heuristic reasoner).

Run: python scripts/evaluate.py
"""

import json
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent import Alert, SentinelAgent
from src.hindsight_wrapper import get_backend
from src.llm_reasoner import get_reasoner, get_usage_stats, reset_usage_stats
import src.memory_defense as memory_defense

try:
    from tabulate import tabulate
except ImportError:  # pragma: no cover
    def tabulate(rows, headers):
        widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
        out = [" | ".join(str(h).ljust(w) for h, w in zip(headers, widths))]
        for r in rows:
            out.append(" | ".join(str(c).ljust(w) for c, w in zip(r, widths)))
        return "\n".join(out)

BANK_ID = f"sentinelmind-eval-{int(time.time())}"

# Ground truth known only for alerts an analyst has actually confirmed.
# Everything else is "unknown" and excluded from accuracy scoring — we do
# not invent ground truth for alerts nobody has verified.
GROUND_TRUTH = {
    "ALERT-001": "malicious",
    "ALERT-004": "benign",
}


def load_json(name):
    return json.loads((Path(__file__).parent.parent / "data" / name).read_text())


def run_mode(agent, alerts, mode):
    rows = []
    for a in alerts:
        alert = Alert(a["alert_id"], a["kind"], a["summary"], a["indicators"], a["raw"])
        start = time.perf_counter()
        v = agent.triage(alert, mode=mode)
        elapsed_ms = (time.perf_counter() - start) * 1000
        rows.append(
            {
                "alert_id": a["alert_id"],
                "verdict": v.verdict,
                "confidence": v.confidence,
                "rationale": v.rationale,
                "n_memories": len(v.used_memories),
                "elapsed_ms": round(elapsed_ms, 2),
            }
        )
        time.sleep(0.5)
    return rows


def main():
    reset_usage_stats()
    alerts = load_json("sample_alerts.json")
    feedback = load_json("analyst_feedback.json")
    backend = get_backend()
    reasoner = get_reasoner()
    agent = SentinelAgent(backend=backend, bank_id=BANK_ID, reasoner=reasoner)

    print(f"Memory backend in use for this evaluation run: {backend.name}")
    print(f"Reasoner in use for this evaluation run: {reasoner.__name__}\n")

    try:
        # --- Phase 1: stateless baseline, before any feedback is retained -----
        stateless_rows = run_mode(agent, alerts, "stateless")

        # --- Phase 2: retain confirmed analyst feedback through Memory Defense -
        print("Retaining analyst feedback (via Memory Defense checkpoint):")
        for fb in feedback:
            target_id = fb["alert_id"].split("-poison")[0]
            matching = next((a for a in alerts if a["alert_id"] == target_id), None)
            if matching is None:
                continue
            alert = Alert(matching["alert_id"], matching["kind"], matching["summary"],
                           matching["indicators"], matching["raw"])
            result = agent.record_feedback(
                alert,
                analyst_verdict=fb["analyst_verdict"],
                analyst_note=fb["analyst_note"],
                source=fb["source"],
            )
            tag = "POISON ATTEMPT" if "poison" in fb["alert_id"] else "legitimate"
            print(
                f"  [{tag}] {fb['alert_id']}: trust={result['assessment'].trust_score} "
                f"decision={result['assessment'].decision.value} retained={result['retained']}"
            )
            if tag == "POISON ATTEMPT":
                assert not result["retained"], "Memory Defense FAILED to block a poisoning attempt!"
                print("      -> confirmed: poisoning attempt was NOT retained.")
            time.sleep(1.0)

        # --- Phase 3: memory-enabled run, after feedback is in memory ----------
        memory_rows = run_mode(agent, alerts, "memory")

        # --- Metrics -------------------------------------------------------------
        print("\nPer-alert comparison:")
        combined = []
        for s, m in zip(stateless_rows, memory_rows):
            changed = "YES" if s["verdict"] != m["verdict"] else "no"
            combined.append([
                s["alert_id"], s["verdict"], m["verdict"], changed,
                m["n_memories"], s["elapsed_ms"], m["elapsed_ms"],
            ])
        print(tabulate(
            combined,
            headers=["alert_id", "stateless_verdict", "memory_verdict", "verdict_changed",
                      "memories_recalled", "stateless_ms", "memory_ms"],
        ))

        def accuracy(rows):
            scored = [r for r in rows if r["alert_id"] in GROUND_TRUTH]
            if not scored:
                return None
            correct = sum(1 for r in scored if r["verdict"] == GROUND_TRUTH[r["alert_id"]])
            return correct / len(scored), len(scored)

        stateless_acc = accuracy(stateless_rows)
        memory_acc = accuracy(memory_rows)
        useful_recall_rate = sum(1 for r in memory_rows if r["n_memories"] > 0) / len(memory_rows)
        verdict_change_rate = sum(
            1 for s, m in zip(stateless_rows, memory_rows) if s["verdict"] != m["verdict"]
        ) / len(stateless_rows)

        print("\nSummary metrics (computed from this run, on the sample set only):")
        if stateless_acc:
            print(f"  Stateless accuracy on {stateless_acc[1]} ground-truthed alerts: {stateless_acc[0]:.0%}")
        if memory_acc:
            print(f"  Memory-enabled accuracy on {memory_acc[1]} ground-truthed alerts: {memory_acc[0]:.0%}")
        print(f"  Useful recall rate (alerts where >=1 memory was recalled): {useful_recall_rate:.0%}")
        print(f"  Verdict-change rate (stateless vs memory disagree): {verdict_change_rate:.0%}")

        stats = get_usage_stats()
        if stats["calls"] > 0:
            print(f"\nLive LLM Usage: {stats['calls']} calls, ~{stats['input_tokens']} input tokens, ~{stats['output_tokens']} output tokens")
        print(
            "\nNote: this sample set has 5 alerts and 2 ground-truthed outcomes — "
            "these numbers demonstrate the evaluation methodology, not a claim "
            "of general accuracy. See docs/EVALUATION.md and docs/LIMITATIONS.md."
        )
    finally:
        agent.close()


if __name__ == "__main__":
    main()
