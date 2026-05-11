import os
import json
import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz

load_dotenv()

CLAUDE_API_KEY  = os.getenv("CLAUDE_API_KEY")
HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")

def normalize(text):
    return text.lower().strip() if text else ""

def email_match(a, b):
    ea = normalize(a.get("email"))
    eb = normalize(b.get("email"))
    if not ea or not eb:
        return 0
    return 1 if ea == eb else 0

def phone_match(a, b):
    if not a.get("phone") or not b.get("phone"):
        return 0
    a_digits = "".join(c for c in a.get("phone", "") if c.isdigit())
    b_digits = "".join(c for c in b.get("phone", "") if c.isdigit())
    if not a_digits or not b_digits:
        return 0
    return 1 if a_digits[-7:] == b_digits[-7:] else 0

def name_similarity(a, b):
    na = normalize(a.get("name", ""))
    nb = normalize(b.get("name", ""))
    if not na or not nb:
        return 0

    parts_a = na.split()
    parts_b = nb.split()

    if len(parts_a) < 2 or len(parts_b) < 2:
        first_a = parts_a[0] if parts_a else ""
        first_b = parts_b[0] if parts_b else ""
        return 0.4 if first_a == first_b else 0

    last_a  = parts_a[-1]
    last_b  = parts_b[-1]
    last_sim = fuzz.ratio(last_a, last_b) / 100

    if last_sim < 0.70:
        return 0

    full_sim = fuzz.ratio(na, nb) / 100
    return max(full_sim, last_sim * 0.9)

def company_similarity(a, b):
    ca = normalize(a.get("company", ""))
    cb = normalize(b.get("company", ""))
    if not ca or not cb:
        return 0
  
    if len(ca) < 5 or len(cb) < 5:
        return 0
    return max(fuzz.token_set_ratio(ca, cb), fuzz.partial_ratio(ca, cb)) / 100

# SCORING + REASONS

def calculate_score(a, b):
    score = 0
    if email_match(a, b):   score += 0.90
    if phone_match(a, b):   score += 0.85
    score += name_similarity(a, b)    * 0.60
    score += company_similarity(a, b) * 0.50
    return min(score, 1.0)

def get_match_reasons(a, b):
    reasons = []
    if email_match(a, b):
        reasons.append(f"Same email: {a.get('email')}")
    if phone_match(a, b):
        reasons.append("Same phone number")
    ns = name_similarity(a, b)
    if ns >= 0.90:
        reasons.append(f"Very similar names: '{a.get('name')}' \u2248 '{b.get('name')}'")
    elif ns >= 0.70:
        reasons.append(f"Similar names: '{a.get('name')}' \u2248 '{b.get('name')}'")
    cs = company_similarity(a, b)
    if cs >= 0.85:
        reasons.append(f"Same company: {a.get('company')}")
    elif cs >= 0.65:
        reasons.append(f"Similar company: '{a.get('company')}' \u2248 '{b.get('company')}'")
    return reasons

def get_matched_fields(a, b):
    fields = []
    if email_match(a, b):                  fields.append("email")
    if phone_match(a, b):                  fields.append("phone")
    if name_similarity(a, b) >= 0.70:     fields.append("name")
    if company_similarity(a, b) >= 0.65:  fields.append("company")
    return fields

#  BLOCKING

def create_blocks(records):
    blocks = {}
    for r in records:
        keys = set()

        if r.get("email"):
            keys.add("email_domain:" + r["email"].split("@")[-1])
            keys.add("email_exact:"  + normalize(r["email"]))

        if r.get("phone"):
            digits = "".join(c for c in r["phone"] if c.isdigit())
            if digits:
                keys.add("phone:" + digits[-7:])

        if r.get("name"):
            parts = normalize(r["name"]).split()
            if len(parts) >= 2:
                keys.add("lastname:" + parts[-1][:5])
            else:
           
                keys.add("firstname:" + parts[0][:5])

        for key in keys:
            blocks.setdefault(key, []).append(r)

    return blocks

#  AI VALIDATION

