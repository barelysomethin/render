"""
FastAPI application — all 5 required endpoints.

Endpoints:
  GET  /v1/healthz   — liveness probe
  GET  /v1/metadata  — team identity
  POST /v1/context   — receive context push
  POST /v1/tick      — periodic wake-up; bot decides what to send
  POST /v1/reply     — handle merchant/customer reply
"""
from __future__ import annotations
import os
import time
import uuid
import asyncio
from datetime import datetime, timezone
from typing import Any
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.schemas import (
    ContextRequest, ContextAccepted, ContextRejected,
    TickRequest, TickResponse, TickAction,
    ReplyRequest, ReplyResponse,
    HealthzResponse, MetadataResponse,
)
from app.store import ContextStore, SuppressionTracker, ConversationTracker
from app.composer import Composer

# ──────────────────────────────────────────────
# App setup
# ──────────────────────────────────────────────

app = FastAPI(title="Vera Bot", version="1.0.0")

START_TIME = time.time()

# Global state (in-memory as spec allows)
store = ContextStore()
suppression = SuppressionTracker()
conversations = ConversationTracker()

# Composer (initialized at startup)
composer: Composer = None


@app.on_event("startup")
async def startup():
    global composer
    api_key = os.getenv("GROQ_API_KEY", "")
    model = os.getenv("GROQ_MODEL", "qwen-qwq-32b")
    composer = Composer(api_key=api_key, model=model)
    print(f"[VERA] Bot started. Model: {model}")


# ──────────────────────────────────────────────
# GET /v1/healthz
# ──────────────────────────────────────────────

def log_audit(action: dict):
    import json
    from datetime import datetime
    with open("BOT_REPLIES_AUDIT.jsonl", "a", encoding="utf-8") as f:
        log_entry = {
            "timestamp": datetime.utcnow().isoformat(),
            "action": action
        }
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

@app.get("/v1/healthz")
async def healthz() -> HealthzResponse:
    return HealthzResponse(
        status="ok",
        uptime_seconds=int(time.time() - START_TIME),
        contexts_loaded=store.counts(),
    )


# ──────────────────────────────────────────────
# GET /v1/metadata
# ──────────────────────────────────────────────

@app.get("/v1/metadata")
async def metadata() -> MetadataResponse:
    return MetadataResponse(
        team_name=os.getenv("TEAM_NAME", "Vera Bot"),
        team_members=os.getenv("TEAM_MEMBERS", "Solo").split(","),
        model=os.getenv("GROQ_MODEL", "qwen-qwq-32b"),
        approach=(
            "Context-first chain-of-thought reasoning: "
            "signal selection -> voice calibration -> message composition. "
            "Dispatches by trigger.kind. "
            "Auto-reply detection, intent transition, suppression, and restraint built in."
        ),
        contact_email=os.getenv("CONTACT_EMAIL", "contact@example.com"),
        version="1.0.0",
        submitted_at=os.getenv("SUBMITTED_AT", datetime.now(timezone.utc).isoformat()),
    )


# ──────────────────────────────────────────────
# POST /v1/context
# ──────────────────────────────────────────────

@app.post("/v1/context")
async def push_context(body: ContextRequest):
    accepted, current_version = store.push(
        scope=body.scope,
        context_id=body.context_id,
        version=body.version,
        payload=body.payload,
        delivered_at=body.delivered_at,
    )

    if accepted:
        return ContextAccepted(
            accepted=True,
            ack_id=f"ack_{body.context_id}_v{body.version}",
            stored_at=store.get_stored_at(body.scope, body.context_id),
        )
    else:
        return JSONResponse(
            status_code=409,
            content={
                "accepted": False,
                "reason": "stale_version",
                "current_version": current_version,
            },
        )


# ──────────────────────────────────────────────
# POST /v1/tick
# ──────────────────────────────────────────────

@app.post("/v1/tick")
async def tick(body: TickRequest) -> TickResponse:
    actions: list[TickAction] = []

    if not body.available_triggers:
        return TickResponse(actions=[])

    # Group triggers by merchant and pick the most urgent one per merchant
    merchant_to_best_trigger = {}
    for trg_id in body.available_triggers:
        trg = store.get("trigger", trg_id)
        if not trg:
            continue
        
        m_id = trg.get("merchant_id")
        if not m_id:
            continue
            
        urgency = trg.get("urgency", 1)
        if m_id not in merchant_to_best_trigger or urgency > merchant_to_best_trigger[m_id][0]:
            merchant_to_best_trigger[m_id] = (urgency, trg_id, trg)

    # Sort merchants by the urgency of their best trigger
    sorted_merchants = sorted(merchant_to_best_trigger.values(), key=lambda x: -x[0])

    for urgency, trg_id, trg in sorted_merchants[:20]:
        suppression_key = trg.get("suppression_key", "")

        if suppression_key and suppression.is_suppressed(suppression_key):
            continue

        merchant_id = trg.get("merchant_id")
        customer_id = trg.get("customer_id")

        if suppression.is_merchant_suppressed(merchant_id):
            continue

        merchant = store.get("merchant", merchant_id)
        if not merchant:
            continue

        category_slug = merchant.get("category_slug", "")
        category = store.get("category", category_slug)
        if not category:
            continue

        customer = store.get("customer", customer_id) if customer_id else None

        try:
            result = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: composer.compose_tick(category, merchant, trg, customer),
            )
            
            body_text = result.get("body", "")
            if not body_text:
                continue

            conv_id = f"conv_{merchant_id}_{trg_id}"
            if conversations.was_body_sent_before(conv_id, body_text):
                continue

            if suppression_key:
                suppression.suppress(suppression_key)

            conversations.add_bot_turn(conv_id, body_text)

            action = TickAction(
                conversation_id=conv_id,
                merchant_id=merchant_id,
                customer_id=customer_id,
                send_as=result.get("send_as", "vera"),
                trigger_id=trg_id,
                template_name=result.get("template_name", f"vera_{trg.get('kind', 'generic')}_v1"),
                template_params=result.get("template_params", []),
                body=body_text,
                cta=result.get("cta", "open_ended"),
                suppression_key=suppression_key,
                rationale=result.get("rationale", ""),
            )
            log_audit(action.dict())
            actions.append(action)
        except Exception as e:
            print(f"[VERA] Composer error for {merchant_id}: {e}")
            continue

    return TickResponse(actions=actions)


