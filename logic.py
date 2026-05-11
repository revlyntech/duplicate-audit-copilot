import os
import json
import requests
from dotenv import load_dotenv
from rapidfuzz import fuzz

load_dotenv()

CLAUDE_API_KEY  = os.getenv("CLAUDE_API_KEY")
HUBSPOT_API_KEY = os.getenv("HUBSPOT_API_KEY")

#  UTILS

def normalize(text):
    return text.lower().strip() if text else ""

def clean_phone(phone: str) -> str:
    """Strip all non-digits from a phone number."""
    return "".join(c for c in (phone or "") if c.isdigit())

#  UNIQUE IDENTIFIER CHECKS

def same_email(a, b) -> bool:
    """True if both contacts share the same non-empty email."""
    ea = normalize(a.get("email") or "")
    eb = normalize(b.get("email") or "")
    return bool(ea and eb and ea == eb)

def same_phone(a, b) -> bool:
    """
    True if both contacts share the same phone number.
    Compares last 10 digits to handle country code differences.
    9369330957 == +91 9369330957 == 91-9369-330957
    """
    pa = clean_phone(a.get("phone") or "")
    pb = clean_phone(b.get("phone") or "")
    if not pa or not pb:
        return False
  
    return pa[-10:] == pb[-10:] if len(pa) >= 10 and len(pb) >= 10 else pa[-7:] == pb[-7:]

#  SIMILARITY (for non-unique fields)

def name_similarity(a, b) -> float:
    """
    Returns 0.0 – 1.0.

    Rules:
    - If either contact has no last name: weak match only (0.4 if first names match)
    - If last names are different (< 70% similar): return 0 immediately
    - Otherwise: full name comparison using fuzz.ratio
    """
    na = normalize(a.get("name") or "")
    nb = normalize(b.get("name") or "")
    if not na or not nb:
        return 0.0

    parts_a = na.split()
    parts_b = nb.split()

    if len(parts_a) < 2 or len(parts_b) < 2:
        return 0.4 if (parts_a[0] == parts_b[0]) else 0.0

    last_sim = fuzz.ratio(parts_a[-1], parts_b[-1]) / 100.0
    if last_sim < 0.70:
        return 0.0

    full_sim = fuzz.ratio(na, nb) / 100.0
    return max(full_sim, last_sim * 0.9)

def company_similarity(a, b) -> float:
    """
    Returns 0.0 – 1.0.
    Ignores short/generic names (ABC, Inc, Ltd, LLC).
    """
    ca = normalize(a.get("company") or "")
    cb = normalize(b.get("company") or "")
    if not ca or not cb:
        return 0.0
    if len(ca) < 5 or len(cb) < 5:
        return 0.0
    return max(fuzz.token_set_ratio(ca, cb), fuzz.partial_ratio(ca, cb)) / 100.0

#  SCORING

def calculate_score(a, b) -> float:
    """
    Composite similarity score 0.0 – 1.0.

    Unique identifiers (email, phone) dominate the score.
    Non-unique fields (name, company) only add supporting weight.
    """
    score = 0.0
    if same_email(a, b): score += 0.90   
    if same_phone(a, b): score += 0.88   
    score += name_similarity(a, b)    * 0.50
    score += company_similarity(a, b) * 0.40
    return min(score, 1.0)

def get_match_reasons(a, b) -> list:
    reasons = []
    if same_email(a, b):
        reasons.append(f"Same email: {a.get('email')}")
    if same_phone(a, b):
        reasons.append(f"Same phone number: {a.get('phone')}")
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

def get_matched_fields(a, b) -> list:
    fields = []
    if same_email(a, b):                  fields.append("email")
    if same_phone(a, b):                  fields.append("phone")
    if name_similarity(a, b) >= 0.70:    fields.append("name")
    if company_similarity(a, b) >= 0.65: fields.append("company")
    return fields

#  BLOCKING

def create_blocks(records: list) -> dict:
    """
    Group records into blocks so we only compare likely duplicates.
    Uses last name (not first name) to avoid grouping all "Rahuls" together.
    """
    blocks = {}
    for r in records:
        keys = set()

        if r.get("email"):
            keys.add("email:" + normalize(r["email"]))

        if r.get("phone"):
            digits = clean_phone(r["phone"])
            if len(digits) >= 10:
                keys.add("phone10:" + digits[-10:])
            elif len(digits) >= 7:
                keys.add("phone7:" + digits[-7:])

        if r.get("name"):
            parts = normalize(r["name"]).split()
            if len(parts) >= 2:
                keys.add("lastname:" + parts[-1][:6])
            else:
                keys.add("firstname:" + parts[0][:6])

        for key in keys:
            blocks.setdefault(key, []).append(r)

    return blocks


