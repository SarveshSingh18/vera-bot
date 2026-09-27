from fastapi import FastAPI
import time
app = FastAPI()
s = time.time()
@app.get("/v1/healthz")
def h():
 return {"status":"ok","uptime_seconds":int(time.time()-s)}
@app.get("/v1/metadata")
def m():
 return {"team_name":"Vera"}
@app.post("/v1/context")
def c(b:dict):
 return {"accepted":True}
@app.post("/v1/tick")
def t(b:dict):
 return {"actions":[]}
@app.post("/v1/reply")
def r(b:dict):
 return {"action":"wait"}
