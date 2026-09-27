from fastapi import FastAPI
from datetime import datetime
import time

app = FastAPI(title="Vera Bot", version="1.0.0")
START_TIME = time.time()

@app.get("/v1/healthz")
async def healthz():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    }

@app.get("/v1/metadata")
async def metadata():
    return {
        "team_name": "Vera Bot Team",
        "team_members": ["Developer"],
        "model": "rule-based-composer",
        "approach": "Deterministic rule-based composer. No LLM.",
        "contact_email": "developer@example.com",
        "version": "1.0.0",
        "submitted_at": datetime.utcnow().isoformat() + 'Z'
    }

@app.post("/v1/context")
async def push_context(body: dict):
    return {"accepted": True, "ack_id": "ack_1", "stored_at": datetime.utcnow().isoformat() + 'Z'}

@app.post("/v1/tick")
async def tick(body: dict):
    return {"actions": []}

@app.post("/v1/reply")
async def reply(body: dict):
    return {"action": "wait", "wait_seconds": 1800, "rationale": "Waiting"}

@app.get("/")
async def root():
    return {"name": "Vera Bot", "status": "running", "docs": "/docs"}
