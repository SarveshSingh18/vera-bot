from fastapi import FastAPI
import time

app = FastAPI()
START_TIME = time.time()

@app.get("/v1/healthz")
async def healthz():
    return {"status": "ok", "uptime_seconds": int(time.time() - START_TIME)}

@app.get("/v1/metadata")
async def metadata():
    return {"team_name": "Vera Bot Team", "version": "1.0.0"}

@app.post("/v1/context")
async def context(body: dict):
    return {"accepted": True}

@app.post("/v1/tick")
async def tick(body: dict):
    return {"actions": []}

@app.post("/v1/reply")
async def reply(body: dict):
    return {"action": "wait"}

@app.get("/")
async def root():
    return {"status": "ok"}