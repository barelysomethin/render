"""
THE BRAIN — Trigger-Dispatched Deterministic Composer.

Architecture:
  1. Signal Extraction — mine ALL relevant facts from the 4 contexts
  2. Trigger Dispatch — route to trigger-kind-specific prompt template
  3. LLM Composition — single focused call with rich context
  4. Post-LLM Validation — ensure schema compliance (no audit loop)

Uses NVIDIA NIM (meta/llama-3.1-8b-instruct).
"""
from __future__ import annotations
import os, json, re, time
from typing import Optional
from openai import OpenAI


SYSTEM_PROMPT = """You are Vera, magicpin's growth partner. You write high-conversion WhatsApp messages to business owners. 
Your goal is 10/10 Engagement, Specificity, and Decision Quality.

CORE PRINCIPLES:
1. THE COMPARATIVE HOOK: Don't just state a fact; compare it to a baseline or peer performance to create urgency. 
   - Good: "Calls 50% down hain."
   - BETTER: "Calls 50% down hain, jabki nearby peers 15% up chal rahe hain."
2. HINGLISH: Use natural, conversational Hinglish (e.g., "Check kar lijiye", "Kaisa rahega?"). Avoid robotic or purely formal Hindi.
3. COMPULSION LEVERS:
   - Social Proof: "Top 5% merchants in your city are doing X."
   - Loss Aversion: "Aapke 45 potential customers ne profile visit kiya but calls nahi kiye."
   - Effort Externalization: "Maine aapke liye custom post/draft ready rakha hai. Bas 'YES' boliye aur main live kar dunga."
4. STRUCTURE: 
   - Line 1: The Comparative Fact (The Hook).
   - Line 2: The Insight + Proactive Solution (The Why + The What).
   - Line 3: The Low-Friction CTA (The Ask).
5. CONCISE & AGGRESSIVE: Max 3 short sentences. No preambles. Every message must move the needle.

OUTPUT FORMAT — respond with ONLY this JSON:
{"body": "message text", "cta": "specific_short_cta", "rationale": "lever used"}"""