def batch_validate_with_ai(pairs, record_map):
    results = {}
    if not pairs:
        return results

    prompt = (
        "You are a strict CRM data expert. Only confirm duplicates when there is "
        "strong evidence: same email, same phone, or nearly identical FULL name "
        "(first + last) with same company. "
        "Do NOT confirm duplicates based on shared first name alone. "
        "Different last names = different people.\n\n"
        "Determine if each pair is a real duplicate:\n"
    )
    for idx, (a_id, b_id) in enumerate(pairs):
        a = record_map[a_id]
        b = record_map[b_id]
        prompt += (
            f"\nPair {idx}:\n"
            f"  A: name={a.get('name')}  email={a.get('email')}  "
            f"phone={a.get('phone')}  company={a.get('company')}\n"
            f"  B: name={b.get('name')}  email={b.get('email')}  "
            f"phone={b.get('phone')}  company={b.get('company')}\n"
        )

    prompt += """
Return ONLY a JSON array, no other text:
[{"pair_index": 0, "is_duplicate": true, "reason": "same email"}]
"""

    try:
        response = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key":         CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type":      "application/json",
            },
            json={
                "model":      "claude-sonnet-4-20250514",
                "max_tokens": 1000,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        raw_text = response.json()["content"][0]["text"].strip()
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        parsed = json.loads(raw_text)
        for item in parsed:
            if item.get("is_duplicate"):
                results[pairs[item["pair_index"]]] = True
    except Exception as e:
        print(f"AI validation error: {e}")

    return results

#  UNION-FIND CLUSTER

class UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        if self.parent.setdefault(x, x) != x:
            self.parent[x] = self.find(self.parent[x])
        return self.parent[x]

    def union(self, a, b):
        self.parent[self.find(a)] = self.find(b)

    def groups(self):
        clusters = {}
        for node in self.parent:
            root = self.find(node)
            clusters.setdefault(root, []).append(node)
        return list(clusters.values())

#  MASTER RECORD

def record_quality_score(r):
    score = 0
    if r.get("email"):   score += 2
    if r.get("phone"):   score += 2
    if r.get("name"):    score += 1
    if r.get("company"): score += 1
    return score

def choose_master(cluster):
    return max(cluster, key=record_quality_score)

#  MERGE SUGGESTION

def generate_merge(cluster, master):
    merged = {}
    for field in ["email", "phone", "name", "company"]:
        values = list(dict.fromkeys([r.get(field) for r in cluster if r.get(field)]))
        merged[field] = values[0] if values else ""
    return merged

#  CONFIDENCE

def confidence(cluster):
    scores = []
    for i in range(len(cluster)):
        for j in range(i + 1, len(cluster)):
            scores.append(calculate_score(cluster[i], cluster[j]))
    return round(sum(scores) / len(scores), 2) if scores else 0

# MAIN DETECTION

def run_duplicate_detection(records):
    blocks        = create_blocks(records)
    uf            = UnionFind()
    pair_matches  = []
    ai_candidates = []

    for block in blocks.values():
        for i in range(len(block)):
            for j in range(i + 1, len(block)):
                a, b  = block[i], block[j]
                score = calculate_score(a, b)

                if score > 0.85:                  
                    pair_matches.append((a["id"], b["id"]))
                elif score > 0.70:                  
                    ai_candidates.append((a["id"], b["id"]))

    record_map = {r["id"]: r for r in records}

    ai_results = batch_validate_with_ai(ai_candidates, record_map)
    for pair in ai_results:
        pair_matches.append(pair)

    for a_id, b_id in pair_matches:
        uf.union(a_id, b_id)

    results = []
    for cluster in uf.groups():
        if len(cluster) < 2:
            continue

        cluster_records = [record_map[i] for i in cluster]
        master = choose_master(cluster_records)
        merged = generate_merge(cluster_records, master)
        conf   = confidence(cluster_records)

        all_reasons = []
        all_fields  = []
        for i in range(len(cluster_records)):
            for j in range(i + 1, len(cluster_records)):
                all_reasons.extend(get_match_reasons(cluster_records[i], cluster_records[j]))
                all_fields.extend(get_matched_fields(cluster_records[i], cluster_records[j]))

        results.append({
            "cluster_ids":      cluster,
            "confidence":       conf,
            "master_id":        master["id"],
            "master_name":      master.get("name", "Unknown"),
            "master_email":     master.get("email", ""),
            "matched_fields":   list(dict.fromkeys(all_fields)),
            "reasons":          list(dict.fromkeys(all_reasons)),
            "merge_suggestion": merged,
            "records": [
                {
                    "id":        r["id"],
                    "name":      r.get("name")    or "Unknown",
                    "email":     r.get("email")   or "",
                    "phone":     r.get("phone")   or "",
                    "company":   r.get("company") or "",
                    "is_master": r["id"] == master["id"],
                }
                for r in cluster_records
            ],
        })

    # Highest confidence first
    results.sort(key=lambda x: x["confidence"], reverse=True)

    return {
        "success":               True,
        "clusters":              results,
        "total_duplicates":      len(results),
        "total_records_scanned": len(records),
    }
