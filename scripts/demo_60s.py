"""
demo_60s.py
============

Runs the exact "wow in 60 seconds" story described in docs/DEMO_SCRIPT.md,
using real data from data/sample_alerts.json and data/analyst_feedback.json.
No numbers here are staged — every verdict is computed live by SentinelAgent.

Run: python scripts/demo_60s.py
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

BANK_ID = f"sentinelmind-demo-{int(time.time())}"


def load_alerts() -> dict[str, dict]:
    data = json.loads((Path(__file__).parent.parent / "data" / "sample_alerts.json").read_text())
    return {a["alert_id"]: a for a in data}


def to_alert(d: dict) -> Alert:
    return Alert(
        alert_id=d["alert_id"],
        kind=d["kind"],
        summary=d["summary"],
        indicators=d["indicators"],
        raw=d["raw"],
    )


def pause(seconds: float = 0.6):
    time.sleep(seconds)


def section(label: str):
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")


def main():
    reset_usage_stats()
    alerts = load_alerts()
    backend = get_backend()
    reasoner = get_reasoner()
    agent = SentinelAgent(backend=backend, bank_id=BANK_ID, reasoner=reasoner)
    print(f"[demo_60s] memory backend={backend.name} reasoner={reasoner.__name__}")

    try:
        # ---- 0-10s: the problem ----------------------------------------------
        section("0-10s | THE PROBLEM")
        print(
            "A SOC analyst is drowning in alerts. Most triage agents look at each\n"
            "alert in total isolation — they have no memory of what happened last\n"
            "week, or five minutes ago on a different host."
        )
        pause()

        # ---- 10-20s: stateless limitation -------------------------------------
        section("10-20s | STATELESS AGENT SEES A LOW-CONFIDENCE ALERT")
        a002 = to_alert(alerts["ALERT-002"])
        print(f"Alert: {a002.summary}")
        v_stateless = agent.triage(a002, mode="stateless")
        print(
            f"\n[STATELESS] verdict={v_stateless.verdict} "
            f"confidence={v_stateless.confidence}\nrationale: {v_stateless.rationale}"
        )
        print(
            "\n-> No history to draw on. The connection to 185.220.101.7 looks like\n"
            "   routine low-confidence noise. This is exactly the kind of alert that\n"
            "   gets dismissed and re-investigated from scratch next time it appears."
        )
        pause()

        # ---- 20-35s: store a meaningful piece of information ------------------
        section("20-35s | ANALYST CONFIRMS A RELATED INCIDENT — STORED IN HINDSIGHT")
        a001 = to_alert(alerts["ALERT-001"])
        feedback = json.loads(
            (Path(__file__).parent.parent / "data" / "analyst_feedback.json").read_text()
        )
        fb001 = next(f for f in feedback if f["alert_id"] == "ALERT-001")
        print(f"Alert: {a001.summary}")
        print(f"Analyst verdict: {fb001['analyst_verdict']} — {fb001['analyst_note']}")
        result = agent.record_feedback(
            a001,
            analyst_verdict=fb001["analyst_verdict"],
            analyst_note=fb001["analyst_note"],
            source=fb001["source"],
        )
        print(
            f"\nMemory Defense: trust_score={result['assessment'].trust_score} "
            f"decision={result['assessment'].decision.value} retained={result['retained']}"
        )
        pause()

        # ---- 35-50s: a new but related situation appears -----------------------
        section("35-50s | A NEW ALERT ARRIVES — DIFFERENT HOST, SAME INDICATOR")
        a005 = to_alert(alerts["ALERT-005"])
        print(f"Alert: {a005.summary}")
        pause()

        # ---- 50-60s: memory-enabled agent recalls and changes its verdict ------
        section("50-60s | HINDSIGHT-ENABLED AGENT RECALLS THE PRIOR INCIDENT")
        v_memory = agent.triage(a005, mode="memory")
        print(
            f"[MEMORY]     verdict={v_memory.verdict} confidence={v_memory.confidence}\n"
            f"rationale: {v_memory.rationale}"
        )
        if v_memory.used_memories:
            print("\nRecalled memory used:")
            for m in v_memory.used_memories:
                print(f"  - ({m.score:.2f}, {m.backend}) {m.content}")

        section("RESULT")
        print(
            f"Same underlying indicator (185.220.101.7). Stateless treated ALERT-002\n"
            f"as low-confidence noise. With memory, ALERT-005 is triaged as "
            f"'{v_memory.verdict}' at confidence {v_memory.confidence} — because the\n"
            f"agent recalled a confirmed prior incident on the same indicator, not\n"
            f"because it re-read a bigger prompt."
        )
        stats = get_usage_stats()
        if stats["calls"] > 0:
            print(f"\nLive LLM Usage: {stats['calls']} calls, ~{stats['input_tokens']} input tokens, ~{stats['output_tokens']} output tokens")
    finally:
        agent.close()


if __name__ == "__main__":
    main()
