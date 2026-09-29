# Architecture

## Pipeline

```
Security Data (alerts, IOCs, analyst verdicts)
        │
        ▼
Memory Defense checkpoint  ──── trust score, injection screen, consistency check
        │  (auto-retain / pending approval / rejected)
        ▼
Hindsight.retain()  ──────────  world facts + experiences, entity/temporal graph
        │
        ▼
[ new alert arrives ]
        │
        ▼
Hindsight.recall()  ──────────  semantic + keyword + graph + temporal fusion
        │
        ▼
SentinelAgent reasoning  ─────  alert + recalled context → verdict + rationale
        │                       (heuristic, or live LLM via src/llm_reasoner.py)
        │
        ▼
Verdict + cited memory  ──────  shown to analyst with *why*, not just *what*
        │
        ▼
Analyst feedback  ────────────  fed back through Memory Defense → retain()
        │
        └──────────────────────► loop closes: future recall reflects this incident
```

This is deliberately not "LLM + Hindsight API" bolted together. Two properties only exist because of that loop:

1. **A verdict can change without any code change or retraining** — `ALERT-005` and `ALERT-002` in the demo data get different verdicts purely because one analyst confirmation was retained in between. See `scripts/demo_60s.py`.
2. **Every verdict is traceable to specific evidence** — `Verdict.used_memories` in `src/agent.py` is never a black box; it's the exact recalled records that fed the reasoning step.

## Why Hindsight specifically (not a vector DB)

| Need | Plain vector DB | Hindsight |
|---|---|---|
| "Have we seen this IP before?" | ✅ similarity search | ✅ |
| "What changed about this IP in the last 30 days?" | ❌ no temporal reasoning | ✅ temporal retrieval strategy |
| Analyst corrects a verdict | New chunk added; old one still equally "true" | Observation is *refined*, not overwritten — confidence shifts, evidence trail kept |
| "Why did the agent believe that?" | Cosine similarity score only | Observations keep supporting evidence + a proof count |
| Isolate one team's/project's memory | Manual filtering by metadata | Native memory banks, strict isolation |
| Screen retained content for secrets/PII | Nothing built in | Opt-in Memory Defense on every retain (45 patterns) — complementary to, and a precedent for, the security-specific defense layer we built on top in `src/memory_defense.py` |

SentinelMind's own `src/memory_defense.py` is a security-domain-specific layer on top of this — Hindsight's built-in Memory Defense screens for secrets/PII; ours screens for prompt-injection-style content and belief-contradiction in security telemetry specifically. They solve adjacent but different problems.

## Component map

- **`src/hindsight_wrapper.py`** — the only place that talks to Hindsight. Exposes `retain` / `recall` / `reflect` with an identical local fallback so the rest of the system is backend-agnostic. See its docstring for why the fallback exists.
- **`src/memory_defense.py`** — trust scoring and decision logic run on every candidate memory *before* `retain()` is called. See `docs/SECURITY.md`.
- **`src/agent.py`** — `SentinelAgent`, the only component that decides *whether* to recall, *what* to retain, and how much a recalled memory should move a verdict. The reasoning step is a pluggable `Reasoner` callable — `heuristic_reasoner` (default, deterministic, offline) or `src/llm_reasoner.py`'s live-LLM reasoner, selected automatically at startup.
- **`src/llm_reasoner.py`** — builds a real Anthropic/OpenAI-backed `Reasoner` from `.env` (`LLM_PROVIDER` / `LLM_API_KEY` / `LLM_MODEL`), with strict JSON-contract parsing, a prompt-injection-hardened system prompt (second defense layer — see `docs/SECURITY.md`), and a safe fallback to `heuristic_reasoner` on any failure. `get_reasoner()` mirrors `hindsight_wrapper.get_backend()`'s auto-selection pattern so both scripts stay a one-line call regardless of what's configured.
- **`scripts/demo_60s.py` / `scripts/evaluate.py`** — the demo narrative and the reproducible stateless-vs-memory comparison, respectively.
- **`frontend/index.html`** — a static dashboard view of real evaluation output; see the file's footer note for how to wire it to a live backend.

## Where the agent actually makes decisions

This matters for the "why is this an agent, not a script" question:

1. **Recall or not** — `SentinelAgent.triage(mode=...)` decides whether the current alert warrants a memory lookup at all (a script would always or never look up).
2. **What to retain** — `record_feedback()` and `retain_raw_alert_context()` route every candidate memory through `memory_defense.score()`, which can auto-retain, hold for human approval, or reject outright — three distinct actions, not one fixed write path.
3. **How much a memory should matter** — the reasoning step treats recalled content as evidence to weigh (see `_MALICIOUS_INDICATOR_HINTS` handling in `src/agent.py`), not as ground truth to copy.

## Scaling considerations

At alert volumes beyond a single-team demo, the two things that need real design work (not present in this prototype, called out honestly in `docs/LIMITATIONS.md`):

- **Bank strategy**: one bank per team/tenant is the natural isolation boundary (see Hindsight's memory banks); a single global bank would leak cross-team context and make Memory Defense harder to reason about.
- **Retain volume control**: not every alert should become a permanent memory. A production version needs a retention policy (e.g. only retain analyst-confirmed verdicts and consolidated observations, not every raw alert) to avoid flooding the bank with noise — this is exactly why `retain_raw_alert_context()` is a separate, lower-trust path from `record_feedback()` in this repo.
