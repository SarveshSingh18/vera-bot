"""
Data models for Vera Bot - Match magicpin testing brief exactly
"""
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from datetime import datetime

# ============================================
# REQUEST MODELS
# ============================================

class ContextPushRequest(BaseModel):
    """POST /v1/context request body (spec §2.1)"""
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: str

class TickRequest(BaseModel):
    """POST /v1/tick request body (spec §2.2)"""
    now: str
    available_triggers: List[str] = []

class ReplyRequest(BaseModel):
    """POST /v1/reply request body (spec §2.3)"""
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str
    message: str
    received_at: str
    turn_number: int

# ============================================
# RESPONSE MODELS
# ============================================

class ContextAcceptedResponse(BaseModel):
    """POST /v1/context response (200) - spec §2.1"""
    accepted: bool = True
    ack_id: str
    stored_at: str

class ContextRejectedResponse(BaseModel):
    """POST /v1/context response (409) - version conflict - spec §2.1"""
    accepted: bool = False
    reason: str
    current_version: int

class ComposedAction(BaseModel):
    """Single action in /v1/tick response - spec §2.2"""
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str] = None
    send_as: str
    trigger_id: str
    template_name: str
    template_params: List[str]
    body: str
    cta: str
    suppression_key: str
    rationale: str

class TickResponse(BaseModel):
    """POST /v1/tick response (200) - spec §2.2"""
    actions: List[ComposedAction]

class ReplySendAction(BaseModel):
    """POST /v1/reply response type 1 - action: 'send' - spec §2.3"""
    action: str = "send"
    body: str
    cta: str
    rationale: str

class ReplyWaitAction(BaseModel):
    """POST /v1/reply response type 2 - action: 'wait' - spec §2.3"""
    action: str = "wait"
    wait_seconds: int
    rationale: str

class ReplyEndAction(BaseModel):
    """POST /v1/reply response type 3 - action: 'end' - spec §2.3"""
    action: str = "end"
    rationale: str

class HealthzResponse(BaseModel):
    """GET /v1/healthz response (200) - spec §2.4"""
    status: str
    uptime_seconds: int
    contexts_loaded: Dict[str, int]

class MetadataResponse(BaseModel):
    """GET /v1/metadata response (200) - spec §2.5"""
    team_name: str
    team_members: List[str]
    model: str
    approach: str
    contact_email: str
    version: str
    submitted_at: str