from fastapi import FastAPI
import time

app = FastAPI()
start = time.time()

@app.get("/v1/healthz")
def healthz():
    return {"status": "ok", "uptime_seconds": int(time.time() - start)}

@app.get("/v1/metadata")
def metadata():
    return {"team_name": "Vera Bot"}

@app.post("/v1/context")
def context(body: dict):
    return {"accepted": True}

@app.post("/v1/tick")
def tick(body: dict):
    return {"actions": []}

@app.post("/v1/reply")
def reply(body: dict):
    return {"action": "wait"}