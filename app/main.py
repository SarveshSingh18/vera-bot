"""
Vera Bot - FastAPI HTTP server
Testing Brief: https://...testing-brief.md
All 5 endpoints implemented per spec §2
"""

import time
from datetime import datetime
from typing import Optional, Union
from fastapi import FastAPI, HTTPException, status

from app.models import (
    ContextPushRequest, ContextAcceptedResponse, ContextRejectedResponse,
    TickRequest, TickResponse, ComposedAction,
    ReplyRequest, ReplySendAction, ReplyWaitAction, ReplyEndAction,
    HealthzResponse, MetadataResponse
)
from app.store import store
from app.composer import composer

# ============================================
# FastAPI App Setup
# ============================================

app = FastAPI(
    title="Vera Bot - magicpin AI Challenge",
    version="1.0.0",
    description="LLM-powered merchant engagement bot"
)

# Track startup time for healthz
START_TIME = time.time()

# ============================================
# ENDPOINT 1: POST /v1/context
# (Spec §2.1)
# ============================================

@app.post(
    "/v1/context",
    response_model=Union[ContextAcceptedResponse, ContextRejectedResponse],
    status_code=200
)
async def push_context(body: ContextPushRequest):
    """
    Receive context push from judge.
    
    Idempotent on (scope, context_id, version).
    Returns 200 with accepted=false if version is stale (judge handles like 409).
    
    Spec §2.1
    """
    
    # Validate scope
    valid_scopes = {"category", "merchant", "customer", "trigger"}
    if body.scope not in valid_scopes:
        return ContextRejectedResponse(
            accepted=False,
            reason="invalid_scope",
            current_version=0
        )
    
    # Try to store (returns accepted: bool, current_version: Optional[int])
    accepted, current_version = store.push_context(
        scope=body.scope,
        context_id=body.context_id,
        version=body.version,
        payload=body.payload,
        delivered_at=body.delivered_at
    )
    
    if not accepted:
        # Stale version
        return ContextRejectedResponse(
            accepted=False,
            reason="stale_version",
            current_version=current_version
        )
    
    # Success
    return ContextAcceptedResponse(
        accepted=True,
        ack_id=f"ack_{body.context_id}_v{body.version}",
        stored_at=datetime.utcnow().isoformat() + 'Z'
    )

# ============================================
# ENDPOINT 2: POST /v1/tick
# (Spec §2.2)
# ============================================

@app.post("/v1/tick", response_model=TickResponse)
async def tick(body: TickRequest):
    """
    Periodic wake-up: bot composes messages for available triggers.
    
    Judge provides:
    - now: current simulated time
    - available_triggers: list of trigger context_ids to consider
    
    Bot returns:
    - actions: list of composed messages (can be empty)
    
    Spec §2.2
    """
    
    actions = []
    
    # For each available trigger
    for trigger_id in body.available_triggers:
        # Get trigger payload
        trigger = store.get_context("trigger", trigger_id)
        if not trigger:
            continue
        
        # Get merchant (trigger.merchant_id → look up merchant context)
        merchant_id = trigger.get("merchant_id")
        if not merchant_id:
            continue
        
        merchant = store.get_context("merchant", merchant_id)
        if not merchant:
            continue
        
        # Get category (merchant.category_slug → look up category context)
        category_slug = merchant.get("category_slug")
        if not category_slug:
            continue
        
        category = store.get_context("category", category_slug)
        if not category:
            continue
        
        # Get customer (if specified in trigger)
        customer_id = trigger.get("customer_id")
        customer = None
        if customer_id:
            customer = store.get_context("customer", customer_id)
        
        # Call composer with all 4 contexts
        action_dict = composer.compose(
            trigger_id=trigger_id,
            merchant=merchant,
            customer=customer,
            trigger=trigger,
            category=category
        )
        
        if action_dict:
            # Track conversation in store
            store.start_conversation(
                conversation_id=action_dict['conversation_id'],
                merchant_id=merchant_id,
                customer_id=customer_id,
                trigger_id=trigger_id
            )
            
            # Convert dict to ComposedAction model (validates schema)
            action = ComposedAction(**action_dict)
            actions.append(action)
    
    return TickResponse(actions=actions)

# ============================================
# ENDPOINT 3: POST /v1/reply
# (Spec §2.3)
# ============================================

