# Memory Flow

## What enters memory, and why

Only two kinds of content are ever retained, and each has its own trust ceiling in `src/memory_defense.SOURCE_TRUST`:

| Content | Source tag | Base trust | Retained via |
|---|---|---|---|
| A confirmed analyst verdict on an alert | `analyst_verdict` | 0.95 | `SentinelAgent.record_feedback()` |
| A summary of an observed alert (for cross-incident correlation, not yet analyst-confirmed) | `raw_alert_payload` | 0.45 | `SentinelAgent.retain_raw_alert_context()` |

Raw log/email bodies are never retained verbatim into long-term memory — only a short structured summary (alert id, kind, indicators, and either the analyst's note or the alert's own summary text). This limits how much attacker-controlled free text ever reaches the memory layer at all, which is the first line of defense described in `docs/SECURITY.md`.

## When memory is recalled

`SentinelAgent.triage(mode="memory")` builds a recall query from the alert's kind, summary, and indicator values (`_build_recall_query` in `src/agent.py`), then calls `recall()` before reasoning. Relevance is determined by:

- **With a real Hindsight server**: Hindsight's own fused semantic + keyword + graph + temporal retrieval, reranked by a cross-encoder.
- **With the local fallback** (no server reachable — see `docs/LIMITATIONS.md`): keyword overlap weighted toward shared concrete indicators (an IP or domain-like token shared between the query and a stored record counts far more than a shared common word) — a deliberately simple approximation of what Hindsight's entity/graph linking does for real.

## What happens when memory is wrong or stale

Hindsight's observation model *refines* beliefs rather than silently replacing them — new evidence strengthens, weakens, or extends an existing belief, and the earlier evidence isn't discarded. SentinelMind's own defense layer adds a narrower, security-specific version of the same idea: `memory_defense.score()` accepts a list of `conflicting_high_confidence_beliefs` and applies a trust penalty when new content contradicts one, forcing a human decision instead of a silent overwrite (see `test_contradicting_high_confidence_belief_is_downgraded` in `tests/test_memory_defense.py`).

In this prototype, the caller is responsible for supplying known conflicts (a production version would look this up automatically via `recall()` before scoring a new item — noted as future work in `docs/LIMITATIONS.md`).

## Conflict example, end to end

1. Analyst confirms `IP 1.2.3.4 confirmed malicious, high confidence` → retained, high trust.
2. Two weeks later, a new event claims `IP 1.2.3.4 is benign` from a lower-trust source (e.g. `internal_siem`).
3. `memory_defense.score()` is called with the prior belief passed as a known conflict → trust score is reduced → decision becomes `PENDING_APPROVAL` instead of `AUTO_RETAIN`.
4. The contradiction is queued for a human to resolve, rather than either being silently ignored or silently overwriting the earlier confirmed verdict.

This is exactly the pattern exercised in `tests/test_memory_defense.py::test_contradicting_high_confidence_belief_is_downgraded`.
