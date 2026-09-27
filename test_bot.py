#!/usr/bin/env python3
"""
Test script - verify bot endpoints work correctly
Run this while bot is running: python -m uvicorn app.main:app --reload
"""

import requests
import json
from datetime import datetime

BASE_URL = "http://localhost:8000"

def test_healthz():
    """Test GET /v1/healthz"""
    print("\n" + "="*60)
    print("TEST 1: GET /v1/healthz")
    print("="*60)
    
    resp = requests.get(f"{BASE_URL}/v1/healthz")
    print(f"Status: {resp.status_code}")
    print(json.dumps(resp.json(), indent=2))
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
    print("✅ PASSED")

def test_metadata():
    """Test GET /v1/metadata"""
    print("\n" + "="*60)
    print("TEST 2: GET /v1/metadata")
    print("="*60)
    
    resp = requests.get(f"{BASE_URL}/v1/metadata")
    print(f"Status: {resp.status_code}")
    print(json.dumps(resp.json(), indent=2))
    assert resp.status_code == 200
    assert "team_name" in resp.json()
    print("✅ PASSED")

def test_context_push():
    """Test POST /v1/context"""
    print("\n" + "="*60)
    print("TEST 3: POST /v1/context (Push category context)")
    print("="*60)
    
    payload = {
        "scope": "category",
        "context_id": "test_category_1",
        "version": 1,
        "payload": {
            "slug": "dentists",
            "offer_catalog": [{"title": "Cleaning", "value": "299"}],
            "voice": {"tone": "professional"}
        },
        "delivered_at": datetime.utcnow().isoformat() + 'Z'
    }
    
    print("Pushing context:")
    print(json.dumps(payload, indent=2))
    
    resp = requests.post(f"{BASE_URL}/v1/context", json=payload)
    print(f"\nStatus: {resp.status_code}")
    print(json.dumps(resp.json(), indent=2))
    
    assert resp.status_code == 200
    assert resp.json()["accepted"] == True
    assert "ack_id" in resp.json()
    print("✅ PASSED")

def test_context_stale_version():
    """Test stale version rejection"""
    print("\n" + "="*60)
    print("TEST 4: POST /v1/context (Stale version - should reject)")
    print("="*60)
    
    payload = {
        "scope": "category",
        "context_id": "test_category_1",
        "version": 1,
        "payload": {"slug": "dentists"},
        "delivered_at": datetime.utcnow().isoformat() + 'Z'
    }
    
    resp = requests.post(f"{BASE_URL}/v1/context", json=payload)
    print(f"Status: {resp.status_code}")
    print(json.dumps(resp.json(), indent=2))
    
    assert resp.status_code == 200
    assert resp.json()["accepted"] == False
    assert resp.json()["reason"] == "stale_version"
    print("✅ PASSED - Correctly rejected stale version")

def test_push_merchant_and_trigger():
    """Push merchant and trigger contexts"""
    print("\n" + "="*60)
    print("TEST 5: POST /v1/context (Push merchant + trigger)")
    print("="*60)
    
    merchant_payload = {
        "scope": "merchant",
        "context_id": "m_dentist_001",
        "version": 1,
        "payload": {
            "id": "m_dentist_001",
            "category_slug": "test_category_1",
            "identity": {
                "name": "Dr. Smith's Dental Clinic"
            },
            "opted_out_triggers": []
        },
        "delivered_at": datetime.utcnow().isoformat() + 'Z'
    }
    
    resp = requests.post(f"{BASE_URL}/v1/context", json=merchant_payload)
    print(f"Merchant push - Status: {resp.status_code}")
    assert resp.status_code == 200
    assert resp.json()["accepted"] == True
    print("✅ Merchant context stored")
    
    trigger_payload = {
        "scope": "trigger",
        "context_id": "trg_research_001",
        "version": 1,
        "payload": {
            "id": "trg_research_001",
            "type": "external_research_alert",
            "kind": "research",
            "merchant_id": "m_dentist_001",
            "priority": "high",
            "digest_item": {
                "title": "New fluoride research",
                "summary": "Latest findings on fluoride treatments"
            }
        },
        "delivered_at": datetime.utcnow().isoformat() + 'Z'
    }
    
    resp = requests.post(f"{BASE_URL}/v1/context", json=trigger_payload)
    print(f"Trigger push - Status: {resp.status_code}")
    assert resp.status_code == 200
    assert resp.json()["accepted"] == True
    print("✅ Trigger context stored")

def test_tick():
    """Test POST /v1/tick"""
    print("\n" + "="*60)
    print("TEST 6: POST /v1/tick (Compose messages)")
    print("="*60)
    
    payload = {
        "now": datetime.utcnow().isoformat() + 'Z',
        "available_triggers": ["trg_research_001"]
    }
    
    print("Tick request:")
    print(json.dumps(payload, indent=2))
    
    resp = requests.post(f"{BASE_URL}/v1/tick", json=payload)
    print(f"\nStatus: {resp.status_code}")
    print(json.dumps(resp.json(), indent=2))
    
    assert resp.status_code == 200
    data = resp.json()
    assert "actions" in data
    
    if data["actions"]:
        action = data["actions"][0]
        print("\n✅ Bot composed a message!")
        print(f"  Merchant: {action.get('merchant_id')}")
        print(f"  CTA: {action.get('cta')}")
        print(f"  Rationale: {action.get('rationale')}")
        
        required_fields = ['conversation_id', 'merchant_id', 'send_as', 'trigger_id', 
                          'template_name', 'template_params', 'body', 'cta', 
                          'suppression_key', 'rationale']
        for field in required_fields:
            assert field in action, f"Missing required field: {field}"
        
        print("✅ All required fields present")
    else:
        print("✅ Bot chose not to send (returned empty actions list)")

def test_reply():
    """Test POST /v1/reply"""
    print("\n" + "="*60)
    print("TEST 7: POST /v1/reply (Handle merchant reply)")
    print("="*60)
    
    test_cases = [
        ("Yes, send it!", "Should send next message"),
        ("Stop messaging me", "Should end conversation"),
        ("Out of office", "Should wait")
    ]
    
    for i, (message, expected) in enumerate(test_cases):
        payload = {
            "conversation_id": f"conv_test_{i}",
            "merchant_id": "m_dentist_001",
            "customer_id": None,
            "from_role": "merchant",
            "message": message,
            "received_at": datetime.utcnow().isoformat() + 'Z',
            "turn_number": 2
        }
        
        print(f"\nCase {i+1}: '{message}'")
        print(f"Expected: {expected}")
        
        resp = requests.post(f"{BASE_URL}/v1/reply", json=payload)
        print(f"Status: {resp.status_code}")
        
        data = resp.json()
        print(f"Bot action: {data.get('action')}")
        print(f"Rationale: {data.get('rationale')}")
        
        assert resp.status_code == 200
        assert "action" in data
        assert data["action"] in ["send", "wait", "end"]
        print("✅ Valid response")

def main():
    print("\n" + "="*60)
    print("VERA BOT - LOCAL TEST SUITE")
    print("="*60)
    
    try:
        test_healthz()
        test_metadata()
        test_context_push()
        test_context_stale_version()
        test_push_merchant_and_trigger()
        test_tick()
        test_reply()
        
        print("\n" + "="*60)
        print("✅ ALL TESTS PASSED!")
        print("="*60)
        print("\nBot is working correctly.")
        print("Next steps:")
        print("1. Push to GitHub")
        print("2. Deploy to Railway")
        print("3. Test against judge_simulator.py")
        print("4. Submit to magicpin")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        return 1
    
    return 0

if __name__ == "__main__":
    import sys
    sys.exit(main())