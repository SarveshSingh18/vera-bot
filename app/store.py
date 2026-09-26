"""
Context store - idempotent, version-keyed storage (spec §2.1)
Key format: (scope, context_id) where scope is "category"|"merchant"|"customer"|"trigger"
"""
from typing import Dict, Tuple, Any, Optional
from datetime import datetime

class ContextStore:
    """
    Idempotent in-memory storage of contexts.
    
    Key insight from spec §2.1:
    - Same (scope, context_id, version) = idempotent (no-op)
    - Higher version for same (scope, context_id) = replace atomically
    - Stale version (lower/equal) = reject with 409
    """
    
    def __init__(self):
        self.contexts: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self.conversations: Dict[str, list] = {}
        self.sent_messages: Dict[str, list] = {}
    
    def push_context(self, scope: str, context_id: str, version: int, payload: dict, delivered_at: str) -> Tuple[bool, Optional[int]]:
        """
        Push context with version control.
        
        Returns:
            (accepted: bool, current_version: int or None)
            - accepted=True, current_version=None: successfully stored
            - accepted=False, current_version=N: stale version (spec §2.1 409 case)
        """
        key = (scope, context_id)
        
        if key in self.contexts:
            existing = self.contexts[key]
            existing_version = existing['version']
            
            if existing_version >= version:
                return False, existing_version
        
        self.contexts[key] = {
            'version': version,
            'payload': payload,
            'stored_at': datetime.utcnow().isoformat() + 'Z',
            'delivered_at': delivered_at
        }
        
        return True, None
    
    def get_context(self, scope: str, context_id: str) -> Optional[dict]:
        """Retrieve stored context payload"""
        key = (scope, context_id)
        if key in self.contexts:
            return self.contexts[key]['payload']
        return None
    
    def get_all_contexts_by_scope(self, scope: str) -> Dict[str, dict]:
        """Get all contexts for a given scope"""
        return {
            context_id: data['payload']
            for (s, context_id), data in self.contexts.items()
            if s == scope
        }
    
    def count_contexts(self) -> Dict[str, int]:
        """Count contexts by scope (for /v1/healthz)"""
        counts = {'category': 0, 'merchant': 0, 'customer': 0, 'trigger': 0}
        for (scope, _), _ in self.contexts.items():
            if scope in counts:
                counts[scope] += 1
        return counts
    
    def start_conversation(self, conversation_id: str, merchant_id: str, customer_id: Optional[str], trigger_id: str):
        """Start tracking a new conversation"""
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = []
        
        self.conversations[conversation_id].append({
            'turn': 0,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'action': 'init',
            'merchant_id': merchant_id,
            'customer_id': customer_id,
            'trigger_id': trigger_id
        })
    
    def add_reply(self, conversation_id: str, message: str, from_role: str, turn_number: int):
        """Add a reply to conversation history"""
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = []
        
        self.conversations[conversation_id].append({
            'turn': turn_number,
            'timestamp': datetime.utcnow().isoformat() + 'Z',
            'from': from_role,
            'message': message
        })
    
    def get_conversation(self, conversation_id: str) -> Optional[list]:
        """Get full conversation history"""
        return self.conversations.get(conversation_id)
    
    def end_conversation(self, conversation_id: str, reason: str = "normal"):
        """Mark conversation as ended"""
        if conversation_id in self.conversations:
            self.conversations[conversation_id].append({
                'turn': len(self.conversations[conversation_id]),
                'timestamp': datetime.utcnow().isoformat() + 'Z',
                'action': 'end',
                'reason': reason
            })

# Global store instance
store = ContextStore()