"""
hindsight_wrapper.py
=====================

A thin, honest abstraction over Hindsight's `retain` / `recall` / `reflect`
operations (see https://github.com/vectorize-io/hindsight).

Design goal: SentinelMind's agent code (`src/agent.py`) should never know or
care whether it's talking to a real Hindsight server or the local fallback
store below. Both implement the same three methods with the same signatures.

Why a fallback exists at all
-----------------------------
This repo needs to be demoable and testable without requiring every judge /
reviewer to first stand up a Hindsight server and an LLM API key. The
`LocalFallbackStore` below is a deliberately simple, fully-offline stand-in:
keyword + recency scoring instead of Hindsight's semantic/keyword/graph/
temporal fusion and reranking. It exists for development and demo
convenience ONLY. It is not a reimplementation of Hindsight and is not
claimed to have comparable recall quality — see docs/LIMITATIONS.md.

Every call logs which backend actually handled it (`backend="hindsight"` or
`backend="local_fallback"`) so evaluation results are never silently mixed.
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class MemoryResult:
    """A single recalled memory, normalized across backends."""

    content: str
    score: float
    timestamp: str | None
    metadata: dict[str, Any] = field(default_factory=dict)
    backend: str = "unknown"


class MemoryBackend:
    """Interface both backends implement."""

    name: str = "base"

    def retain(
        self,
        bank_id: str,
        content: str,
        context: str | None = None,
        timestamp: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def recall(self, bank_id: str, query: str, top_k: int = 5) -> list[MemoryResult]:
        raise NotImplementedError

    def reflect(self, bank_id: str, query: str) -> str:
        raise NotImplementedError

    def close(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class HindsightBackend(MemoryBackend):
    """Wraps the real `hindsight_client.Hindsight` client."""

    name = "hindsight"

    def __init__(self, base_url: str):
        from hindsight_client import Hindsight  # imported lazily; optional dep

        self._client = Hindsight(base_url=base_url)

    def retain(self, bank_id, content, context=None, timestamp=None, metadata=None, max_retries: int = 3):
        clean_meta = {k: str(v) for k, v in metadata.items()} if metadata else None
        for attempt in range(max_retries + 1):
            try:
                return self._client.retain(
                    bank_id=bank_id,
                    content=content,
                    context=context,
                    metadata=clean_meta,
                )
            except Exception as exc:
                err_str = str(exc)
                if ("429" in err_str or "quota" in err_str.lower() or "rate limit" in err_str.lower() or "exhausted" in err_str.lower()) and attempt < max_retries:
                    delay_match = re.search(r"try again in ([\d\.]+)s", err_str.lower())
                    backoff = float(delay_match.group(1)) + 0.5 if delay_match else 1.5 * (attempt + 1)
                    time.sleep(backoff)
                    continue
                raise

    def recall(self, bank_id, query, top_k=5, max_retries: int = 3) -> list[MemoryResult]:
        raw = None
        for attempt in range(max_retries + 1):
            try:
                raw = self._client.recall(bank_id=bank_id, query=query)
                break
            except Exception as exc:
                err_str = str(exc)
                if "404" in err_str or "not found" in err_str.lower():
                    return []
                if ("429" in err_str or "quota" in err_str.lower() or "rate limit" in err_str.lower() or "exhausted" in err_str.lower()) and attempt < max_retries:
                    delay_match = re.search(r"try again in ([\d\.]+)s", err_str.lower())
                    backoff = float(delay_match.group(1)) + 0.5 if delay_match else 1.5 * (attempt + 1)
                    time.sleep(backoff)
                    continue
                raise
        results = []
        items = getattr(raw, "results", None)
        if items is None:
            items = raw if isinstance(raw, list) else raw.get("results", []) if isinstance(raw, dict) else []
        for item in items:
            if hasattr(item, "text"):
                content = item.text or ""
                scores_obj = getattr(item, "scores", None)
                score = getattr(scores_obj, "overall", 0.0) if scores_obj else 0.0
                ts = getattr(item, "occurred_start", None) or getattr(item, "mentioned_at", None)
                meta = getattr(item, "metadata", {}) or {}
            elif isinstance(item, dict):
                content = item.get("text") or item.get("content", "")
                score_val = item.get("scores") or item.get("score", 0.0)
                score = score_val.get("overall", 0.0) if isinstance(score_val, dict) else float(score_val or 0.0)
                ts = item.get("occurred_start") or item.get("mentioned_at") or item.get("timestamp")
                meta = item.get("metadata", {}) or {}
            else:
                d = vars(item)
                content = d.get("text") or d.get("content", "")
                score = float(d.get("score", 0.0))
                ts = d.get("timestamp")
                meta = d.get("metadata", {}) or {}

            results.append(
                MemoryResult(
                    content=content,
                    score=float(score) if score is not None else 0.0,
                    timestamp=str(ts) if ts else None,
                    metadata=meta,
                    backend=self.name,
                )
            )
        return results[:top_k]

    def reflect(self, bank_id, query, max_retries: int = 3) -> str:
        for attempt in range(max_retries + 1):
            try:
                raw = self._client.reflect(bank_id=bank_id, query=query)
                if hasattr(raw, "text"):
                    return raw.text or ""
                if isinstance(raw, dict):
                    return raw.get("text") or raw.get("answer") or raw.get("content") or json.dumps(raw)
                return str(raw)
            except Exception as exc:
                err_str = str(exc)
                if ("429" in err_str or "quota" in err_str.lower() or "rate limit" in err_str.lower()) and attempt < max_retries:
                    backoff = 3.0 * (attempt + 1)
                    time.sleep(backoff)
                    continue
                return f"Error reflecting memory: {exc}"

    def close(self) -> None:
        """Close the underlying Hindsight API client and its aiohttp session."""
        if hasattr(self, "_client") and self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass

    def __del__(self) -> None:
        self.close()


class LocalFallbackStore(MemoryBackend):
    """
    Fully offline, file-backed stand-in for Hindsight.

    Storage: one JSON-lines file per bank under `.local_memory/<bank_id>.jsonl`.
    Recall: simple weighted keyword-overlap + recency score. This is
    intentionally simple — it exists so the rest of the system (agent logic,
    memory defense, evaluation harness) can be built and demoed without a
    live server, not to approximate Hindsight's retrieval quality.
    """

    name = "local_fallback"

    def __init__(self, storage_dir: str = ".local_memory"):
        self._dir = Path(storage_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, bank_id: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]", "_", bank_id)
        return self._dir / f"{safe}.jsonl"

    def retain(self, bank_id, content, context=None, timestamp=None, metadata=None):
        record = {
            "id": str(uuid.uuid4()),
            "content": content,
            "context": context,
            "timestamp": timestamp or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "metadata": metadata or {},
            "retained_at": time.time(),
        }
        with open(self._path(bank_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return {"status": "retained", "id": record["id"], "backend": self.name}

    def _load(self, bank_id: str) -> list[dict]:
        path = self._path(bank_id)
        if not path.exists():
            return []
        records = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    records.append(json.loads(line))
        return records

    # Deliberately small stopword list: this is a keyword-overlap fallback,
    # not a real retrieval model, so common words (including domain-generic
    # ones like "alert"/"analyst" that appear in every retained record) are
    # excluded to avoid matching every record against every query.
    _STOPWORDS = {
        "a", "an", "the", "is", "are", "was", "were", "to", "of", "in", "on",
        "for", "and", "or", "this", "that", "it", "at", "by", "from", "with",
        "alert", "analyst", "verdict", "note", "indicators", "id",
    }

    _MIN_RELEVANCE = 0.15  # below this, a match is noise, not a real recall

    # A shared token that looks like a concrete indicator (an IP address or a
    # dotted domain-like string) is worth far more than a shared generic
    # word — this is the fallback's crude stand-in for Hindsight's real
    # entity/graph-linked recall, where "same IOC" drives retrieval even
    # when the surrounding text is otherwise unrelated.
    _INDICATOR_RE = re.compile(r"^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}|[a-z0-9-]+\.[a-z]{2,})$")

    @classmethod
    def _tokenize(cls, text: str) -> set[str]:
        tokens = set(re.findall(r"[a-z0-9\.\-]+", text.lower()))
        return tokens - cls._STOPWORDS

    def recall(self, bank_id, query, top_k=5) -> list[MemoryResult]:
        records = self._load(bank_id)
        if not records:
            return []
        q_tokens = self._tokenize(query)
        now = time.time()
        scored = []
        for r in records:
            c_tokens = self._tokenize(r["content"])
            shared = q_tokens & c_tokens
            if not shared:
                continue
            # Overlap coefficient (relative to the smaller set) rather than
            # Jaccard-over-union: a short query shouldn't be penalized just
            # because the stored record is long.
            overlap_coeff = len(shared) / max(1, min(len(q_tokens), len(c_tokens)))
            indicator_hits = [t for t in shared if self._INDICATOR_RE.match(t)]
            indicator_bonus = min(0.6, 0.4 * len(indicator_hits))
            base_score = min(1.0, overlap_coeff + indicator_bonus)
            if base_score < self._MIN_RELEVANCE:
                continue
            # Mild recency boost: memories from the last 30 days score higher,
            # capped so it never dominates topical relevance.
            age_days = max(0.0, (now - r.get("retained_at", now)) / 86400)
            recency_boost = max(0.0, 1.0 - min(age_days, 30) / 30) * 0.1
            score = min(1.0, base_score + recency_boost)
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            MemoryResult(
                content=r["content"],
                score=round(score, 4),
                timestamp=r.get("timestamp"),
                metadata=r.get("metadata", {}),
                backend=self.name,
            )
            for score, r in scored[:top_k]
        ]

    def reflect(self, bank_id, query) -> str:
        """
        No LLM synthesis in the fallback — reflect() degrades to a
        concatenated summary of the top recalled memories. This is a known,
        documented limitation (see docs/LIMITATIONS.md), not a hidden one.
        """
        results = self.recall(bank_id, query, top_k=5)
        if not results:
            return "No relevant memory found."
        lines = [f"- {r.content} (relevance {r.score:.2f})" for r in results]
        return "Based on recalled memory:\n" + "\n".join(lines)


def _server_reachable(base_url: str, timeout: float = 2.0) -> bool:
    try:
        import urllib.request

        url = f"{base_url.rstrip('/')}/health"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return True
        except Exception:
            with urllib.request.urlopen(base_url, timeout=timeout) as resp:
                return True
    except Exception:
        return False


def get_backend() -> MemoryBackend:
    """
    Auto-select the backend: real Hindsight if HINDSIGHT_API_URL is set and
    reachable and the client library is installed, else the local fallback.
    """
    base_url = os.environ.get("HINDSIGHT_API_URL", "").strip()
    if base_url:
        try:
            if _server_reachable(base_url):
                backend = HindsightBackend(base_url)
                print(f"[hindsight_wrapper] Using real Hindsight server at {base_url}")
                return backend
        except ImportError:
            print(
                "[hindsight_wrapper] HINDSIGHT_API_URL is set but the "
                "`hindsight-client` package isn't installed. "
                "Run: pip install hindsight-client"
            )
        except Exception as exc:  # pragma: no cover - defensive
            print(f"[hindsight_wrapper] Could not connect to Hindsight server: {exc}")

    print(
        "[hindsight_wrapper] No reachable Hindsight server found — "
        "using local fallback store (.local_memory/). See docs/LIMITATIONS.md."
    )
    return LocalFallbackStore()
