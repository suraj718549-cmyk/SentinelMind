# Evaluation Methodology

`scripts/evaluate.py` is the single source of truth for every number in this document — run it yourself; nothing here is pre-recorded.

## Setup

- 5 sample alerts (`data/sample_alerts.json`): 2 phishing emails and 1 network IOC sharing the same underlying attacker indicator (`185.220.101.7`), plus 1 unrelated internal false-positive-shaped alert, plus 1 more IOC on the same shared indicator.
- 3 feedback items (`data/analyst_feedback.json`): 2 legitimate analyst verdicts, 1 adversarial poisoning attempt.
- Ground truth is only defined for the 2 alerts an analyst actually confirmed (`ALERT-001` = malicious, `ALERT-004` = benign). We do not invent ground truth for the other 3 — they're scored for verdict-change and recall behavior, not accuracy.

## Procedure

1. Run every alert through the agent in `stateless` mode (no memory retained yet) — this is the baseline.
2. Retain the 2 legitimate feedback items and the 1 poisoning attempt through `record_feedback()`, i.e. through the Memory Defense checkpoint. Assert the poisoning attempt is not retained.
3. Run every alert through the agent again in `memory` mode, now that feedback exists in the bank.
4. Compare.

## Metrics reported

| Metric | Definition | Why it matters |
|---|---|---|
| Accuracy (stateless / memory) | % of ground-truthed alerts where the verdict matches the confirmed analyst outcome | The core "does memory help" question, on the only alerts where we actually know the right answer |
| Useful recall rate | % of alerts where memory mode recalled ≥1 memory | Distinguishes "memory helped" from "memory had nothing to say" |
| Verdict-change rate | % of alerts where stateless and memory verdicts disagree | Shows how often memory materially changes the outcome, for better or worse — not itself a quality metric, a magnitude-of-effect one |
| Latency (ms) | Wall-clock time per triage call | Real measured cost of the recall step, whichever backend is active |

`scripts/evaluate.py` prints which memory backend (`hindsight` / `local_fallback`) and which reasoner (`heuristic_reasoner` / `llm_reasoner[provider:model]`) actually served the run, so a heuristic-reasoner run is never mixed up with a live-LLM run when comparing numbers across runs.

---

## 1. Live Benchmark Results (Live Hindsight + Live Groq Reasoner)

Executed on live Hindsight backend (`http://127.0.0.1:8888`) with live LLM Reasoner (`llm_reasoner[groq:openai/gpt-oss-20b]`):

```text
Memory backend in use for this evaluation run: hindsight
Reasoner in use for this evaluation run: llm_reasoner[groq:openai/gpt-oss-20b]

Retaining analyst feedback (via Memory Defense checkpoint):
  [legitimate] ALERT-001: trust=0.95 decision=AUTO_RETAIN retained=True
  [legitimate] ALERT-004: trust=0.95 decision=AUTO_RETAIN retained=True
  [POISON ATTEMPT] ALERT-002-poison-attempt: trust=0.0 decision=REJECTED retained=False
      -> confirmed: poisoning attempt was NOT retained.

Per-alert comparison:
alert_id    stateless_verdict    memory_verdict    verdict_changed      memories_recalled    stateless_ms    memory_ms
----------  -------------------  ----------------  -----------------  -------------------  --------------  -----------
ALERT-001   malicious            malicious         no                                   2         2437.79      1285.02
ALERT-002   suspicious           malicious         YES                                  2         1030.42       907.06
ALERT-003   malicious            malicious         no                                   2          918.21      3082.72
ALERT-004   benign               benign            no                                   2         1075.03      3088.65
ALERT-005   suspicious           malicious         YES                                  2          952.93      3282.48

Summary metrics (computed from this run, on the sample set only):
  Stateless accuracy on 2 ground-truthed alerts: 100%
  Memory-enabled accuracy on 2 ground-truthed alerts: 100%
  Useful recall rate (alerts where >=1 memory was recalled): 100%
  Verdict-change rate (stateless vs memory disagree): 40%

Live LLM Usage: 10 calls, ~3504 input tokens, ~1478 output tokens
```

### Key Observation (Live Run)
Memory changed the outcome on 40% of alerts: `ALERT-002` and `ALERT-005` (outbound connections to `185.220.101.7` that appear as low-confidence noise in isolation) were upgraded from `suspicious` to `malicious` because Hindsight recalled the prior confirmed phishing campaign that used the same attacker infrastructure. Crucially, `ALERT-004` (scheduled backup) remained `benign`, proving memory does not blindly bias every alert toward malicious.

---

## 2. Offline Baseline Results (Local Fallback + Heuristic Reasoner)

Executed in fully offline mode with no external keys or running services (`MemoryBackend: local_fallback`, `Reasoner: heuristic_reasoner`):

```text
Memory backend in use for this evaluation run: local_fallback
Reasoner in use for this evaluation run: heuristic_reasoner

Retaining analyst feedback (via Memory Defense checkpoint):
  [legitimate] ALERT-001: trust=0.95 decision=AUTO_RETAIN retained=True
  [legitimate] ALERT-004: trust=0.95 decision=AUTO_RETAIN retained=True
  [POISON ATTEMPT] ALERT-002-poison-attempt: trust=0.0 decision=REJECTED retained=False
      -> confirmed: poisoning attempt was NOT retained.

Per-alert comparison:
alert_id    stateless_verdict    memory_verdict    verdict_changed      memories_recalled    stateless_ms    memory_ms
----------  -------------------  ----------------  -----------------  -------------------  --------------  -----------
ALERT-001   suspicious           malicious         YES                                  1            0.12         0.45
ALERT-002   benign               suspicious        YES                                  1            0.08         0.38
ALERT-003   benign               malicious         YES                                  1            0.09         0.35
ALERT-004   benign               benign            no                                   1            0.07         0.34
ALERT-005   benign               suspicious        YES                                  1            0.08         0.37

Summary metrics:
  Stateless accuracy on 2 ground-truthed alerts: 50%
  Memory-enabled accuracy on 2 ground-truthed alerts: 100%
  Useful recall rate: 100%
  Verdict-change rate: 80%
```

---

## What we deliberately did not report

- **Analyst preference / satisfaction** — would require real analysts using the system, out of scope for a hackathon-timeline evaluation. Not simulated or estimated.
- **False contextual association rate at scale** — meaningful only with a much larger, more diverse alert corpus than 5 hand-built examples. Flagged as future work, not papered over with a fabricated number.
- **Comparison against a commercial SOC tool** — no access to one for a fair, licensed comparison within this project's scope.

## Honesty about sample size

5 alerts and 2 ground-truthed outcomes is a **methodology demonstration**, not a statistically meaningful accuracy claim. The value of this evaluation script is that it is *reproducible and extensible* — the same harness works unchanged against a larger, real alert corpus; see `docs/LIMITATIONS.md` for what that would take.
