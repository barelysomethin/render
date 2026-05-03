"""
Full API smoke test — simulates the judge harness flow:
Phase 1: Warmup (healthz, metadata, context pushes)
Phase 2: Tick + Reply
"""
import json
import urllib.request
import urllib.error
import sys
import os

BASE = "http://localhost:8083"

def safe_print(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode('ascii', 'replace').decode('ascii'))

def req(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body else None
    headers = {"Content-Type": "application/json"} if data else {}
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        res = urllib.request.urlopen(r, timeout=35)
        return res.status, json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))

def ok(label, status, body, expect_status=200):
    icon = "OK" if status == expect_status else "FAIL"
    safe_print(f"  [{icon}] {label} ({status}): {json.dumps(body, ensure_ascii=False)[:200]}")
    return status == expect_status

print("\n=== Pre-test: Teardown ===")
s, b = req("POST", "/v1/teardown")
ok("POST /v1/teardown", s, b)

print("\n=== Phase 1: Warmup ===")

# 1. Healthz
s, b = req("GET", "/v1/healthz")
ok("GET /v1/healthz", s, b)

# 2. Metadata
s, b = req("GET", "/v1/metadata")
ok("GET /v1/metadata", s, b)

# 3. Push category context
category_payload = {
    "slug": "dentists",
    "voice": {"tone": "peer_clinical", "vocab_taboo": ["guaranteed", "100% safe"]},
    "offer_catalog": [
        {"id": "den_001", "title": "Dental Cleaning @ ₹299", "value": "299", "audience": "new_user", "type": "service_at_price"}
    ],
    "peer_stats": {"avg_rating": 4.4, "avg_ctr": 0.030, "avg_reviews": 62, "scope": "delhi_solo_practices"},
    "digest": [
        {
            "id": "d_2026W17_jida_fluoride",
            "kind": "research",
            "title": "3-month fluoride recall cuts caries 38% better than 6-month",
            "source": "JIDA Oct 2026, p.14",
            "trial_n": 2100,
            "patient_segment": "high_risk_adults",
            "summary": "Large RCT showing 3-month recall protocol significantly reduces caries recurrence in high-risk adults."
        }
    ],
    "patient_content_library": [],
    "seasonal_beats": [{"month_range": "Nov-Feb", "note": "exam-stress bruxism spike"}],
    "trend_signals": [{"query": "clear aligners delhi", "delta_yoy": 0.62, "segment_age": "28-45"}]
}

s, b = req("POST", "/v1/context", {
    "scope": "category", "context_id": "dentists", "version": 1,
    "payload": category_payload, "delivered_at": "2026-04-26T09:45:00Z"
})
ok("POST /v1/context (category: dentists)", s, b)

# 4. Push merchant context
merchant_payload = {
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "category_slug": "dentists",
    "identity": {
        "name": "Dr. Meera's Dental Clinic", "city": "Delhi", "locality": "Lajpat Nagar",
        "verified": True, "languages": ["en", "hi"], "owner_first_name": "Meera"
    },
    "subscription": {"status": "active", "plan": "Pro", "days_remaining": 82},
    "performance": {
        "window_days": 30, "views": 2410, "calls": 18, "directions": 45,
        "ctr": 0.021, "delta_7d": {"views_pct": 0.18, "calls_pct": -0.05}
    },
    "offers": [
        {"id": "o_meera_001", "title": "Dental Cleaning @ ₹299", "status": "active"},
        {"id": "o_meera_002", "title": "Deep Cleaning @ ₹499", "status": "expired"}
    ],
    "conversation_history": [],
    "customer_aggregate": {
        "total_unique_ytd": 540, "lapsed_180d_plus": 78,
        "retention_6mo_pct": 0.38, "high_risk_adult_count": 124
    },
    "signals": ["stale_posts:22d", "ctr_below_peer_median", "high_risk_adult_cohort"]
}

s, b = req("POST", "/v1/context", {
    "scope": "merchant", "context_id": "m_001_drmeera_dentist_delhi", "version": 1,
    "payload": merchant_payload, "delivered_at": "2026-04-26T09:45:30Z"
})
ok("POST /v1/context (merchant: Dr. Meera)", s, b)

# 5. Idempotency check — same version should return 409
s, b = req("POST", "/v1/context", {
    "scope": "merchant", "context_id": "m_001_drmeera_dentist_delhi", "version": 1,
    "payload": merchant_payload, "delivered_at": "2026-04-26T09:45:30Z"
})
ok("POST /v1/context (idempotency - should 409)", s, b, expect_status=409)