# ──────────────────────────────────────────────
# POST /v1/reply
# ──────────────────────────────────────────────

@app.post("/v1/reply")
async def reply(body: ReplyRequest) -> ReplyResponse:
    conv_id = body.conversation_id
    message = body.message
    merchant_id = body.merchant_id
    customer_id = body.customer_id

    # Check if conversation is opted out
    if suppression.is_conversation_opted_out(conv_id):
        return ReplyResponse(
            action="end",
            rationale="Conversation previously ended by merchant opt-out.",
        )

    # Record this turn
    conversations.add_turn(conv_id, body.from_role, message)

    # ── Rule-based checks first (fast, no LLM needed) ──

    # 1. Hostile / explicit opt-out
    if conversations.is_hostile_opt_out(message):
        suppression.opt_out_conversation(conv_id)
        if merchant_id:
            suppression.opt_out_merchant(merchant_id, days=30)
        return ReplyResponse(
            action="end",
            rationale="Merchant explicitly opted out. Closing conversation. Suppressing all triggers for 30 days.",
        )

    # 2. Auto-reply detection
    if conversations.is_auto_reply(conv_id, message):
        merchant_turns = conversations.get_merchant_turns(conv_id)
        auto_reply_count = sum(1 for m in merchant_turns if m == message)

        if auto_reply_count >= 3:
            suppression.opt_out_conversation(conv_id)
            return ReplyResponse(
                action="end",
                rationale="Auto-reply detected 3 times in a row. No real engagement. Closing conversation.",
            )
        elif auto_reply_count == 2:
            return ReplyResponse(
                action="wait",
                wait_seconds=86400,  # 24 hours
                rationale="Same auto-reply twice — owner not at phone. Waiting 24h before retry.",
            )
        else:
            return ReplyResponse(
                action="wait",
                wait_seconds=14400,  # 4 hours
                rationale="Detected merchant auto-reply (canned response). Backing off 4 hours for owner to see it.",
            )

    # 3. Intent transition — go to action mode
    is_intent = conversations.is_intent_transition(message)

    # ── LLM reply composition ──
    merchant = store.get("merchant", merchant_id) if merchant_id else None
    customer = store.get("customer", customer_id) if customer_id else None
    category_slug = (merchant or {}).get("category_slug", "")
    category = store.get("category", category_slug) if category_slug else None
    history = conversations.get_history(conv_id)

    # Add intent flag to the prompt context
    augmented_message = message
    if is_intent:
        augmented_message = f"[INTENT_TRANSITION DETECTED - switch to ACTION MODE] {message}"

    try:
        result = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: composer.compose_reply(
                conv_id, merchant_id, customer_id, body.from_role,
                augmented_message, body.turn_number, merchant, customer, category, history
            ),
        )
    except Exception as e:
        print(f"[VERA] Reply composer error: {e}")
        return ReplyResponse(
            action="send",
            body="Got it — let me help you with that.",
            cta="open_ended",
            rationale="Fallback response due to composer error.",
        )

    action = result.get("action", "send")
    reply_body = result.get("body", "")

    # Record bot's reply
    if action == "send" and reply_body:
        conversations.add_bot_turn(conv_id, reply_body)

    if action == "end":
        suppression.opt_out_conversation(conv_id)

    res_action = {
        "action": action,
        "body": reply_body,
        "cta": result.get("cta", "open_ended"),
        "wait_seconds": result.get("wait_seconds"),
        "rationale": result.get("rationale", "")
    }
    log_audit(res_action)
    return ReplyResponse(**res_action)


# ──────────────────────────────────────────────
# Optional: /v1/teardown (clean up state)
# ──────────────────────────────────────────────

@app.post("/v1/teardown")
async def teardown():
    global store, suppression, conversations
    store = ContextStore()
    suppression = SuppressionTracker()
    conversations = ConversationTracker()
    return {"status": "ok", "message": "State wiped."}
