"""
scripts/web_server.py
=====================
FastAPI interactive server for SentinelMind SOC Triage Agent.
Connects the web dashboard directly to live Hindsight memory and Groq LLM reasoning.
"""

import os
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Load environment
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

from src.agent import SentinelAgent, Alert, Verdict
from src.hindsight_wrapper import get_backend
from src.llm_reasoner import get_reasoner
from src.memory_defense import Decision

app = FastAPI(title="SentinelMind SOC Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Persistent in-memory log of defense decisions
DEFENSE_LOG: List[Dict[str, Any]] = [
    {"alert": "ALERT-001", "source": "analyst_verdict", "trust": 0.95, "decision": "AUTO_RETAIN", "reason": "Confirmed analyst verdict with high trust score."},
    {"alert": "ALERT-004", "source": "analyst_verdict", "trust": 0.95, "decision": "AUTO_RETAIN", "reason": "Nightly backup FP confirmed by analyst."},
    {"alert": "ALERT-002 (poison attempt)", "source": "raw_alert_payload", "trust": 0.00, "decision": "REJECTED", "reason": "Adversarial injection patterns detected in payload."},
]

# Sample corpus
SAMPLE_ALERTS = [
    {
        "id": "ALERT-001",
        "kind": "phishing_email",
        "summary": "Email from billing@paypa1-secure.com claiming account suspension, asks recipient to verify identity via link",
        "indicators": {"sender_domain": "paypa1-secure.com", "sender_ip": "185.220.101.7"},
        "raw": "Urgent notice: verify account at paypa1-secure.com immediately or your access will be disabled."
    },
    {
        "id": "ALERT-002",
        "kind": "network_ioc",
        "summary": "Outbound connection from workstation WKS-014 to 185.220.101.7 on port 443, flagged by IDS as low-confidence",
        "indicators": {"dest_ip": "185.220.101.7", "host": "WKS-014", "port": "443"},
        "raw": "IDS alert low-confidence TCP 443 -> 185.220.101.7"
    },
    {
        "id": "ALERT-003",
        "kind": "phishing_email",
        "summary": "Email from noreply@paypa1-secure.com to finance lead, requesting urgent credential update",
        "indicators": {"sender_domain": "paypa1-secure.com", "sender_ip": "185.220.101.7"},
        "raw": "Password expiration alert from paypa1-secure.com"
    },
    {
        "id": "ALERT-004",
        "kind": "network_ioc",
        "summary": "Internal scheduled backup job from finance-server to backup-nas.internal.local, flagged by new IDS rule",
        "indicators": {"dest_ip": "10.0.4.22", "host": "finance-server", "port": "445"},
        "raw": "SMB connection to backup-nas on scheduled window"
    },
    {
        "id": "ALERT-005",
        "kind": "network_ioc",
        "summary": "Outbound connection from workstation WKS-031 to 185.220.101.7 on port 443",
        "indicators": {"dest_ip": "185.220.101.7", "host": "WKS-031", "port": "443"},
        "raw": "TLS session established to 185.220.101.7"
    },
]

class TriageRequest(BaseModel):
    alert_id: str
    kind: str
    summary: str
    indicators: Dict[str, Any] = {}
    raw: Optional[str] = None

class FeedbackRequest(BaseModel):
    alert_id: str
    kind: str
    summary: str
    analyst_verdict: str  # benign, suspicious, malicious
    analyst_notes: str
    indicators: Dict[str, Any] = {}
    raw_payload: Optional[str] = None
    trust_score: float = 0.95

@app.get("/api/status")
def get_status():
    backend = get_backend()
    reasoner = get_reasoner()
    backend_name = getattr(backend, "name", type(backend).__name__)
    reasoner_name = getattr(reasoner, "__name__", str(reasoner))
    return {
        "status": "online",
        "backend": backend_name,
        "reasoner": reasoner_name,
        "hindsight_bank": os.environ.get("HINDSIGHT_BANK_ID", "sentinelmind-soc"),
    }

@app.get("/api/alerts")
def get_alerts():
    return {"alerts": SAMPLE_ALERTS}

@app.get("/api/defense-log")
def get_defense_log():
    return {"log": DEFENSE_LOG}

@app.post("/api/triage")
def run_triage(req: TriageRequest):
    backend = get_backend()
    reasoner = get_reasoner()
    bank_id = os.environ.get("HINDSIGHT_BANK_ID", "sentinelmind-soc")
    
    agent = SentinelAgent(backend=backend, reasoner=reasoner, bank_id=bank_id)
    
    alert = Alert(
        alert_id=req.alert_id,
        kind=req.kind,
        summary=req.summary,
        indicators=req.indicators,
        raw=req.raw or "",
    )
    
    try:
        # Run stateless triage
        t0 = time.perf_counter()
        stateless_res = agent.triage(alert, mode="stateless")
        stateless_ms = (time.perf_counter() - t0) * 1000.0

        # Run memory-informed triage
        t1 = time.perf_counter()
        memory_res = agent.triage(alert, mode="memory")
        memory_ms = (time.perf_counter() - t1) * 1000.0
        
        return {
            "alert_id": req.alert_id,
            "stateless": {
                "verdict": stateless_res.verdict,
                "confidence": stateless_res.confidence,
                "rationale": stateless_res.rationale,
                "duration_ms": round(stateless_ms, 2),
            },
            "memory": {
                "verdict": memory_res.verdict,
                "confidence": memory_res.confidence,
                "rationale": memory_res.rationale,
                "duration_ms": round(memory_ms, 2),
                "memories": [
                    {
                        "score": round(m.score, 2),
                        "text": m.content,
                        "source": m.metadata.get("source", "hindsight") if m.metadata else "hindsight",
                        "timestamp": m.timestamp,
                    }
                    for m in memory_res.used_memories
                ],
            },
            "verdict_changed": stateless_res.verdict != memory_res.verdict,
        }
    finally:
        agent.close()

@app.post("/api/feedback")
def submit_feedback(req: FeedbackRequest):
    backend = get_backend()
    reasoner = get_reasoner()
    bank_id = os.environ.get("HINDSIGHT_BANK_ID", "sentinelmind-soc")
    agent = SentinelAgent(backend=backend, reasoner=reasoner, bank_id=bank_id)
    
    alert = Alert(
        alert_id=req.alert_id,
        kind=req.kind,
        summary=req.summary,
        indicators=req.indicators,
        raw=req.raw_payload or "",
    )
    
    try:
        verdict_res = agent.record_feedback(
            alert=alert,
            analyst_verdict=req.analyst_verdict,
            analyst_note=req.analyst_notes,
        )
        assessment = verdict_res["assessment"]
        retained = verdict_res["retained"]
        decision_str = assessment.decision.value if hasattr(assessment.decision, "value") else str(assessment.decision)
        reasons_str = "; ".join(assessment.reasons) if assessment.reasons else "Passed trust & consistency checks"
        
        DEFENSE_LOG.insert(0, {
            "alert": req.alert_id,
            "source": "analyst_verdict",
            "trust": assessment.trust_score,
            "decision": decision_str,
            "retained": retained,
            "reason": reasons_str,
        })
        
        return {
            "alert_id": req.alert_id,
            "decision": decision_str,
            "trust_score": assessment.trust_score,
            "retained": retained,
            "reason": reasons_str,
        }
    finally:
        agent.close()

# Mount frontend directory for static assets
frontend_dir = ROOT_DIR / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

def is_port_in_use(host: str, port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "3000"))
    host = "127.0.0.1"
    if is_port_in_use(host, port):
        print(f"[web_server] SentinelMind dashboard is ALREADY running at http://localhost:{port}. Reusing running instance.")
        sys.exit(0)
    print(f"[web_server] Starting SentinelMind live dashboard on http://localhost:{port}")
    uvicorn.run("scripts.web_server:app", host=host, port=port, reload=False)
