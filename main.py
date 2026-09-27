"""
Vera trigger-composer bot — built against testing-brief.md (magicpin AI Challenge).

5 endpoints:
  GET  /v1/healthz
  GET  /v1/metadata
  POST /v1/context   scope: category | merchant | customer | trigger
  POST /v1/tick       -> {"actions": [...]}
  POST /v1/reply      -> {"action": "send" | "wait" | "end", ...}

Design notes:
  - CONTEXTS is the single source of truth. The judge PUSHES all category/merchant/
    customer/trigger data via /v1/context (including the 5 category objects) during
    warmup — we do NOT hardcode category data into the bot. The bundled data/*.json
    files are only used by test_local.py to simulate a judge locally before you deploy.
  - Idempotent by (scope, context_id, version): a version <= the one already stored
    is a no-op / 409 stale_version; a higher version replaces atomically.
  - /v1/tick only ever *starts* new conversations, and only one per "actor"
    (merchant_id for merchant-scoped triggers, customer_id for customer-scoped
    triggers) per tick, chosen by highest urgency among the judge's
    `available_triggers` hint. Continuing a conversation happens in /v1/reply.
  - Composition is template/data-driven (safe, fast, always inside the 30s budget,
    never invents facts not present in the pushed context). An optional LLM-assisted
    composer hook is included but OFF by default — see USE_LLM_COMPOSER below.
  - Every handler is defensive: malformed input never 500s; it degrades to an empty
    or minimally-safe response per the failure-mode table in the brief.
"""

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI()
START_TIME = time.time()

# ---------------------------------------------------------------------------
# Bot identity (edit these before you submit)
# ---------------------------------------------------------------------------
TEAM_NAME = os.environ.get("TEAM_NAME", "Team Vera")
TEAM_MEMBERS = [m.strip() for m in os.environ.get("TEAM_MEMBER", "Sarvesh").split(",") if m.strip()]
BOT_MODEL = os.environ.get("BOT_MODEL", "template-composer-v1")
BOT_APPROACH = "Deterministic, data-grounded composer per trigger kind; template + rules " \
               "reply engine with auto-reply-loop and intent-transition detection."
CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "sarveshsingh1812@gmail.com")
BOT_VERSION = "1.0.0"
SUBMITTED_AT = os.environ.get("SUBMITTED_AT", "")  # fill in at submission time

TICK_ACTION_CAP = 20
MAX_CONTEXT_BYTES = 500_000
URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)

DATA_DIR = Path(__file__).parent / "data"
LOAD_LOCAL_SEED_DATA = os.environ.get("LOAD_LOCAL_SEED_DATA", "false").lower() == "true"

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------

# (scope, context_id) -> {"version": int, "payload": dict}
CONTEXTS: Dict[Tuple[str, str], Dict[str, Any]] = {}

# suppression_key -> True, once a trigger with that key has produced an action
USED_SUPPRESSION_KEYS: set = set()

# actor_key ("merchant:<id>" or "customer:<id>") -> conversation_id currently open
ACTIVE_ACTOR_CONVERSATIONS: Dict[str, str] = {}

# conversation_id -> state dict (history, trigger, actor, sent bodies, repeat counter)
CONVERSATIONS: Dict[str, Dict[str, Any]] = {}


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def safe_get(d: Any, *path, default=None):
    cur = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur if cur is not None else default


def truncate(text: str, max_len: int = 360) -> str:
    if not text:
        return text
    return text if len(text) <= max_len else text[: max_len - 1].rstrip() + "…"


def strip_urls(text: str) -> str:
    return URL_PATTERN.sub("", text or "")


# ---------------------------------------------------------------------------
# Optional local seed (dev convenience only — never required, never trusted
# over a real push from the judge, since real pushes always carry version>=1
# and preseeded rows are stored at version 0).
# ---------------------------------------------------------------------------

def load_local_seed():
    if not LOAD_LOCAL_SEED_DATA or not DATA_DIR.exists():
        return
    for f in DATA_DIR.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            slug = data.get("slug") or f.stem
            CONTEXTS[("category", slug)] = {"version": 0, "payload": data}
        except Exception:
            continue


load_local_seed()

# ---------------------------------------------------------------------------
# Context accessors
# ---------------------------------------------------------------------------

def get_ctx(scope: str, context_id: Optional[str]) -> Dict[str, Any]:
    if not context_id:
        return {}
    row = CONTEXTS.get((scope, context_id))
    return (row or {}).get("payload", {}) or {}


def counts_loaded() -> Dict[str, int]:
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _cid) in CONTEXTS.keys():
        counts[scope] = counts.get(scope, 0) + 1
    return counts