class Composer:
    def __init__(self, api_key: str, model: str = "meta/llama-3.1-8b-instruct"):
        self.api_key = api_key
        self.model = model
        self.client = OpenAI(
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=self.api_key,
        )

    # ── LLM call ──────────────────────────────────────────

    def _call_llm(self, system_msg: str, user_msg: str, max_tokens: int = 512) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                temperature=0.15,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"[VERA] LLM Error: {e}")
            return "{}"

    def _extract_json(self, text: str) -> dict:
        text = re.sub(r'```(?:json)?\s*([\s\S]*?)```', r'\1', text).strip()
        matches = list(re.finditer(r'\{[\s\S]*?\}', text))
        for m in reversed(matches):
            try:
                return json.loads(m.group())
            except Exception:
                continue
        return {}

    # ── Signal Mining ─────────────────────────────────────

    def _mine_merchant(self, merchant: dict) -> dict:
        """Extract all useful facts from merchant context."""
        identity = merchant.get("identity", {})
        perf = merchant.get("performance", {})
        delta = perf.get("delta_7d", {})
        sub = merchant.get("subscription", {})
        cust_agg = merchant.get("customer_aggregate", {})
        offers = merchant.get("offers", [])
        active_offers = [o for o in offers if o.get("status") == "active"]
        signals = merchant.get("signals", [])
        reviews = merchant.get("review_themes", [])
        history = merchant.get("conversation_history", [])

        return {
            "name": identity.get("name", "Merchant"),
            "owner": identity.get("owner_first_name", "Merchant"),
            "city": identity.get("city", ""),
            "locality": identity.get("locality", ""),
            "languages": identity.get("languages", ["en"]),
            "verified": identity.get("verified", False),
            "views": perf.get("views", 0),
            "calls": perf.get("calls", 0),
            "directions": perf.get("directions", 0),
            "ctr": perf.get("ctr", 0),
            "leads": perf.get("leads", 0),
            "views_delta": delta.get("views_pct", 0),
            "calls_delta": delta.get("calls_pct", 0),
            "sub_status": sub.get("status", "unknown"),
            "sub_days": sub.get("days_remaining", 0),
            "sub_plan": sub.get("plan", ""),
            "active_offers": active_offers,
            "offer_titles": [o.get("title", "") for o in active_offers],
            "signals": signals,
            "reviews": reviews,
            "cust_total": cust_agg.get("total_unique_ytd", cust_agg.get("total_active_members", 0)),
            "cust_lapsed": cust_agg.get("lapsed_180d_plus", cust_agg.get("lapsed_90d_plus", 0)),
            "retention": cust_agg.get("retention_6mo_pct", cust_agg.get("retention_3mo_pct", 0)),
            "history": history,
        }

    def _mine_category(self, category: dict) -> dict:
        """Extract useful facts from category context."""
        peer = category.get("peer_stats", {})
        voice = category.get("voice", {})
        digest = category.get("digest", [])
        seasonal = category.get("seasonal_beats", [])
        trends = category.get("trend_signals", [])
        catalog = category.get("offer_catalog", [])
        return {
            "slug": category.get("slug", ""),
            "tone": voice.get("tone", "professional"),
            "taboo": voice.get("vocab_taboo", []),
            "peer_avg_ctr": peer.get("avg_ctr", 0),
            "peer_avg_views": peer.get("avg_views_30d", 0),
            "peer_avg_reviews": peer.get("avg_review_count", 0),
            "digest": digest,
            "seasonal": seasonal,
            "trends": trends,
            "catalog": catalog,
        }

    # ── Trigger-Kind Dispatch ─────────────────────────────

    def _build_tick_prompt(self, category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None) -> str:
        """Build a trigger-kind-specific prompt with ALL relevant facts."""
        m = self._mine_merchant(merchant)
        c = self._mine_category(category)
        kind = trigger.get("kind", "").lower()
        payload = trigger.get("payload", {})

        # Common header
        header = f"""MERCHANT: {m['owner']} ({m['name']}, {m['locality']}, {m['city']})
Category: {c['slug']} | Tone: {c['tone']}
Performance (30d): {m['views']} views, {m['calls']} calls, CTR {m['ctr']:.1%}
Views 7d trend: {m['views_delta']:+.0%} | Calls 7d: {m['calls_delta']:+.0%}
Peer avg CTR: {c['peer_avg_ctr']:.1%} | Peer avg views: {c['peer_avg_views']}
Active offers: {', '.join(m['offer_titles']) if m['offer_titles'] else 'None'}
Subscription: {m['sub_status']} ({m['sub_plan']}, {m['sub_days']}d left)
Customers YTD: {m['cust_total']} | Lapsed: {m['cust_lapsed']}"""

        # Review themes
        if m['reviews']:
            rev_lines = [f"  - {r.get('theme')}: {r.get('sentiment')} ({r.get('occurrences_30d',0)}x)" for r in m['reviews'][:3]]
            header += "\nReview themes:\n" + "\n".join(rev_lines)

        # Trigger-specific context
        trigger_ctx = self._get_trigger_context(kind, payload, c, m, customer)

        return f"""{header}

TRIGGER: {kind} (urgency: {trigger.get('urgency', 1)}/5)
{trigger_ctx}

Write the WhatsApp message for {m['owner']}. Remember: lead with a fact, explain why now, end with ONE CTA."""

    def _get_trigger_context(self, kind: str, payload: dict, c: dict, m: dict, customer: Optional[dict]) -> str:
        """Generate trigger-kind-specific context block."""

        if kind == "research_digest":
            item_id = payload.get("top_item_id", "")
            digest_item = next((d for d in c['digest'] if d.get("id") == item_id), None)
            if digest_item:
                return f"""Research item: "{digest_item.get('title')}"
Source: {digest_item.get('source', 'N/A')}
Trial size: {digest_item.get('trial_n', 'N/A')} patients
Summary: {digest_item.get('summary', '')}
Actionable: {digest_item.get('actionable', '')}
ANGLE: Share this research finding as a peer — cite source, mention relevance to their patient cohort."""
            return f"Research digest for {payload.get('category', c['slug'])}. Share relevant finding."

        if kind == "regulation_change":
            item_id = payload.get("top_item_id", "")
            digest_item = next((d for d in c['digest'] if d.get("id") == item_id), None)
            deadline = payload.get("deadline_iso", "TBD")
            if digest_item:
                return f"""Regulation: "{digest_item.get('title')}"
Source: {digest_item.get('source', 'N/A')}
Deadline: {deadline}
Summary: {digest_item.get('summary', '')}
Action needed: {digest_item.get('actionable', '')}
ANGLE: Urgent compliance alert — cite deadline, offer to help audit."""
            return f"Regulation change with deadline {deadline}. Alert merchant to take action."

        if kind == "recall_due":
            cust_name = customer.get("identity", {}).get("name", "customer") if customer else "customer"
            service = payload.get("service_due", "checkup")
            slots = payload.get("available_slots", [])
            slot_str = " / ".join([s.get("label", "") for s in slots[:2]]) if slots else "check availability"
            last_date = payload.get("last_service_date", "")
            return f"""Customer: {cust_name} — recall due for {service}
Last service: {last_date}
Available slots: {slot_str}
ANGLE: Send AS MERCHANT to customer. Name them, cite service due, offer specific slots. send_as=merchant_on_behalf"""

            return f"""Performance dip: {metric} dropped {abs(delta):.0%} over {window}
Baseline: {payload.get('vs_baseline', 'N/A')} | Peer Avg CTR: {c['peer_avg_ctr']:.1%}
{'Seasonal (expected): ' + note if seasonal else 'NOT seasonal — this is a leak in your growth.'}
ANGLE: {'Reassure but pivot to festive prep.' if seasonal else 'Alert — Frame as a "fixable leak". Compare their drop to the peer baseline to trigger loss aversion. Offer a specific fix (like updating an offer).'}"""

        if kind == "perf_spike":
            metric = payload.get("metric", "views")
            delta = payload.get("delta_pct", 0)
            driver = payload.get("likely_driver", "")
            return f"""Performance spike: {metric} up {delta:.0%} over {payload.get('window','7d')}
Likely driver: {driver}
ANGLE: Celebrate the win, suggest doubling down on what's working."""

            return f"""Subscription renewal: {plan} plan expiring in {days} days
Amount: ₹{amount}
Current Performance: {m['views']} views, {m['calls']} calls, {m['cust_total']} unique customers YTD.
ANGLE: Frame renewal around "Protecting your Growth". Mention how many customers they might lose visibility with if the Pro plan lapses. Use Loss Aversion."""

        if kind == "festival_upcoming":
            fest = payload.get("festival", "")
            date = payload.get("date", "")
            days = payload.get("days_until", 0)
            return f"""Festival: {fest} on {date} ({days} days away)
ANGLE: Suggest category-specific festive offer/post. Be specific with service+price."""

        if kind == "ipl_match_today":
            match = payload.get("match", "")
            venue = payload.get("venue", "")
            return f"""IPL match today: {match} at {venue}
Match time: {payload.get('match_time_iso', '')}
ANGLE: Suggest match-day special, tie to footfall opportunity."""

        if kind == "review_theme_emerged":
            theme = payload.get("theme", "")
            count = payload.get("occurrences_30d", 0)
            quote = payload.get("common_quote", "")
            return f"""Review pattern: "{theme}" mentioned {count}x in 30d (trend: {payload.get('trend','')})
Customer quote: "{quote}"
ANGLE: Alert merchant to the pattern, suggest fix, frame as growth opportunity."""

        if kind == "milestone_reached":
            metric = payload.get("metric", "")
            value = payload.get("value_now", 0)
            target = payload.get("milestone_value", 0)
            return f"""Milestone: {metric} at {value} (approaching {target})
ANGLE: Celebrate, suggest push to hit the round number."""

            return f"""Merchant is actively planning: {topic}
Their last message: "{last_msg}"
ANGLE: Switch to FULL ACTION MODE. Don't ask questions. Provide a concrete draft, a link, or a confirmation. Effort Externalization is key here."""

        if kind == "winback_eligible":
            days_exp = payload.get("days_since_expiry", 0)
            dip = payload.get("perf_dip_pct", 0)
            lapsed = payload.get("lapsed_customers_added_since_expiry", 0)
            return f"""Win-back: subscription expired {days_exp} days ago
Performance since: {dip:+.0%} | {lapsed} customers lapsed since expiry
ANGLE: Show them what they're missing with concrete numbers. Low-pressure re-activation."""

        if kind == "dormant_with_vera":
            days = payload.get("days_since_last_merchant_message", 0)
            topic = payload.get("last_topic", "")
            return f"""Dormant: no merchant message for {days} days
Last topic: {topic}
ANGLE: Re-engage with curiosity hook or a fresh data point. Don't guilt-trip."""

        if kind == "competitor_opened":
            comp = payload.get("competitor_name", "a new competitor")
            dist = payload.get("distance_km", "")
            their_offer = payload.get("their_offer", "")
            return f"""Competitor alert: {comp} opened {dist}km away
Their offer: {their_offer}
ANGLE: Alert with social proof / differentiation angle. Not fear-mongering."""

        if kind == "supply_alert":
            molecule = payload.get("molecule", "")
            batches = payload.get("affected_batches", [])
            mfr = payload.get("manufacturer", "")
            return f"""Supply alert: {molecule} recall — batches {', '.join(batches)} by {mfr}
ANGLE: Urgent — help merchant notify affected customers, offer to filter customer list."""

        if kind == "chronic_refill_due":
            mols = payload.get("molecule_list", [])
            runs_out = payload.get("stock_runs_out_iso", "")
            return f"""Refill due for customer: {', '.join(mols)}
Stock runs out: {runs_out}
Delivery address saved: {payload.get('delivery_address_saved', False)}
ANGLE: Send AS MERCHANT. Proactive refill reminder with delivery option. send_as=merchant_on_behalf"""

        if kind == "category_seasonal":
            trends = payload.get("trends", [])
            return f"""Seasonal demand shift: {', '.join(trends)}
ANGLE: Suggest shelf/inventory/offer adjustments based on demand trends."""

        if kind == "gbp_unverified":
            uplift = payload.get("estimated_uplift_pct", 0)
            return f"""Google Business Profile is UNVERIFIED
Estimated visibility uplift after verification: {uplift:.0%}
Path: {payload.get('verification_path', 'postcard_or_phone_call')}
ANGLE: Frame as quick win for visibility — offer to guide through verification."""

        if kind == "cde_opportunity":
            item_id = payload.get("digest_item_id", "")
            digest_item = next((d for d in c['digest'] if d.get("id") == item_id), None)
            credits = payload.get("credits", 0)
            fee = payload.get("fee", "")
            if digest_item:
                return f"""CDE opportunity: "{digest_item.get('title')}"
Credits: {credits} | Fee: {fee}
Date: {digest_item.get('date', 'TBD')}
ANGLE: Peer recommendation — mention CDE credits and topic relevance."""
            return f"CDE opportunity: {credits} credits, {fee}."

        if kind == "wedding_package_followup":
            wedding_date = payload.get("wedding_date", "")
            next_step = payload.get("next_step_window_open", "")
            cust_name = customer.get("identity", {}).get("name", "customer") if customer else "customer"
            return f"""Bridal follow-up for {cust_name}: wedding on {wedding_date}
Trial completed: {payload.get('trial_completed', '')}
Next step: {next_step}
ANGLE: Send AS MERCHANT. Time-sensitive bridal prep. send_as=merchant_on_behalf"""

        if kind == "trial_followup":
            trial_date = payload.get("trial_date", "")
            sessions = payload.get("next_session_options", [])
            slot_str = " / ".join([s.get("label", "") for s in sessions[:2]]) if sessions else "check availability"
            cust_name = customer.get("identity", {}).get("name", "customer") if customer else "customer"
            return f"""Trial follow-up for {cust_name}: trial on {trial_date}
Next session options: {slot_str}
ANGLE: Send AS MERCHANT. Follow up on trial, offer next session. send_as=merchant_on_behalf"""

        if kind == "customer_lapsed_hard":
            days = payload.get("days_since_last_visit", 0)
            focus = payload.get("previous_focus", "")
            months = payload.get("previous_membership_months", 0)
            cust_name = customer.get("identity", {}).get("name", "customer") if customer else "customer"
            return f"""Lapsed customer: {cust_name}, {days} days since last visit
Previous focus: {focus} | Was member for {months} months
ANGLE: Send AS MERCHANT. Win-back with empathy + specific re-entry offer. send_as=merchant_on_behalf"""

        if kind == "curious_ask_due":
            template = payload.get("ask_template", "")
            return f"""Curiosity hook due: {template}
ANGLE: Ask the merchant a genuine question about their business this week. Drives engagement."""

        # Fallback for unknown triggers
        return f"""Trigger kind: {kind}
Payload: {json.dumps(payload, default=str)[:300]}
ANGLE: Compose a relevant, specific message based on the payload data."""

    # ── TICK Composition ──────────────────────────────────

    def compose_tick(self, category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None) -> dict:
        """Compose a message for a tick event."""
        kind = trigger.get("kind", "").lower()
        payload = trigger.get("payload", {})
        m = self._mine_merchant(merchant)

        # Build the prompt
        prompt = self._build_tick_prompt(category, merchant, trigger, customer)

        # Call LLM
        raw = self._call_llm(SYSTEM_PROMPT, prompt)
        result = self._extract_json(raw)

        body = result.get("body", "")

        # Validation: if LLM failed, try raw text extraction
        if not body or len(body) < 10:
            # Try to use raw text if JSON parse failed
            clean = re.sub(r'```.*?```', '', raw, flags=re.DOTALL).strip()
            if len(clean) > 20 and len(clean) < 500:
                body = clean
            else:
                return {"action": "wait", "body": "", "rationale": f"LLM failed for {kind}"}

        # Strip URLs
        body = re.sub(r'https?://\S+|www\.\S+', '', body).strip()

        # Determine send_as from trigger scope
        scope = trigger.get("scope", "merchant")
        send_as_hint = "merchant_on_behalf" if (scope == "customer" or customer) else "vera"
        # Also check if trigger context suggested send_as
        if "merchant_on_behalf" in self._get_trigger_context(kind, payload, self._mine_category(category), m, customer):
            send_as_hint = "merchant_on_behalf"

        owner = m["owner"]
        return {
            "action": "send",
            "body": body,
            "cta": result.get("cta", "binary_yes_no"),
            "send_as": send_as_hint,
            "suppression_key": trigger.get("suppression_key", ""),
            "template_name": f"vera_{kind}_v1",
            "template_params": [owner, body[:50], ""],
            "rationale": result.get("rationale", f"Trigger: {kind}")
        }

    # ── REPLY Composition ─────────────────────────────────

    def compose_reply(self, conversation_id: str, merchant_id: str, customer_id: Optional[str],
                      from_role: str, message: str, turn_number: int,
                      merchant: Optional[dict], customer: Optional[dict],
                      category: Optional[dict], history: list[dict]) -> dict:
        m_name = merchant.get("identity", {}).get("name", "Merchant") if merchant else "Merchant"
        o_name = merchant.get("identity", {}).get("owner_first_name", m_name) if merchant else "Merchant"

        # Build history string
        hist_lines = []
        for t in history[-6:]:
            role = t.get("from", "unknown").upper()
            msg = t.get("msg", "")[:150]
            hist_lines.append(f"  [{role}]: {msg}")
        hist = "\n".join(hist_lines)

        # Extract merchant context if available
        ctx_block = ""
        if merchant:
            m = self._mine_merchant(merchant)
            ctx_block = f"""Merchant: {m['name']} ({m['locality']}, {m['city']})
Active offers: {', '.join(m['offer_titles']) if m['offer_titles'] else 'None'}
Performance: {m['views']} views, {m['calls']} calls, CTR {m['ctr']:.1%}"""

        user_msg = f"""{ctx_block}
OWNER NAME: {o_name}
LATEST MESSAGE FROM {'MERCHANT' if from_role == 'merchant' else 'CUSTOMER'}: "{message}"
CONVERSATION HISTORY:
{hist}

Turn: {turn_number}

Respond naturally in Hinglish. If merchant shows intent to act, switch to ACTION mode (give concrete next steps).
If merchant seems done, action should be "end".
Keep response under 3 lines. ONE CTA max."""

        res = self._extract_json(self._call_llm(SYSTEM_PROMPT, user_msg))

        # Ensure valid action
        action = res.get("action", "send")
        if action not in ("send", "wait", "end"):
            action = "send"

        body = res.get("body", "")
        if body:
            body = re.sub(r'https?://\S+|www\.\S+', '', body).strip()

        return {
            "action": action,
            "body": body,
            "cta": res.get("cta", "open_ended"),
            "rationale": res.get("rationale", ""),
        }
