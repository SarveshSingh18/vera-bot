from fastapi import FastAPI
app = FastAPI()
@app.get("/v1/healthz")
def healthz():
 return {"status":"ok"}
@app.get("/v1/metadata")
def metadata():
 return {"team":"Vera"}
@app.post("/v1/context")
def context(b:dict):
 return {"accepted":True}
@app.post("/v1/tick")
def tick(b:dict):
 return {"actions":[]}
@app.post("/v1/reply")
def reply(b:dict):
 return {"action":"wait"}