# 6. Push trigger context
trigger_payload = {
    "id": "trg_001_research_digest_dentists",
    "scope": "merchant",
    "kind": "research_digest",
    "source": "external",
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "customer_id": None,
    "payload": {"category": "dentists", "top_item_id": "d_2026W17_jida_fluoride"},
    "urgency": 2,
    "suppression_key": "research:dentists:2026-W17",
    "expires_at": "2026-05-03T00:00:00Z"
}

s, b = req("POST", "/v1/context", {
    "scope": "trigger", "context_id": "trg_001_research_digest_dentists", "version": 1,
    "payload": trigger_payload, "delivered_at": "2026-04-26T10:32:00Z"
})
ok("POST /v1/context (trigger: research_digest)", s, b)

# 7. Healthz after warmup
s, b = req("GET", "/v1/healthz")
ok("GET /v1/healthz (after warmup)", s, b)
print(f"       Contexts loaded: {b.get('contexts_loaded')}")

print("\n=== Phase 2: Tick ===")

# 8. Tick — bot should compose a message for Dr. Meera
print("  [..] POST /v1/tick (calling LLM — may take 5-15s)...")
s, b = req("POST", "/v1/tick", {
    "now": "2026-04-26T10:35:00Z",
    "available_triggers": ["trg_001_research_digest_dentists"]
})
ok("POST /v1/tick", s, b)

if b.get("actions"):
    action = b["actions"][0]
    safe_print(f"\n  === COMPOSED MESSAGE ===")
    safe_print(f"  merchant_id:      {action.get('merchant_id')}")
    safe_print(f"  send_as:          {action.get('send_as')}")
    safe_print(f"  cta:              {action.get('cta')}")
    safe_print(f"  suppression_key:  {action.get('suppression_key')}")
    safe_print(f"  rationale:        {action.get('rationale')}")
    safe_print(f"\n  BODY:\n  {action.get('body')}\n")
    conv_id = action.get("conversation_id")
else:
    safe_print("  [WARN] No actions returned from tick")
    conv_id = "conv_test_001"

# 9. Suppression check — same trigger again should return empty actions
s, b2 = req("POST", "/v1/tick", {
    "now": "2026-04-26T10:40:00Z",
    "available_triggers": ["trg_001_research_digest_dentists"]
})
ok("POST /v1/tick (suppression - should return [])", s, b2)
suppressed = len(b2.get("actions", [])) == 0
safe_print(f"  Suppression working: {'YES' if suppressed else 'NO - BUG!'}")

print("\n=== Phase 3: Reply Scenarios ===")

# 10. Engaged reply
safe_print("  [..] POST /v1/reply (engaged: 'Yes please send the abstract')...")
s, b = req("POST", "/v1/reply", {
    "conversation_id": conv_id,
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "customer_id": None,
    "from_role": "merchant",
    "message": "Yes please send the abstract. Also draft the patient WhatsApp.",
    "received_at": "2026-04-26T10:42:00Z",
    "turn_number": 2
})
ok("POST /v1/reply (engaged merchant)", s, b)
safe_print(f"  action: {b.get('action')} | body: {str(b.get('body', ''))[:120]}")

# 11. Auto-reply detection
safe_print("\n  [..] POST /v1/reply (auto-reply detection)...")
s, b = req("POST", "/v1/reply", {
    "conversation_id": "conv_autoreply_test",
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "customer_id": None,
    "from_role": "merchant",
    "message": "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly.",
    "received_at": "2026-04-26T10:43:00Z",
    "turn_number": 1
})
ok("POST /v1/reply (auto-reply 1st time)", s, b)
safe_print(f"  action: {b.get('action')} (expected: wait)")

# 12. Hostile opt-out
safe_print("\n  [..] POST /v1/reply (hostile opt-out)...")
s, b = req("POST", "/v1/reply", {
    "conversation_id": "conv_hostile_test",
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "customer_id": None,
    "from_role": "merchant",
    "message": "Stop messaging me. Not interested.",
    "received_at": "2026-04-26T10:44:00Z",
    "turn_number": 1
})
ok("POST /v1/reply (hostile opt-out)", s, b)
safe_print(f"  action: {b.get('action')} (expected: end)")

# 13. Intent transition
safe_print("\n  [..] POST /v1/reply (intent transition: 'ok let's do it')...")
s, b = req("POST", "/v1/reply", {
    "conversation_id": conv_id,
    "merchant_id": "m_001_drmeera_dentist_delhi",
    "customer_id": None,
    "from_role": "merchant",
    "message": "Ok let's do it. What's next?",
    "received_at": "2026-04-26T10:45:00Z",
    "turn_number": 3
})
ok("POST /v1/reply (intent transition)", s, b)
safe_print(f"  action: {b.get('action')} | body: {str(b.get('body', ''))[:120]}")

print("\n=== All tests complete ===\n")
