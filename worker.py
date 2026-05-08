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

def get_contact(contact_id: str) -> dict:
    
    resp = requests.get(
        f"https://api.hubapi.com/crm/v3/objects/contacts/{contact_id}",
        headers={"Authorization": f"Bearer {HUBSPOT_API_KEY}"},
        params={"properties": "firstname,lastname,email,phone,company"},
        timeout=15
    )
    return resp.json()

def search_contacts(filters: list) -> list:
    
    resp = requests.post(
        "https://api.hubapi.com/crm/v3/objects/contacts/search",
        headers={
            "Authorization": f"Bearer {HUBSPOT_API_KEY}",
            "Content-Type": "application/json"
        },
        json={
            "filterGroups": [{"filters": filters}],
            "properties": ["firstname", "lastname", "email", "phone", "company"],
            "limit": 100
        },
        timeout=15
    )
    return resp.json().get("results", [])

def format_contact(contact: dict) -> dict:
   
    props = contact.get("properties", {})
    return {
        "id": contact["id"],
        "name": f"{props.get('firstname','') or ''} {props.get('lastname','') or ''}".strip(),
        "email": props.get("email") or "",
        "phone": props.get("phone") or "",
        "company": props.get("company") or "",
    }

def find_candidates(contact_id: str) -> list:
    
    raw = get_contact(contact_id)
    if "id" not in raw:
        print(f"Contact {contact_id} not found")
        return []

    target = format_contact(raw)
    print(f"Target contact: {target['name']} | {target['email']} | {target['phone']}")

    candidates = {contact_id: target}  

   
    if target["email"]:
        results = search_contacts([{
            "propertyName": "email",
            "operator": "EQ",
            "value": target["email"]
        }])
        for r_contact in results:
            if r_contact["id"] not in candidates:
                candidates[r_contact["id"]] = format_contact(r_contact)

   
    if target["phone"]:
        digits = "".join(c for c in target["phone"] if c.isdigit())
        if len(digits) >= 7:
            results = search_contacts([{
                "propertyName": "phone",
                "operator": "CONTAINS_TOKEN",
                "value": digits[-7:]
            }])
            for r_contact in results:
                if r_contact["id"] not in candidates:
                    candidates[r_contact["id"]] = format_contact(r_contact)

    
    name_parts = target["name"].split()
    if len(name_parts) >= 1 and name_parts[0]:
        results = search_contacts([{
            "propertyName": "firstname",
            "operator": "EQ",
            "value": name_parts[0]
        }])
        for r_contact in results:
            if r_contact["id"] not in candidates:
                candidates[r_contact["id"]] = format_contact(r_contact)

    if len(name_parts) >= 2 and name_parts[-1]:
        results = search_contacts([{
            "propertyName": "lastname",
            "operator": "EQ",
            "value": name_parts[-1]
        }])
        for r_contact in results:
            if r_contact["id"] not in candidates:
                candidates[r_contact["id"]] = format_contact(r_contact)

    print(f"Found {len(candidates)} candidates to compare (vs 41,563 before)")
    return list(candidates.values())


print("Worker started — waiting for jobs...")

while True:
    job_id = None
    try:
        job_id = r.lpop("queue")
        if not job_id:
            time.sleep(2)
            continue

        print(f"⚡ Processing job: {job_id}")
        r.set(f"{job_id}:status", "processing")

        raw = r.get(f"{job_id}:data")
        if not raw:
            r.set(f"{job_id}:status", "error")
            r.set(f"{job_id}:error", "No job data found")
            continue

        data = json.loads(raw)
        if isinstance(data, str):
            data = json.loads(data)

       
        records = data.get("records", [])
        contact_id = records[0].get("id") if records else None

        if not contact_id:
            r.set(f"{job_id}:status", "error")
            r.set(f"{job_id}:error", "No contact ID in job")
            continue

       
        candidates = find_candidates(contact_id)
        print(f"Comparing {len(candidates)} candidate contacts")

        result = run_duplicate_detection(candidates)

        r.set(f"{job_id}:result", json.dumps(result))
        r.set(f"{job_id}:status", "done")
        print(f"Done: {job_id} — {len(result.get('clusters', []))} clusters found")

    except Exception as e:
        print(f"Worker error: {e}")
        if job_id:
            try:
                r.set(f"{job_id}:status", "error")
                r.set(f"{job_id}:error", str(e))
            except:
                pass
        time.sleep(2)