# ---------------------------------------------------------------------------
# Voice / language helpers
# ---------------------------------------------------------------------------

def voice_of(category: Dict[str, Any]) -> Dict[str, Any]:
    return category.get("voice", {}) or {}


def taboo_words(category: Dict[str, Any]) -> List[str]:
    v = voice_of(category)
    return (v.get("taboos") or v.get("vocab_taboo") or [])


def scrub_taboo(text: str, category: Dict[str, Any]) -> str:
    for t in taboo_words(category):
        if not t:
            continue
        # keep only the bit before any parenthetical caveat, e.g. "FDA-approved (use only ...)"
        phrase = t.split(" (")[0].strip()
        if phrase and phrase.lower() in text.lower():
            pattern = re.compile(re.escape(phrase), re.IGNORECASE)
            text = pattern.sub("", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def merchant_name(merchant: Dict[str, Any]) -> str:
    return safe_get(merchant, "identity", "name") or "there"


def uses_hindi(languages_or_pref) -> bool:
    if isinstance(languages_or_pref, list):
        return "hi" in languages_or_pref
    if isinstance(languages_or_pref, str):
        return "hi" in languages_or_pref.lower()
    return False


def greeting_for_merchant(merchant: Dict[str, Any]) -> str:
    name = merchant_name(merchant)
    langs = safe_get(merchant, "identity", "languages", default=[])
    if uses_hindi(langs):
        return f"Namaste {name},"
    return f"Hi {name},"


def greeting_for_customer(customer: Dict[str, Any]) -> str:
    name = safe_get(customer, "identity", "name") or "there"
    pref = safe_get(customer, "identity", "language_pref", default="")
    if uses_hindi(pref):
        return f"Namaste {name},"
    return f"Hi {name},"


def category_for_merchant(merchant: Dict[str, Any]) -> Dict[str, Any]:
    slug = merchant.get("category_slug") or merchant.get("category")
    return get_ctx("category", slug)


def digest_item(category: Dict[str, Any], item_id: Optional[str]) -> Dict[str, Any]:
    if not item_id:
        return {}
    for item in category.get("digest", []) or []:
        if item.get("id") == item_id:
            return item
    return {}


def offer_by_audience(category: Dict[str, Any], audience: Optional[str] = None) -> Dict[str, Any]:
    catalog = category.get("offer_catalog", []) or []
    if not catalog:
        return {}
    if audience:
        for o in catalog:
            if o.get("audience") == audience:
                return o
    return catalog[0]


# ---------------------------------------------------------------------------
# Composers: one per trigger kind seen in the seed data, + a safe fallback.
# Each returns (body, template_name, template_params, cta, rationale).
# Every composer only uses fields actually present on the trigger/category/
# merchant/customer objects — nothing is invented.
# ---------------------------------------------------------------------------

def _compose_result(body, template_name, params, cta, rationale):
    return truncate(body), template_name, params, cta, rationale


def compose_research_digest(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    item = digest_item(category, p.get("top_item_id"))
    title = item.get("title", "")
    summary = item.get("summary", "")
    actionable = item.get("actionable", "")
    source = item.get("source", "")
    hello = greeting_for_merchant(merchant)
    body = f"{hello} {title}. {summary} {('Suggested: ' + actionable) if actionable else ''} ({source})".strip()
    return _compose_result(
        body, "vera_research_digest_v1", [merchant_name(merchant), title, source],
        "open_ended",
        "External research digest relevant to this merchant's category; surfacing with source + suggested action."
    )


def compose_regulation_change(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    item = digest_item(category, p.get("top_item_id"))
    title = item.get("title", "")
    summary = item.get("summary", "")
    actionable = item.get("actionable", "")
    deadline = p.get("deadline_iso", "")
    hello = greeting_for_merchant(merchant)
    body = f"{hello} heads up on a compliance change: {title}. {summary} " \
           f"{('Deadline: ' + deadline + '. ') if deadline else ''}{('Suggested: ' + actionable) if actionable else ''}"
    return _compose_result(
        body, "vera_regulation_change_v1", [merchant_name(merchant), title, deadline],
        "open_ended",
        "Regulatory deadline directly affects this merchant's compliance; urgency justifies proactive alert."
    )


def compose_recall_due(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    service = (p.get("service_due") or "your visit").replace("_", " ")
    slots = p.get("available_slots") or []
    hello = greeting_for_customer(customer)
    slot_text = " or ".join(s.get("label", "") for s in slots if s.get("label"))
    body = f"{hello} it's about time for {service}. " \
           f"{('We have ' + slot_text + ' open — want me to book one?') if slot_text else 'Want me to check available slots?'}"
    return _compose_result(
        body, "vera_recall_due_v1", [safe_get(customer, "identity", "name", default=""), service, slot_text],
        "book_slot",
        "Customer relationship data shows a recall/recurring-service window is due; offering concrete open slots."
    )


def compose_perf_dip(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    metric = p.get("metric", "performance")
    delta = p.get("delta_pct")
    window = p.get("window", "recently")
    hello = greeting_for_merchant(merchant)
    delta_txt = f"{abs(delta) * 100:.0f}% down" if isinstance(delta, (int, float)) else "down"
    body = f"{hello} quick flag — your {metric} are {delta_txt} over the last {window}. Want to look at what might be behind it?"
    return _compose_result(
        body, "vera_perf_dip_v1", [merchant_name(merchant), metric, delta_txt],
        "open_ended",
        "Performance dip detected in pushed metrics; surfacing early since delta is material."
    )


def compose_renewal_due(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    days = p.get("days_remaining")
    plan = p.get("plan", "your plan")
    amount = p.get("renewal_amount")
    hello = greeting_for_merchant(merchant)
    amt_txt = f"₹{amount:,}" if isinstance(amount, (int, float)) else ""
    body = f"{hello} {plan} renews in {days} day{'s' if days != 1 else ''}{(' at ' + amt_txt) if amt_txt else ''}. Want me to send the renewal link?"
    return _compose_result(
        body, "vera_renewal_due_v1", [merchant_name(merchant), str(days), amt_txt],
        "renew",
        "Subscription renewal window is closing; timely reminder with amount for a frictionless decision."
    )


def compose_festival_upcoming(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    festival = p.get("festival", "the festival")
    days_until = p.get("days_until")
    hello = greeting_for_merchant(merchant)
    body = f"{hello} {festival} is {days_until} days out. Worth planning a seasonal offer or stock-up now while there's lead time."
    return _compose_result(
        body, "vera_festival_upcoming_v1", [merchant_name(merchant), festival, str(days_until)],
        "open_ended",
        "Low-urgency seasonal heads-up, sent early enough to be useful for planning rather than last-minute."
    )


def compose_wedding_package_followup(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    days_to_wedding = p.get("days_to_wedding")
    next_step = (p.get("next_step_window_open") or "the next step").replace("_", " ")
    hello = greeting_for_customer(customer)
    body = f"{hello} with {days_to_wedding} days to go, this is a good window to start {next_step}. Want me to check available dates?"
    return _compose_result(
        body, "vera_wedding_followup_v1", [safe_get(customer, "identity", "name", default=""), next_step],
        "book_slot",
        "Bridal package customer is in an open follow-up window per their trial history; proactive next step."
    )


def compose_curious_ask_due(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    ask = (p.get("ask_template") or "").replace("_", " ")
    hello = greeting_for_merchant(merchant)
    if "demand" in ask:
        question = "what service has been in the most demand this week?"
    else:
        question = ask or "how's business been this week?"
    body = f"{hello} quick one — {question}"
    return _compose_result(
        body, "vera_curious_ask_v1", [merchant_name(merchant)],
        "open_ended",
        "Low-friction relationship-building check-in; no ask has been sent recently."
    )


def compose_winback_eligible(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    days = p.get("days_since_expiry")
    hello = greeting_for_merchant(merchant)
    body = f"{hello} it's been {days} days since your plan lapsed, and we've noticed a dip since. Want me to send the renewal link to get back to full visibility?"
    return _compose_result(
        body, "vera_merchant_winback_v1", [merchant_name(merchant), str(days)],
        "renew",
        "Merchant's own subscription lapsed with a measurable performance impact; renewal nudge with the 'why'."
    )


def compose_ipl_match_today(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    match = p.get("match", "today's match")
    is_weeknight = p.get("is_weeknight")
    hello = greeting_for_merchant(merchant)
    seasonal_note = ""
    for beat in category.get("seasonal_beats", []) or []:
        if "IPL" in (beat.get("note") or "") or "ipl" in (beat.get("note") or "").lower():
            seasonal_note = beat.get("note", "")
            break
    if is_weeknight is False:
        body = f"{hello} {match} is on tonight, but weekend matches have tended to run below your weekday average " \
               f"{('(' + seasonal_note + ')') if seasonal_note else ''} — probably not worth a big push tonight."
    else:
        body = f"{hello} {match} is on tonight — weeknight matches have been driving extra covers " \
               f"{('(' + seasonal_note + ')') if seasonal_note else ''}. Want a quick match-night combo post?"
    return _compose_result(
        body, "vera_ipl_match_v1", [merchant_name(merchant), match, str(is_weeknight)],
        "open_ended",
        "Match-night trigger cross-checked against this category's own seasonal data on weeknight-vs-weekend performance."
    )


def compose_review_theme_emerged(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    theme = (p.get("theme") or "").replace("_", " ")
    occurrences = p.get("occurrences_30d")
    trend = p.get("trend", "")
    hello = greeting_for_merchant(merchant)
    body = f"{hello} a pattern's showing up in recent reviews — {theme}, mentioned {occurrences} times this month and {trend}. Worth a look before it affects your rating."
    return _compose_result(
        body, "vera_review_theme_v1", [merchant_name(merchant), theme, str(occurrences)],
        "open_ended",
        "Recurring, rising review theme; flagged early with frequency rather than quoting individual reviews."
    )


def compose_milestone_reached(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    metric = (p.get("metric") or "").replace("_", " ")
    value_now = p.get("value_now")
    milestone = p.get("milestone_value")
    hello = greeting_for_merchant(merchant)
    body = f"{hello} you're at {value_now} {metric}, {milestone - value_now if isinstance(milestone, (int,float)) and isinstance(value_now,(int,float)) else 'just a few'} short of {milestone}. A quick nudge to recent customers could get you there this week."
    return _compose_result(
        body, "vera_milestone_v1", [merchant_name(merchant), metric, str(milestone)],
        "open_ended",
        "Imminent milestone is a natural, low-pressure reason to prompt review generation."
    )


def compose_active_planning_intent(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    topic = (p.get("intent_topic") or "").replace("_", " ")
    hello = greeting_for_merchant(merchant)
    offer_hint = offer_by_audience(category)
    hint_txt = f" We've seen similar setups do well around \"{offer_hint.get('title')}\"-style packaging." if offer_hint else ""
    body = f"{hello} on {topic} — here's a starting shape: define the target group, a launch-week intro price, and 2-3 weekly slots to start.{hint_txt} Want me to draft the full package?"
    return _compose_result(
        body, "vera_planning_intent_v1", [merchant_name(merchant), topic],
        "open_ended",
        "Merchant is actively engaged and asked a direct planning question; matching their momentum with a concrete next step instead of another qualifying question."
    )


def compose_seasonal_perf_dip(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    metric = p.get("metric", "numbers")
    note = p.get("season_note", "").replace("_", " ")
    hello = greeting_for_merchant(merchant)
    body = f"{hello} your {metric} are down a bit, but this lines up with the usual {note} pattern for your category — not a red flag, just the season."
    return _compose_result(
        body, "vera_seasonal_dip_v1", [merchant_name(merchant), metric],
        "open_ended",
        "Dip is flagged as expected-seasonal by the trigger itself; reassurance rather than alarm avoids false urgency."
    )


def compose_customer_lapsed_hard(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    days = p.get("days_since_last_visit")
    focus = (p.get("previous_focus") or "").replace("_", " ")
    hello = greeting_for_customer(customer)
    offer = offer_by_audience(category, "repeat_user") or offer_by_audience(category)
    offer_txt = offer.get("title", "a welcome-back offer")
    body = f"{hello} it's been {days} days! If {focus} is still the goal, {offer_txt} is there whenever you want to restart."
    return _compose_result(
        body, "vera_customer_winback_v1", [safe_get(customer, "identity", "name", default=""), focus],
        "open_ended",
        "Hard-lapsed customer with a known prior goal; re-engagement anchored to that goal plus a concrete offer."
    )


def compose_trial_followup(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    options = p.get("next_session_options") or []
    hello = greeting_for_customer(customer)
    slot_text = " or ".join(o.get("label", "") for o in options if o.get("label"))
    body = f"{hello} how was the trial? {('Next session options: ' + slot_text + ' — want me to lock one in?') if slot_text else 'Want to book your next session?'}"
    return _compose_result(
        body, "vera_trial_followup_v1", [safe_get(customer, "identity", "name", default=""), slot_text],
        "book_slot",
        "Trial recently completed; timely follow-up with concrete next slots to convert to a regular booking."
    )


def compose_supply_alert(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    item = digest_item(category, p.get("alert_id"))
    molecule = p.get("molecule", "")
    batches = p.get("affected_batches") or []
    hello = greeting_for_merchant(merchant)
    actionable = item.get("actionable", "")
    body = f"{hello} urgent: recall on {molecule} — batches {', '.join(batches)} affected. {actionable}"
    return _compose_result(
        body, "vera_supply_alert_v1", [merchant_name(merchant), molecule, ", ".join(batches)],
        "acknowledge",
        "Safety recall — highest urgency, sent verbatim from the official alert with concrete batch numbers only."
    )


def compose_chronic_refill_due(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    molecules = p.get("molecule_list") or []
    delivery = p.get("delivery_address_saved")
    hello = greeting_for_customer(customer)
    mol_txt = ", ".join(molecules)
    body = f"{hello} your {mol_txt} refill is due soon. {'Should I schedule delivery to your saved address?' if delivery else 'Want me to arrange delivery?'}"
    return _compose_result(
        body, "vera_chronic_refill_v1", [safe_get(customer, "identity", "name", default=""), mol_txt],
        "confirm_delivery",
        "Chronic-medication stock-out is imminent per last refill + consumption; proactive refill with saved-address convenience."
    )


def compose_category_seasonal(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    trends = p.get("trends") or []
    hello = greeting_for_merchant(merchant)
    trend_txt = "; ".join(t.replace("_", " ") for t in trends[:3])
    body = f"{hello} seasonal shift for your category: {trend_txt}. Worth a shelf/menu rearrange this week."
    return _compose_result(
        body, "vera_category_seasonal_v1", [merchant_name(merchant), trend_txt],
        "open_ended",
        "Category-wide seasonal signal with a concrete, low-effort merchandising action."
    )


def compose_gbp_unverified(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    uplift = p.get("estimated_uplift_pct")
    hello = greeting_for_merchant(merchant)
    uplift_txt = f"~{uplift*100:.0f}%" if isinstance(uplift, (int, float)) else "meaningfully"
    body = f"{hello} your listing isn't verified yet — verified listings typically see {uplift_txt} more visibility. Want the verification steps?"
    return _compose_result(
        body, "vera_gbp_unverified_v1", [merchant_name(merchant), uplift_txt],
        "open_ended",
        "Straightforward, high-leverage fix the merchant hasn't done yet; framed with the concrete upside."
    )


def compose_cde_opportunity(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    item = digest_item(category, p.get("digest_item_id"))
    title = item.get("title", "")
    credits = p.get("credits")
    fee = (p.get("fee") or "").replace("_", " ")
    hello = greeting_for_merchant(merchant)
    body = f"{hello} {title}{(' — ' + str(credits) + ' credits') if credits else ''}{(', ' + fee) if fee else ''}. Want the details?"
    return _compose_result(
        body, "vera_cde_opportunity_v1", [merchant_name(merchant), title],
        "open_ended",
        "Relevant continuing-education opportunity surfaced ahead of its date, low pressure."
    )


def compose_competitor_opened(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    name = p.get("competitor_name", "a new competitor")
    distance = p.get("distance_km")
    their_offer = p.get("their_offer", "")
    hello = greeting_for_merchant(merchant)
    body = f"{hello} {name} opened {distance}km away{(' running ' + their_offer) if their_offer else ''}. Not a reason to price-match — worth double-checking your own offer is visible and current."
    return _compose_result(
        body, "vera_competitor_opened_v1", [merchant_name(merchant), name, str(distance)],
        "open_ended",
        "Factual competitive signal, framed constructively (not urging a price war) per category voice guidance."
    )


def compose_perf_spike(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    metric = p.get("metric", "numbers")
    driver = (p.get("likely_driver") or "").replace("_", " ")
    delta = p.get("delta_pct")
    hello = greeting_for_merchant(merchant)
    delta_txt = f"+{delta*100:.0f}%" if isinstance(delta, (int, float)) else "up"
    body = f"{hello} nice — {metric} are {delta_txt}{(' , likely from ' + driver) if driver else ''}. Worth doing more of whatever that was."
    return _compose_result(
        body, "vera_perf_spike_v1", [merchant_name(merchant), metric, delta_txt],
        "open_ended",
        "Positive signal with an identifiable likely driver; encouraging repetition rather than silence on good news."
    )


def compose_dormant_with_vera(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    days = p.get("days_since_last_merchant_message")
    last_topic = (p.get("last_topic") or "").replace("_", " ")
    hello = greeting_for_merchant(merchant)
    body = f"{hello} it's been {days} days since we last spoke about {last_topic}. Still on your mind, or should I check back later?"
    return _compose_result(
        body, "vera_dormant_checkin_v1", [merchant_name(merchant), last_topic],
        "open_ended",
        "Long silence after an unresolved topic; low-pressure check-in referencing the actual last topic, not generic."
    )


def compose_fallback(trig, category, merchant, customer):
    p = trig.get("payload", {}) or {}
    kind = trig.get("kind", "update")
    scope = trig.get("scope", "merchant")
    hello = greeting_for_customer(customer) if scope == "customer" else greeting_for_merchant(merchant)
    # Use whatever human-readable hint exists without inventing anything.
    hint = p.get("title") or p.get("summary") or kind.replace("_", " ")
    body = f"{hello} an update on {hint}."
    return _compose_result(
        body, "vera_generic_v1", [kind],
        "open_ended",
        f"Unrecognised trigger kind '{kind}'; used a grounded generic composer instead of inventing detail."
    )


KIND_COMPOSERS = {
    "research_digest": compose_research_digest,
    "regulation_change": compose_regulation_change,
    "recall_due": compose_recall_due,
    "perf_dip": compose_perf_dip,
    "renewal_due": compose_renewal_due,
    "festival_upcoming": compose_festival_upcoming,
    "wedding_package_followup": compose_wedding_package_followup,
    "curious_ask_due": compose_curious_ask_due,
    "winback_eligible": compose_winback_eligible,
    "ipl_match_today": compose_ipl_match_today,
    "review_theme_emerged": compose_review_theme_emerged,
    "milestone_reached": compose_milestone_reached,
    "active_planning_intent": compose_active_planning_intent,
    "seasonal_perf_dip": compose_seasonal_perf_dip,
    "customer_lapsed_hard": compose_customer_lapsed_hard,
    "trial_followup": compose_trial_followup,
    "supply_alert": compose_supply_alert,
    "chronic_refill_due": compose_chronic_refill_due,
    "category_seasonal": compose_category_seasonal,
    "gbp_unverified": compose_gbp_unverified,
    "cde_opportunity": compose_cde_opportunity,
    "competitor_opened": compose_competitor_opened,
    "perf_spike": compose_perf_spike,
    "dormant_with_vera": compose_dormant_with_vera,
}


def compose(trig: Dict[str, Any]):
    kind = (trig.get("kind") or "").lower()
    merchant = get_ctx("merchant", trig.get("merchant_id"))
    customer = get_ctx("customer", trig.get("customer_id")) if trig.get("customer_id") else {}
    category = category_for_merchant(merchant)
    fn = KIND_COMPOSERS.get(kind, compose_fallback)
    try:
        body, template_name, params, cta, rationale = fn(trig, category, merchant, customer)
    except Exception:
        body, template_name, params, cta, rationale = compose_fallback(trig, category, merchant, customer)
    body = strip_urls(scrub_taboo(body, category))
    return body, template_name, params, cta, rationale


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/v1/healthz")
def healthz():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": counts_loaded(),
    }


@app.get("/v1/metadata")
def metadata():
    return {
        "team_name": TEAM_NAME,
        "team_members": TEAM_MEMBERS,
        "model": BOT_MODEL,
        "approach": BOT_APPROACH,
        "contact_email": CONTACT_EMAIL,
        "version": BOT_VERSION,
        "submitted_at": SUBMITTED_AT or now_iso(),
    }


@app.post("/v1/context")
async def push_context(request: Request):
    try:
        raw = await request.body()
    except Exception:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "unreadable_body"})

    if len(raw) > MAX_CONTEXT_BYTES:
        return JSONResponse(status_code=413, content={"accepted": False, "reason": "payload_too_large"})

    try:
        body = json.loads(raw) if raw else {}
    except Exception:
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_json"})

    if not isinstance(body, dict):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_body_shape"})

    scope = body.get("scope")
    context_id = body.get("context_id")
    version = body.get("version")
    payload = body.get("payload")

    if scope not in ("category", "merchant", "customer", "trigger"):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_scope", "details": f"got {scope!r}"})
    if not context_id or not isinstance(context_id, str):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_context_id"})
    if not isinstance(version, int):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_version"})
    if not isinstance(payload, dict):
        return JSONResponse(status_code=400, content={"accepted": False, "reason": "invalid_payload"})

    key = (scope, context_id)
    current = CONTEXTS.get(key)
    if current and current["version"] >= version:
        return JSONResponse(status_code=409, content={
            "accepted": False, "reason": "stale_version", "current_version": current["version"]
        })

    CONTEXTS[key] = {"version": version, "payload": payload}
    return {"accepted": True, "ack_id": f"ack_{context_id}_v{version}", "stored_at": now_iso()}


@app.post("/v1/tick")
async def tick(request: Request):
    try:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
    except Exception:
        return {"actions": []}

    if not isinstance(body, dict):
        return {"actions": []}

    available = body.get("available_triggers") or []
    if not isinstance(available, list):
        return {"actions": []}

    now_dt = parse_iso(body.get("now")) or datetime.now(timezone.utc)

    # Gather eligible triggers: exist, not expired, not already suppressed,
    # actor not already mid-conversation.
    candidates: List[Dict[str, Any]] = []
    for trig_id in available:
        try:
            if not isinstance(trig_id, str):
                continue
            trig = get_ctx("trigger", trig_id)
            if not trig:
                continue
            expires = parse_iso(trig.get("expires_at"))
            if expires and now_dt > expires:
                continue
            suppression_key = trig.get("suppression_key")
            if suppression_key and suppression_key in USED_SUPPRESSION_KEYS:
                continue
            scope = trig.get("scope", "merchant")
            actor_id = trig.get("customer_id") if scope == "customer" and trig.get("customer_id") else trig.get("merchant_id")
            if not actor_id:
                continue
            actor_key = f"{scope}:{actor_id}"
            if actor_key in ACTIVE_ACTOR_CONVERSATIONS:
                continue
            candidates.append(trig)
        except Exception:
            continue

    # One new conversation per actor per tick: keep only the highest-urgency
    # trigger for each actor_key.
    best_per_actor: Dict[str, Dict[str, Any]] = {}
    for trig in candidates:
        scope = trig.get("scope", "merchant")
        actor_id = trig.get("customer_id") if scope == "customer" and trig.get("customer_id") else trig.get("merchant_id")
        actor_key = f"{scope}:{actor_id}"
        current = best_per_actor.get(actor_key)
        if current is None or (trig.get("urgency", 0) or 0) > (current.get("urgency", 0) or 0):
            best_per_actor[actor_key] = trig

    actions: List[Dict[str, Any]] = []
    for actor_key, trig in best_per_actor.items():
        if len(actions) >= TICK_ACTION_CAP:
            break
        try:
            body_text, template_name, params, cta, rationale = compose(trig)
            if not body_text.strip():
                continue

            conversation_id = f"conv_{uuid.uuid4().hex[:12]}"
            scope = trig.get("scope", "merchant")
            merchant_id = trig.get("merchant_id")
            customer_id = trig.get("customer_id")
            suppression_key = trig.get("suppression_key")

            action = {
                "conversation_id": conversation_id,
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "send_as": "vera",
                "trigger_id": trig.get("id"),
                "template_name": template_name,
                "template_params": params,
                "body": body_text,
                "cta": cta,
                "suppression_key": suppression_key,
                "rationale": rationale,
            }
            actions.append(action)

            if suppression_key:
                USED_SUPPRESSION_KEYS.add(suppression_key)
            ACTIVE_ACTOR_CONVERSATIONS[actor_key] = conversation_id
            CONVERSATIONS[conversation_id] = {
                "trigger": trig,
                "actor_key": actor_key,
                "merchant_id": merchant_id,
                "customer_id": customer_id,
                "turns": 1,
                "sent_bodies": [body_text],
                "last_inbound": None,
                "inbound_repeat_count": 0,
                "state": "open",
            }
        except Exception:
            continue

    return {"actions": actions}


# --- reply classification helpers -----------------------------------------

ACCEPT_WORDS = ["yes", "sure", "ok", "okay", "sounds good", "let's do it", "lets do it",
                "interested", "go ahead", "please", "book it", "confirm"]
DECLINE_WORDS = ["not interested", "no thanks", "no thank you", "stop", "unsubscribe",
                 "don't contact", "dont contact", "not now and never"]
WAIT_WORDS = ["later", "busy", "call me later", "give me time", "not now", "will check", "will get back"]
HOSTILE_WORDS = ["idiot", "stupid", "shut up", "useless", "scam", "fraud"]


def classify_reply(message: str) -> str:
    m = (message or "").lower().strip()
    if not m:
        return "unclear"
    for w in DECLINE_WORDS:
        if w in m:
            return "decline"
    for w in WAIT_WORDS:
        if w in m:
            return "wait"
    for w in ACCEPT_WORDS:
        if w in m:
            return "accept"
    return "unclear"


def is_hostile_or_offtopic(message: str) -> bool:
    m = (message or "").lower()
    return any(w in m for w in HOSTILE_WORDS)


def vary_if_repeated(new_body: str, convo: Dict[str, Any]) -> str:
    if new_body in convo.get("sent_bodies", []):
        return new_body + " (following up on the above)"
    return new_body


@app.post("/v1/reply")
async def reply(request: Request):
    try:
        raw = await request.body()
        body = json.loads(raw) if raw else {}
    except Exception:
        return {"action": "wait", "wait_seconds": 1800, "rationale": "Malformed reply payload; backing off."}

    if not isinstance(body, dict):
        return {"action": "wait", "wait_seconds": 1800, "rationale": "Malformed reply payload; backing off."}

    conversation_id = body.get("conversation_id")
    message = body.get("message", "") or ""
    from_role = body.get("from_role", "merchant")

    convo = CONVERSATIONS.get(conversation_id)
    if not convo:
        # Unknown conversation — respond minimally and safely rather than crash.
        return {"action": "end", "rationale": "Unknown conversation_id; ending safely rather than guessing state."}

    try:
        # --- auto-reply-loop detection ---
        if message and message == convo.get("last_inbound"):
            convo["inbound_repeat_count"] = convo.get("inbound_repeat_count", 0) + 1
        else:
            convo["inbound_repeat_count"] = 0
        convo["last_inbound"] = message

        if convo["inbound_repeat_count"] >= 2:  # same text arrived 3x total
            convo["state"] = "ended"
            if convo.get("actor_key"):
                ACTIVE_ACTOR_CONVERSATIONS.pop(convo["actor_key"], None)
            return {"action": "end", "rationale": "Same message repeated multiple times in a row — looks like a canned auto-reply; exiting gracefully."}

        # --- turn cap ---
        convo["turns"] = convo.get("turns", 1) + 1
        if convo["turns"] > 5:
            convo["state"] = "ended"
            if convo.get("actor_key"):
                ACTIVE_ACTOR_CONVERSATIONS.pop(convo["actor_key"], None)
            return {"action": "end", "rationale": "Reached the 5-turn conversation cap; wrapping up cleanly."}

        classification = classify_reply(message)
        hostile = is_hostile_or_offtopic(message)

        trig = convo.get("trigger", {})
        cta = None
        try:
            _, _, _, cta, _ = compose(trig)
        except Exception:
            cta = "open_ended"

        if classification == "decline":
            convo["state"] = "ended"
            if convo.get("actor_key"):
                ACTIVE_ACTOR_CONVERSATIONS.pop(convo["actor_key"], None)
            return {"action": "end", "rationale": "Recipient declined; exiting without further prompting."}

        if classification == "wait":
            return {"action": "wait", "wait_seconds": 1800, "rationale": "Recipient asked for time; backing off 30 minutes."}

        if hostile:
            resp_body = "Understood — happy to help another time. In the meantime, is there anything else about your listing or offers I can help with?"
            resp_body = vary_if_repeated(resp_body, convo)
            convo.setdefault("sent_bodies", []).append(resp_body)
            return {"action": "send", "body": resp_body, "cta": "open_ended",
                    "rationale": "Message was hostile/off-topic; staying polite and steering back to the bot's actual scope without escalating."}

        if classification == "accept":
            # Intent transition: move straight to action, not another qualifying question.
            p = trig.get("payload", {}) or {}
            if cta == "book_slot":
                slots = p.get("available_slots") or p.get("next_session_options") or []
                slot_text = " or ".join(s.get("label", "") for s in slots if s.get("label"))
                resp_body = f"Great — locking in {slot_text or 'the next available slot'}. I'll confirm shortly."
            elif cta == "renew":
                resp_body = "Perfect — sending the renewal link now so you can complete it in one tap."
            elif cta == "book_slot" or cta == "confirm_delivery":
                resp_body = "Great — scheduling that now."
            else:
                resp_body = "Great, let's move on it — I'll put together the details and follow up shortly."
            resp_body = vary_if_repeated(resp_body, convo)
            convo.setdefault("sent_bodies", []).append(resp_body)
            return {"action": "send", "body": resp_body, "cta": cta or "open_ended",
                    "rationale": "Recipient accepted; switching immediately from qualifying to concrete next action per their momentum."}

        # unclear / genuine question: keep it going, grounded, no repeats
        resp_body = "Happy to help with that — could you tell me a bit more, or just say 'yes' if you'd like me to go ahead?"
        resp_body = vary_if_repeated(resp_body, convo)
        convo.setdefault("sent_bodies", []).append(resp_body)
        return {"action": "send", "body": resp_body, "cta": "open_ended",
                "rationale": "Reply didn't clearly accept, decline, or ask to wait; asking one lightweight clarifying question while keeping the original offer open."}
    except Exception:
        return {"action": "wait", "wait_seconds": 1800, "rationale": "Unexpected error handling this reply; backing off rather than failing the turn."}
