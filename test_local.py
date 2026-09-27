"""
Local sanity check — simulates a slice of the judge harness against your bot
BEFORE you deploy. Run the bot first, then run this script.

Terminal 1:
    cd bot
    uvicorn main:app --host 0.0.0.0 --port 8000

Terminal 2:
    export BOT_URL=http://localhost:8000   # optional, this is the default
    python test_local.py
"""

import json
import os
import time

import requests

BOT_URL = os.environ.get("BOT_URL", "http://localhost:8000")


def post(path, body):
    r = requests.post(f"{BOT_URL}{path}", json=body, timeout=10)
    print(f"POST {path} -> {r.status_code}")
    try:
        print(json.dumps(r.json(), indent=2, ensure_ascii=False))
    except Exception:
        print(r.text)
    print("-" * 60)
    return r


def get(path):
    r = requests.get(f"{BOT_URL}{path}", timeout=10)
    print(f"GET {path} -> {r.status_code}")
    print(json.dumps(r.json(), indent=2, ensure_ascii=False))
    print("-" * 60)
    return r


def main():
    # 1. liveness + identity
    get("/v1/healthz")
    get("/v1/metadata")

    # 2. push the "dentists" category context (this is the real payload shape
    #    from testing-brief.md §3.1 — trimmed to what our composers use)
    with open("data/dentists.json", "r", encoding="utf-8") as f:
        dentists = json.load(f)
    post("/v1/context", {
        "scope": "category", "context_id": "dentists", "version": 1,
        "payload": dentists, "delivered_at": "2026-04-26T10:00:00Z",
    })

    # 3. push a merchant context
    post("/v1/context", {
        "scope": "merchant", "context_id": "m_001_drmeera_dentist_delhi", "version": 1,
        "payload": {
            "merchant_id": "m_001_drmeera_dentist_delhi",
            "category_slug": "dentists",
            "identity": {"name": "Dr. Meera's Dental Clinic", "city": "Delhi",
                         "locality": "Lajpat Nagar", "verified": True, "languages": ["en", "hi"]},
            "subscription": {"status": "active", "plan": "Pro", "days_remaining": 82},
            "performance": {"window_days": 30, "views": 2410, "calls": 18, "directions": 45, "ctr": 0.021,
                             "delta_7d": {"views_pct": 0.18, "calls_pct": -0.05}},
            "offers": [],
            "conversation_history": [],
            "customer_aggregate": {"total_unique_ytd": 540, "lapsed_180d_plus": 78, "retention_6mo_pct": 0.38},
            "signals": ["high_risk_adult_cohort"],
        },
        "delivered_at": "2026-04-26T10:00:00Z",
    })

    # 4. push a customer context
    post("/v1/context", {
        "scope": "customer", "context_id": "c_001_priya_for_m001", "version": 1,
        "payload": {
            "customer_id": "c_001_priya_for_m001",
            "merchant_id": "m_001_drmeera_dentist_delhi",
            "identity": {"name": "Priya", "language_pref": "hi-en mix"},
            "relationship": {"first_visit": "2025-11-04", "last_visit": "2026-05-12", "visits_total": 4,
                              "services_received": ["cleaning", "cleaning", "whitening", "cleaning"]},
            "state": "lapsed_soft",
            "preferences": {"preferred_slots": "weekday_evening", "channel": "whatsapp"},
            "consent": {"opted_in_at": "2025-11-04", "scope": ["recall_reminders"]},
        },
        "delivered_at": "2026-04-26T10:00:00Z",
    })

    # 5. push three triggers: research_digest (merchant), regulation_change (merchant),
    #    recall_due (customer) — taken straight from triggers_seed.json
    triggers = [
        {
            "id": "trg_001_research_digest_dentists", "scope": "merchant", "kind": "research_digest",
            "source": "external", "merchant_id": "m_001_drmeera_dentist_delhi", "customer_id": None,
            "payload": {"category": "dentists", "top_item_id": "d_2026W17_jida_fluoride"},
            "urgency": 2, "suppression_key": "research:dentists:2026-W17", "expires_at": "2026-05-03T00:00:00Z",
        },
        {
            "id": "trg_002_compliance_dci_radiograph", "scope": "merchant", "kind": "regulation_change",
            "source": "external", "merchant_id": "m_001_drmeera_dentist_delhi", "customer_id": None,
            "payload": {"category": "dentists", "top_item_id": "d_2026W17_dci_radiograph", "deadline_iso": "2026-12-15"},
            "urgency": 4, "suppression_key": "compliance:dci_radiograph:2026", "expires_at": "2026-12-15T00:00:00Z",
        },
        {
            "id": "trg_003_recall_due_priya", "scope": "customer", "kind": "recall_due", "source": "internal",
            "merchant_id": "m_001_drmeera_dentist_delhi", "customer_id": "c_001_priya_for_m001",
            "payload": {"service_due": "6_month_cleaning", "last_service_date": "2026-05-12", "due_date": "2026-11-12",
                        "available_slots": [{"iso": "2026-11-05T18:00:00+05:30", "label": "Wed 5 Nov, 6pm"},
                                             {"iso": "2026-11-06T17:00:00+05:30", "label": "Thu 6 Nov, 5pm"}]},
            "urgency": 3, "suppression_key": "recall:c_001_priya_for_m001:6mo", "expires_at": "2026-11-30T00:00:00Z",
        },
    ]
    for t in triggers:
        post("/v1/context", {
            "scope": "trigger", "context_id": t["id"], "version": 1,
            "payload": t, "delivered_at": "2026-04-26T10:00:00Z",
        })

    time.sleep(0.2)

    # 6. tick — expect TWO actions: the merchant slot picks regulation_change
    #    (urgency 4 beats research_digest's urgency 2), and the customer slot
    #    (a different actor) gets recall_due independently.
    r = post("/v1/tick", {
        "now": "2026-04-26T10:30:00Z",
        "available_triggers": [t["id"] for t in triggers],
    })
    actions = r.json().get("actions", [])
    assert len(actions) == 2, f"expected 2 actions (one merchant slot + one customer slot), got {len(actions)}"
    kinds = sorted(a["trigger_id"] for a in actions)
    print("Actions returned for trigger_ids:", kinds)
    assert "trg_002_compliance_dci_radiograph" in kinds, "urgency-based selection failed: regulation_change should have won the merchant slot"
    assert "trg_003_recall_due_priya" in kinds, "customer-scoped trigger should get its own slot"

    # 7. tick again with the SAME available_triggers — should now return
    #    ZERO actions (suppression_key already used + actor already in an
    #    open conversation).
    r2 = post("/v1/tick", {
        "now": "2026-04-26T10:35:00Z",
        "available_triggers": [t["id"] for t in triggers],
    })
    assert r2.json().get("actions") == [], "second tick should be empty (dedupe + open-conversation guard)"

    # 8. reply flow: merchant accepts
    merchant_conv = next(a for a in actions if a["merchant_id"] == "m_001_drmeera_dentist_delhi")
    r3 = post("/v1/reply", {
        "conversation_id": merchant_conv["conversation_id"],
        "merchant_id": "m_001_drmeera_dentist_delhi", "customer_id": None,
        "from_role": "merchant", "message": "Yes sounds good, go ahead",
        "received_at": "2026-04-26T10:45:00Z", "turn_number": 2,
    })
    assert r3.json().get("action") == "send"

    # 9. auto-reply loop: same canned text 3x in a row -> bot should end
    for i in range(3):
        r4 = post("/v1/reply", {
            "conversation_id": merchant_conv["conversation_id"],
            "merchant_id": "m_001_drmeera_dentist_delhi", "customer_id": None,
            "from_role": "merchant", "message": "Thank you for your message. We will get back to you.",
            "received_at": "2026-04-26T10:50:00Z", "turn_number": 3 + i,
        })
    assert r4.json().get("action") == "end", "should detect the repeated canned auto-reply and end"

    print("\nAll local checks passed.")


if __name__ == "__main__":
    main()
