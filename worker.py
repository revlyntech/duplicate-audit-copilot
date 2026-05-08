import json
import time
import os
import requests
from dotenv import load_dotenv
from upstash_redis import Redis
from logic import run_duplicate_detection

load_dotenv()

r = Redis.from_env()
HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")

def fetch_all_hubspot_contacts():
    contacts = []
    after = None
    while True:
        params = {
            "limit": 100,
            "properties": "firstname,lastname,email,phone,company"
        }
        if after:
            params["after"] = after
        resp = requests.get(
            "https://api.hubapi.com/crm/v3/objects/contacts",
            headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}"},
            params=params,
            timeout=15
        )
        data = resp.json()
        for contact in data.get("results", []):
            props = contact.get("properties", {})
            contacts.append({
                "id": contact["id"],
                "name": f"{props.get('firstname','') or ''} {props.get('lastname','') or ''}".strip(),
                "email": props.get("email") or "",
                "phone": props.get("phone") or "",
                "company": props.get("company") or "",
            })
        after = data.get("paging", {}).get("next", {}).get("after")
        if not after:
            break
    return contacts

print("Worker started — waiting for jobs...")

while True:
    try:
        job_id = r.lpop("queue")
        if not job_id:
            time.sleep(2)
            continue

        print(f"Processing job: {job_id}")
        r.set(f"{job_id}:status", "processing")

        # Fetch ALL contacts from HubSpot
        print("Fetching contacts from HubSpot...")
        all_contacts = fetch_all_hubspot_contacts()
        print(f"Got {len(all_contacts)} contacts to scan")

        result = run_duplicate_detection(all_contacts)

        r.set(f"{job_id}:result", json.dumps(result))
        r.set(f"{job_id}:status", "done")
        print(f"Done: {job_id} — {len(result.get('clusters', []))} duplicate clusters found")

    except Exception as e:
        print(f"Worker error: {e}")
        if job_id:
            r.set(f"{job_id}:status", "error")
            r.set(f"{job_id}:error", str(e))
        time.sleep(2)