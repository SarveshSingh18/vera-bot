"""
Composer - Decision engine that uses 4 contexts: category, merchant, trigger, customer
Spec §4 (challenge-brief.md)
"""
from typing import Dict, Any, Optional, List

class Composer:
    """
    Takes (category, merchant, trigger, customer) → ComposedAction
    
    From testing brief §2.2, output format:
    {
        conversation_id: str,
        merchant_id: str,
        customer_id: Optional[str],
        send_as: str,
        trigger_id: str,
        template_name: str,
        template_params: List[str],
        body: str,
        cta: str,
        suppression_key: str,
        rationale: str
    }
    """
    
    def compose(self,
                trigger_id: str,
                merchant: Dict[str, Any],
                customer: Optional[Dict[str, Any]],
                trigger: Dict[str, Any],
                category: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Main composition function.
        Returns a composed action or None if nothing worth sending.
        """
        
        merchant_id = merchant.get('id') or merchant.get('merchant_id', 'unknown')
        merchant_name = merchant.get('identity', {}).get('name', 'Partner')
        category_slug = merchant.get('category_slug', 'general')
        
        customer_id = customer.get('id') if customer else None
        customer_name = customer.get('identity', {}).get('name', 'Friend') if customer else None
        
        trigger_type = trigger.get('type', 'unknown')
        trigger_kind = trigger.get('kind', 'unknown')
        
        if self._should_suppress(merchant, trigger, customer):
            return None
        
        message_data = self._build_by_trigger_type(
            trigger_type, trigger_kind, merchant, customer, trigger, category
        )
        
        if not message_data:
            return None
        
        conversation_id = f"conv_{merchant_id}_{trigger_id}"
        suppression_key = self._make_suppression_key(category_slug, trigger_type, merchant_id)
        
        return {
            'conversation_id': conversation_id,
            'merchant_id': merchant_id,
            'customer_id': customer_id,
            'send_as': message_data.get('send_as', 'vera'),
            'trigger_id': trigger_id,
            'template_name': message_data.get('template_name', 'generic_v1'),
            'template_params': message_data.get('template_params', []),
            'body': message_data['body'],
            'cta': message_data.get('cta', 'Learn More'),
            'suppression_key': suppression_key,
            'rationale': message_data.get('rationale', 'Composed from context')
        }
    
    def _should_suppress(self, merchant: Dict, trigger: Dict, customer: Optional[Dict]) -> bool:
        """Decide if we should skip this one"""
        if trigger.get('priority') == 'low':
            return True
        
        if merchant.get('opted_out_triggers', []):
            if trigger.get('type') in merchant.get('opted_out_triggers', []):
                return True
        
        return False
    
    def _build_by_trigger_type(self,
                                trigger_type: str,
                                trigger_kind: str,
                                merchant: Dict,
                                customer: Optional[Dict],
                                trigger: Dict,
                                category: Dict) -> Optional[Dict[str, Any]]:
        """
        Build message based on trigger type.
        This is where category-specific logic goes.
        """
        
        merchant_name = merchant.get('identity', {}).get('name', 'Partner')
        customer_name = customer.get('identity', {}).get('name', 'Friend') if customer else None
        category_name = category.get('slug', 'general')
        
        if trigger_kind == 'research':
            return self._build_research_alert(merchant, customer, trigger, category)
        
        elif trigger_kind == 'offer':
            return self._build_offer_message(merchant, customer, trigger, category)
        
        elif trigger_kind == 'surge':
            return self._build_surge_alert(merchant, customer, trigger, category)
        
        elif trigger_kind == 'seasonal':
            return self._build_seasonal_message(merchant, customer, trigger, category)
        
        else:
            return self._build_generic(merchant, customer, trigger, category)
    
    def _build_research_alert(self, merchant: Dict, customer: Optional[Dict], trigger: Dict, category: Dict) -> Dict[str, Any]:
        """Research digest / educational alert"""
        merchant_name = merchant.get('identity', {}).get('name', 'Partner')
        digest_item = trigger.get('digest_item', {})
        title = digest_item.get('title', 'New research available')
        summary = digest_item.get('summary', 'Check the latest findings')
        
        body = f"Hi {merchant_name}, \n\n{title}\n\n{summary}\n\nThis research may be relevant to your practice."
        
        return {
            'body': body,
            'cta': 'View Research',
            'send_as': 'vera',
            'template_name': 'research_digest_v1',
            'template_params': [merchant_name, title, summary],
            'rationale': f"Research alert: {title} for {merchant_name}"
        }
    
    def _build_offer_message(self, merchant: Dict, customer: Optional[Dict], trigger: Dict, category: Dict) -> Dict[str, Any]:
        """Offer expiry or promotional alert"""
        merchant_name = merchant.get('identity', {}).get('name', 'Partner')
        offer_name = trigger.get('offer_name', 'Special Offer')
        expiry = trigger.get('expiry_date', 'Soon')
        
        body = f"Hi {merchant_name}, \n\nYour offer '{offer_name}' expires on {expiry}. Promote it before time runs out!"
        
        return {
            'body': body,
            'cta': 'Promote Now',
            'send_as': 'vera',
            'template_name': 'offer_expiry_v1',
            'template_params': [merchant_name, offer_name, expiry],
            'rationale': f"Offer expiry alert: {offer_name}"
        }
    
    def _build_surge_alert(self, merchant: Dict, customer: Optional[Dict], trigger: Dict, category: Dict) -> Dict[str, Any]:
        """Demand surge or performance signal"""
        merchant_name = merchant.get('identity', {}).get('name', 'Partner')
        metric = trigger.get('metric_name', 'Traffic')
        value = trigger.get('metric_value', 'increased')
        
        body = f"Hi {merchant_name}, \n\nYour {metric} has {value} significantly! This is a great time to upsell premium services."
        
        return {
            'body': body,
            'cta': 'View Insights',
            'send_as': 'vera',
            'template_name': 'surge_alert_v1',
            'template_params': [merchant_name, metric, value],
            'rationale': f"Performance surge: {metric} for {merchant_name}"
        }
    
    def _build_seasonal_message(self, merchant: Dict, customer: Optional[Dict], trigger: Dict, category: Dict) -> Dict[str, Any]:
        """Seasonal or time-sensitive opportunity"""
        merchant_name = merchant.get('identity', {}).get('name', 'Partner')
        season = trigger.get('season', 'Now')
        opportunity = trigger.get('opportunity', 'a seasonal opportunity')
        
        body = f"Hi {merchant_name}, \n\n{season} is the perfect time for {opportunity}. Prepare your inventory and marketing!"
        
        return {
            'body': body,
            'cta': 'Get Ready',
            'send_as': 'vera',
            'template_name': 'seasonal_v1',
            'template_params': [merchant_name, season, opportunity],
            'rationale': f"Seasonal opportunity: {opportunity}"
        }
    
    def _build_generic(self, merchant: Dict, customer: Optional[Dict], trigger: Dict, category: Dict) -> Dict[str, Any]:
        """Generic fallback when trigger type is unknown"""
        merchant_name = merchant.get('identity', {}).get('name', 'Partner')
        trigger_type = trigger.get('type', 'update')
        
        body = f"Hi {merchant_name}, \n\nWe have an update that might be valuable for your business. Check it out!"
        
        return {
            'body': body,
            'cta': 'Learn More',
            'send_as': 'vera',
            'template_name': 'generic_v1',
            'template_params': [merchant_name],
            'rationale': f"Generic message for trigger type: {trigger_type}"
        }
    
    def _make_suppression_key(self, category: str, trigger_type: str, merchant_id: str) -> str:
        """Create suppression key for tracking duplicate sends"""
        return f"{trigger_type}:{category}:{merchant_id}"

# Global composer instance
composer = Composer()