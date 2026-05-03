# Vera Bot — magicpin AI Challenge Submission

## Approach

**Architecture: Context-First Chain-of-Thought Reasoning**

Instead of a single-prompt template filler, Vera Bot uses a multi-step reasoning approach:

1. **Signal Selection** — Given category, merchant, trigger, and optional customer context, the LLM first identifies the *single most important driving signal* for this moment. Not all signals; the one that should trigger action now.

2. **Voice Calibration** — The system routes composition through category-specific voice rules baked into the system prompt. Dentists get peer-clinical tone. Restaurants get operator-peer voice. Pharmacies get trustworthy-precise language with molecule names and batch numbers.

3. **Message Composition** — The message is drafted using only facts from the provided context (no hallucination). Every number, date, and citation must trace back to a context field.

4. **Post-LLM Validation** — Python-level checks ensure: no URLs, no repeated body, correct CTA shape, suppression key present.

## Model Choice

**Groq — llama-3.3-70b-versatile**

Chosen for:
- Ultra-low latency (~1-3s response time) which is critical for the 30s challenge budget (and the judge's tighter 15s timeout).
- Exceptional reasoning depth (70B parameters) ensuring high specificity and category fit.
- Better stability on Groq compared to larger models like 120B during high-concurrency tick windows.

## Bonus Features Implemented

- **Auto-reply detection** — Detects WhatsApp Business canned auto-replies by phrase matching + message repetition. Progressive backoff: wait 4h → wait 24h → end.
- **Intent transition** — Detects "ok let's do it" / "haan karo" and switches from qualifying to action mode immediately.
- **Hostile/opt-out handling** — Detects explicit opt-out language and suppresses merchant for 30 days.
- **Out-of-scope redirection** — Politely declines off-topic requests (GST, legal) and redirects to original thread.
- **Language adaptation** — Detects Hindi in merchant languages and uses Hinglish automatically.
- **Suppression tracking** — Tracks suppression_keys to prevent re-sending the same message type.
- **Restraint** — Returns empty actions[] when no trigger is worth sending (better to be silent than spam).

## Tradeoffs

- **In-memory store** — Fast but requires no restarts. For production: Redis or SQLite with WAL mode.
- **Temperature=0** — Fully deterministic as required. Tradeoff: slightly less creative, but consistent.
- **Single LLM call per compose** — Keeps latency under 5s. Multi-step reasoning is embedded in the prompt rather than chained.

## What Additional Context Would Have Helped

- Merchant's actual WhatsApp conversation history (last 30 days) for better personalization
- Competitor pricing data for the locality (to make loss-aversion messages more concrete)
- Merchant's historical response rate by time of day (to optimize send timing)
- Google Search Console data for each merchant's locality queries

## Scale Note

For production at 300K+ merchants: 3-layer funnel — rule engine (98% suppressed) → embedding retrieval (template-matched) → LLM only for novel cases. Reduces LLM calls by ~99% while maintaining output quality.