#  AI VALIDATION (borderline cases only)

def batch_validate_with_ai(pairs: list, record_map: dict) -> dict:
    """
    Send borderline pairs (name+company match, no email/phone match) to Claude.
    Only called when score is in the 0.65–0.88 range.
    """
    results = {}
    if not pairs:
        return results

    prompt = (
        "You are a strict CRM deduplication expert.\n\n"
        "RULES:\n"
        "1. Same email = DUPLICATE (email is unique to one person).\n"
        "2. Same phone number = DUPLICATE (phone is unique to one person).\n"
        "3. Same full name (first + last) AND same company = PROBABLE DUPLICATE.\n"
        "4. Same name, DIFFERENT companies = NOT a duplicate (different people).\n"
        "5. Same first name only = NOT a duplicate.\n\n"
        "The pairs below have already passed email and phone checks (no match on those).\n"
        "Evaluate based on name and company similarity only:\n\n"
    )
    for idx, (a_id, b_id) in enumerate(pairs):
        a = record_map[a_id]
        b = record_map[b_id]
        prompt += (
            f"Pair {idx}:\n"
            f"  A: name={a.get('name')}  email={a.get('email')}  "
            f"phone={a.get('phone')}  company={a.get('company')}\n"
            f"  B: name={b.get('name')}  email={b.get('email')}  "
            f"phone={b.get('phone')}  company={b.get('company')}\n\n"
        )

    prompt += 'Return ONLY JSON: [{"pair_index": 0, "is_duplicate": true, "reason": "..."}]\n'

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
        raw = response.json()["content"][0]["text"].strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        for item in json.loads(raw):
            if item.get("is_duplicate"):
                results[pairs[item["pair_index"]]] = True
    except Exception as e:
        print(f"AI validation error: {e}")

    return results

#  UNION-FIND CLUSTERING

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
            clusters.setdefault(self.find(node), []).append(node)
        return list(clusters.values())


#  MASTER RECORD

def record_quality_score(r) -> int:
    score = 0
    if r.get("email"):   score += 2
    if r.get("phone"):   score += 2
    if r.get("name"):    score += 1
    if r.get("company"): score += 1
    return score

def choose_master(cluster):
    return max(cluster, key=record_quality_score)

def generate_merge(cluster, master):
    merged = {}
    for field in ["email", "phone", "name", "company"]:
        values = list(dict.fromkeys([r.get(field) for r in cluster if r.get(field)]))
        merged[field] = values[0] if values else ""
    return merged

def confidence(cluster) -> float:
    scores = [
        calculate_score(cluster[i], cluster[j])
        for i in range(len(cluster))
        for j in range(i + 1, len(cluster))
    ]
    return round(sum(scores) / len(scores), 2) if scores else 0.0

#  MAIN DETECTION

def run_duplicate_detection(records: list) -> dict:
   
    blocks        = create_blocks(records)
    uf            = UnionFind()
    pair_matches  = []  
    ai_candidates = []  

    record_map = {r["id"]: r for r in records}

    for block in blocks.values():
        for i in range(len(block)):
            for j in range(i + 1, len(block)):
                a, b = block[i], block[j]

                if same_email(a, b) or same_phone(a, b):
                    pair_matches.append((a["id"], b["id"]))
                    continue

                ns = name_similarity(a, b)
                cs = company_similarity(a, b)

                if ns < 0.80:
                    continue   
                if cs < 0.65:
                    continue   

                ai_candidates.append((a["id"], b["id"]))

    for pair in batch_validate_with_ai(ai_candidates, record_map):
        pair_matches.append(pair)

    seen = set()
    unique_pairs = []
    for pair in pair_matches:
        key = tuple(sorted(pair))
        if key not in seen:
            seen.add(key)
            unique_pairs.append(pair)

    for a_id, b_id in unique_pairs:
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
            "master_name":      master.get("name")  or "Unknown",
            "master_email":     master.get("email") or "",
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

    results.sort(key=lambda x: x["confidence"], reverse=True)

    return {
        "success":               True,
        "clusters":              results,
        "total_duplicates":      len(results),
        "total_records_scanned": len(records),
    }
