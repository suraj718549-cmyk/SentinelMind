# Demo Script

Run live with `python scripts/demo_60s.py` — this document is the narration to say out loud alongside it, plus what to do after the first 60 seconds.

## 0–10s — The problem

> "A SOC analyst sees hundreds of alerts a day. Most triage tools — including LLM-based ones — look at each alert completely in isolation. No memory of last week, or five minutes ago on a different machine."

## 10–20s — The stateless limitation

Run `ALERT-002`: a low-confidence outbound connection alert.

> "Stateless mode calls this benign, low confidence. Nothing wrong with that call in isolation — there's genuinely not much here on its own."

## 20–35s — Store a meaningful piece of information

Show the analyst confirming `ALERT-001` — a phishing email from the same IP — as malicious, and the Memory Defense log showing it was retained.

> "An analyst just confirmed a phishing email from this exact IP as malicious. That verdict is retained into Hindsight — auditable, trust-scored, not just appended to a log file."

## 35–50s — A new but related situation

Introduce `ALERT-005`: a new outbound connection alert, different workstation, same IP.

> "New alert, different machine, same underlying indicator. To a stateless system this looks exactly like ALERT-002 did — another isolated low-confidence blip."

## 50–60s — The recall moment

Run `ALERT-005` in memory mode.

> "With Hindsight, the agent recalls the confirmed incident and the verdict changes — from benign to suspicious — and it shows exactly which memory it used to get there. Not a bigger prompt. A remembered fact."

## After the 60 seconds — the deeper demo

1. **Run `scripts/evaluate.py` live.** Show the full stateless-vs-memory table and the accuracy numbers — emphasize every number is computed in that run, not pre-recorded.
2. **Show the poisoning attempt being blocked**, from the same evaluate.py output — this is usually the moment that gets a technical judge to lean in, because it shows the team thought about attacking their *own* system, not just building it.
3. **Open `frontend/index.html`** to walk through the same story visually, alert by alert, including the recalled-memory citation for each one.
4. **Close on the judge-question answers** in the README §6 — have them ready verbatim, don't improvise the "why Hindsight" answer live.

## What to have open before you start

- `scripts/demo_60s.py` output, or run it live if confident in timing
- `scripts/evaluate.py` output, ready to re-run live
- `frontend/index.html` open in a browser tab
- `docs/SECURITY.md` open, in case a judge asks the poisoning question in depth
