import uuid
import json
import os
import requests
 
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from upstash_redis import Redis
 
load_dotenv()
 
app = FastAPI(title="Duplicate Audit Copilot")
 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
 
r = Redis.from_env()
 
HUBSPOT_API_KEY       = os.getenv("HUBSPOT_API_KEY")
HUBSPOT_CLIENT_ID     = os.getenv("HUBSPOT_CLIENT_ID")
HUBSPOT_CLIENT_SECRET = os.getenv("HUBSPOT_CLIENT_SECRET")
HUBSPOT_REDIRECT_URI  = os.getenv("HUBSPOT_REDIRECT_URI", "http://localhost:8000/oauth/callback")
 
# ======================
# 🔹 Health
# ======================
@app.get("/health")
def health():
    return {"status": "ok"}
 
# ======================
# 🔹 OAuth
# ======================
@app.get("/", response_class=HTMLResponse)
def root():
    return HTMLResponse("<h2>Duplicate Audit Copilot is running</h2>")
 
@app.get("/oauth/callback")
def oauth_callback(request: Request):
    code = request.query_params.get("code")
    if not code:
        return HTMLResponse("<h2>No code received</h2>")
    try:
        resp = requests.post(
            "https://api.hubapi.com/oauth/v1/token",
            data={
                "grant_type":    "authorization_code",
                "client_id":     HUBSPOT_CLIENT_ID,
                "client_secret": HUBSPOT_CLIENT_SECRET,
                "redirect_uri":  HUBSPOT_REDIRECT_URI,
                "code":          code,
            },
        )
        token_data   = resp.json()
        access_token = token_data.get("access_token", "")
        portal_id    = token_data.get("hub_id", "")
        if portal_id:
            r.set(f"portal:{portal_id}:access_token", access_token)
            r.set(f"portal:{portal_id}:token_data", json.dumps(token_data))
            print(f"Portal {portal_id} connected")
    except Exception as e:
        print(f"OAuth exchange error: {e}")
    return HTMLResponse("<h2>App Installed Successfully</h2><p>You may close this tab.</p>")
 
# ======================
# 🔹 Job Queue
# ======================
@app.post("/start-job")
async def start_job(request: Request):
    try:
        body = await request.body()
        data = json.loads(body) if body else {}
        if isinstance(data, str):
            data = json.loads(data)
    except Exception:
        data = {}
 
    job_id = str(uuid.uuid4())
    r.set(f"{job_id}:status", "queued")
    r.set(f"{job_id}:data", json.dumps(data))
    r.rpush("queue", job_id)
    print(f"Job queued: {job_id}")
    return {"job_id": job_id, "status": "queued"}
 
 
@app.get("/job/{job_id}")
def get_job_status(job_id: str):
    status = r.get(f"{job_id}:status")
    if not status:
        return {"status": "not_found"}
    if status == "done":
        raw    = r.get(f"{job_id}:result")
        result = json.loads(raw) if raw else {}
        return {"status": "done", "result": result}
    if status == "error":
        msg = r.get(f"{job_id}:error") or "Unknown error"
        return {"status": "error", "message": msg}
    return {"status": status}
 
# ======================
# 🔹 Merge Records
# ======================
@app.post("/merge")
async def merge_records(request: Request):
    """
    Merges two HubSpot contacts.
    primary_id   = the record to KEEP (master)
    duplicate_id = the record to DELETE (gets absorbed into primary)
    """
    try:
        body = await request.body()
        data = json.loads(body) if body else {}
        if isinstance(data, str):
            data = json.loads(data)
    except Exception:
        data = {}
 
    primary_id   = data.get("primary_id")
    duplicate_id = data.get("duplicate_id")
 
    if not primary_id or not duplicate_id:
        return {"ok": False, "error": "primary_id and duplicate_id are required"}
 
    if not HUBSPOT_API_KEY:
        return {"ok": False, "error": "HUBSPOT_API_KEY not set in .env"}
 
    print(f"Merging: keep={primary_id}, delete={duplicate_id}")
 
    try:
        resp = requests.post(
            "https://api.hubapi.com/crm/v3/objects/contacts/merge",
            headers={
                "Authorization": f"Bearer {HUBSPOT_API_KEY}",
                "Content-Type":  "application/json",
            },
            json={
                "primaryObjectId":  primary_id,
                "objectIdToMerge":  duplicate_id,
            },
            timeout=15,
        )
 
        if resp.status_code >= 400:
            error_msg = resp.json().get("message", resp.text[:200])
            print(f"HubSpot merge error: {error_msg}")
            return {"ok": False, "error": error_msg}
 
        print(f"Merge successful: {primary_id} absorbed {duplicate_id}")
        return {"ok": True, "kept": primary_id, "deleted": duplicate_id}
 
    except Exception as e:
        print(f"Merge exception: {e}")
        return {"ok": False, "error": str(e)}
 