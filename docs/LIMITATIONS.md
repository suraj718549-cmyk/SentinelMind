# Limitations

Stated plainly, per the project's own standard of preferring a clear limitation over fake perfection.

## Reasoning engine

`src/agent.py` defaults to a deterministic, keyword/rule-based `heuristic_reasoner` when no provider is configured — every demo and test run is fully reproducible with zero external API dependency beyond Hindsight itself, and no judge has to hand us an API key to see the system work end to end. `src/llm_reasoner.py` is the real-LLM path: set `LLM_PROVIDER` + `LLM_API_KEY` in `.env` (Groq, Anthropic, or OpenAI) and `scripts/demo_60s.py` / `scripts/evaluate.py` automatically switch to a live model call for reasoning, with the recalled memory injected into its prompt.

- **Sample evaluation completed live**: `scripts/evaluate.py` has now been verified live against real Hindsight and live LLM reasoning (Groq `openai/gpt-oss-20b`), achieving 100% accuracy on confirmed ground truth and a 40% verdict-change rate (upgrading low-confidence noise to malicious when correlated with prior attacker infrastructure).
- **Single call, no self-consistency / voting**: One model call per alert, no majority-vote-of-N or a second pass to catch a bad first answer.
- **Prompt-injection hardening is instruction-based, not architecturally enforced**: The system prompt tells the model to treat alert/memory text as data, not commands (see `docs/SECURITY.md`) — this measurably reduces but does not provably eliminate susceptibility to a sufficiently clever injection, the same caveat that applies to any LLM-in-the-loop system today.
- **Rate limiting / throughput**: On free-tier API accounts, tight token-per-minute (TPM) limits require backoff retries and compact prompt packing. With `max_tokens=250` and concise prompts, the full evaluation runs within ~4,900 tokens total.

## Local fallback memory store

When no Hindsight server is reachable, `src/hindsight_wrapper.LocalFallbackStore` provides a same-interface, file-backed stand-in using keyword-overlap scoring with a hand-tuned bonus for shared concrete indicators (IPs/domains). This is **not** a reimplementation of Hindsight's semantic + keyword + graph + temporal fusion and cross-encoder reranking — it exists purely so this repo is demoable and testable offline. Every recall result carries a `backend` field precisely so results from the two paths are never silently conflated; `scripts/evaluate.py` prints which backend served each run. The primary live backend is the real Hindsight server (`hindsight-api`).

## Sample size

5 sample alerts, 2 with confirmed ground truth. `docs/EVALUATION.md` is explicit that this validates the evaluation *methodology*, not a general accuracy claim. A real deployment evaluation would need at minimum dozens of confirmed incidents across multiple alert types.

## Memory Defense scope

`src/memory_defense.py`'s `INJECTION_PATTERNS` list is illustrative (a handful of representative phrasings), not exhaustive. It will not catch a sufficiently novel injection phrasing. `docs/SECURITY.md` names this explicitly. In live verification, it successfully blocked adversarial poisoning (`REJECTED`) while allowing verified analyst inputs (`AUTO_RETAIN`). Production-grade defense would add an LLM-based classifier pass and an ongoing red-team test suite, not just pattern matching.

## Conflict detection is not automatic yet

`memory_defense.score()` accepts `conflicting_high_confidence_beliefs` as a parameter, but nothing in this repo automatically calls `recall()` to find those conflicts before scoring new content — the caller currently has to supply them. Wiring this up (recall relevant prior beliefs → pass them into the trust check automatically) is a natural and fairly small next step, not a redesign.

## No load / scale testing

No testing has been done at realistic SOC alert volumes (thousands/day). `docs/ARCHITECTURE.md` names the two design questions that would need real answers at that scale: bank partitioning strategy and a retention policy to avoid flooding the bank with low-value raw alert memories.

## Web Dashboard & Live API

The frontend dashboard (`frontend/index.html`) is connected directly to the live backend API via `scripts/web_server.py` at `http://localhost:3000`, enabling interactive live triage and real-time Memory Defense testing against the running Hindsight bank and Groq LLM reasoner. A static fallback view is also preserved if the backend is not running.

## Single-language, single-domain

Alerts in this repo are English-language phishing/network examples. Hindsight itself is multilingual by design; the heuristic reasoner and keyword-based defenses are English-only by construction. The live LLM reasoner (see "Reasoning engine" above) is not restricted to English, but this has not been tested against non-English alert text.