@app.post("/v1/reply")
async def reply(body: ReplyRequest) -> Union[ReplySendAction, ReplyWaitAction, ReplyEndAction]:
    """
    Handle reply from merchant/customer.
    
    Judge sends:
    - conversation_id: existing conversation
    - message: merchant's reply
    - from_role: who sent it ("merchant" | "customer")
    - turn_number: turn count
    
    Bot must respond with action: "send" | "wait" | "end"
    
    Spec §2.3 - Must respond within 30 seconds
    """
    
    # Track the reply
    store.add_reply(
        conversation_id=body.conversation_id,
        message=body.message,
        from_role=body.from_role,
        turn_number=body.turn_number
    )
    
    message_lower = body.message.lower()
    
    # ---- RULE 1: Detect explicit rejection ----
    hard_declines = ["stop", "unsubscribe", "spam", "not interested", "leave me alone", "blocked"]
    if any(word in message_lower for word in hard_declines):
        store.end_conversation(body.conversation_id, reason="merchant_declined")
        return ReplyEndAction(
            action="end",
            rationale="Merchant explicitly asked to stop. Respecting preference."
        )
    
    # ---- RULE 2: Detect auto-reply ----
    auto_reply_indicators = ["out of office", "vacation", "auto-reply", "on leave", "unavailable"]
    if any(indicator in message_lower for indicator in auto_reply_indicators):
        # Wait before retrying
        return ReplyWaitAction(
            action="wait",
            wait_seconds=3600,
            rationale="Merchant appears to be out of office. Will retry later."
        )
    
    # ---- RULE 3: Detect interest/commitment ----
    interest_signals = ["yes", "tell me more", "send it", "how much", "pricing", "interested", "let's"]
    if any(signal in message_lower for signal in interest_signals):
        next_message = (
            f"Great! Here's what we recommend next: "
            f"We can help you optimize your strategy with our premium tools. "
            f"Would you like a personalized demo?"
        )
        return ReplySendAction(
            action="send",
            body=next_message,
            cta="Schedule Demo",
            rationale="Merchant showed interest. Escalating to next step with clear CTA."
        )
    
    # ---- RULE 4: Detect clarifying questions ----
    question_indicators = ["?", "what", "how", "when", "where", "why"]
    if any(qi in message_lower for qi in question_indicators):
        next_message = (
            "Great question! We're here to help. "
            "Can you share more about your specific needs so we can provide a tailored solution?"
        )
        return ReplySendAction(
            action="send",
            body=next_message,
            cta="Tell Us More",
            rationale="Merchant asked for clarification. Continuing engagement with open question."
        )
    
    # ---- RULE 5: Default - wait for more signals ----
    return ReplyWaitAction(
        action="wait",
        wait_seconds=1800,
        rationale="Merchant's intent is unclear. Will wait for further signals before continuing."
    )

# ============================================
# ENDPOINT 4: GET /v1/healthz
# (Spec §2.4)
# ============================================

@app.get("/v1/healthz", response_model=HealthzResponse)
async def healthz():
    """
    Liveness probe. Judge calls this every 60s.
    Three consecutive failures = bot disqualified.
    
    Returns:
    - status: "ok"
    - uptime_seconds: seconds since bot started
    - contexts_loaded: count of each context type
    
    Spec §2.4
    """
    
    uptime = int(time.time() - START_TIME)
    contexts_loaded = store.count_contexts()
    
    return HealthzResponse(
        status="ok",
        uptime_seconds=uptime,
        contexts_loaded=contexts_loaded
    )

# ============================================
# ENDPOINT 5: GET /v1/metadata
# (Spec §2.5)
# ============================================

@app.get("/v1/metadata", response_model=MetadataResponse)
async def metadata():
    """
    Bot identity and approach.
    
    Required fields:
    - team_name
    - team_members
    - model (LLM used or approach)
    - approach (strategy description)
    - contact_email
    - version
    - submitted_at (ISO timestamp)
    
    Spec §2.5
    """
    
    return MetadataResponse(
        team_name="Vera Bot Team",
        team_members=["Developer"],
        model="rule-based-composer",
        approach="Deterministic rule-based composer with 4-context framework (category, merchant, trigger, customer). No LLM for determinism and speed. Trigger-type-specific templates filled with real data only.",
        contact_email="developer@example.com",
        version="1.0.0",
        submitted_at=datetime.utcnow().isoformat() + 'Z'
    )

# ============================================
# Optional: POST /v1/teardown
# (Spec §11 - privacy)
# ============================================

@app.post("/v1/teardown")
async def teardown():
    """
    Optional: Judge calls at end of test to clean up state.
    Bot must wipe all context data per spec §11 (privacy).
    """
    # Clear all stored contexts
    store.contexts.clear()
    store.conversations.clear()
    store.sent_messages.clear()
    
    return {"status": "cleaned", "timestamp": datetime.utcnow().isoformat() + 'Z'}

# ============================================
# Root endpoint (for testing)
# ============================================

@app.get("/")
async def root():
    return {
        "name": "Vera Bot",
        "status": "running",
        "docs": "/docs",
        "health": "/v1/healthz"
    }

# ============================================
# Run locally
# ============================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)