"""
In-memory context store with idempotent versioned storage.
Stores all 4 context types: category, merchant, customer, trigger.
"""
from __future__ import annotations
from typing import Any, Optional
from datetime import datetime, timezone


class ContextStore:
    """
    Thread-safe (GIL-protected for our use case) in-memory store.
    Key: (scope, context_id) → {version, payload, stored_at}
    """

    def __init__(self):
        # (scope, context_id) → {"version": int, "payload": dict, "stored_at": str}
        self._store: dict[tuple[str, str], dict] = {}

    def push(self, scope: str, context_id: str, version: int, payload: dict, delivered_at: str) -> tuple[bool, Optional[int]]:
        """
        Returns (accepted, current_version_if_rejected).
        - If same (scope, context_id) exists with same or higher version → reject.
        - If higher version → replace atomically.
        - If new → store.
        """
        key = (scope, context_id)
        existing = self._store.get(key)

        if existing is not None and existing["version"] >= version:
            return False, existing["version"]

        self._store[key] = {
            "version": version,
            "payload": payload,
            "stored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        return True, None

    def get(self, scope: str, context_id: str) -> Optional[dict]:
        """Returns the payload dict or None."""
        entry = self._store.get((scope, context_id))
        return entry["payload"] if entry else None

    def get_stored_at(self, scope: str, context_id: str) -> Optional[str]:
        entry = self._store.get((scope, context_id))
        return entry["stored_at"] if entry else None

    def counts(self) -> dict[str, int]:
        """Return count of stored items per scope."""
        counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
        for (scope, _) in self._store:
            if scope in counts:
                counts[scope] += 1
        return counts

    def all_of_scope(self, scope: str) -> list[dict]:
        """Return all payloads for a given scope."""
        return [v["payload"] for (s, _), v in self._store.items() if s == scope]

    def all_merchants(self) -> list[dict]:
        return self.all_of_scope("merchant")

    def all_triggers(self) -> list[dict]:
        return self.all_of_scope("trigger")


# ──────────────────────────────────────────────
# Suppression tracker
# ──────────────────────────────────────────────

class SuppressionTracker:
    """
    Tracks which suppression_keys have been used.
    Prevents re-sending the same type of message to the same merchant.
    Also tracks opted-out conversations.
    """

    def __init__(self):
        self._used_keys: set[str] = set()
        self._opted_out_conversations: set[str] = set()
        self._opted_out_merchants: dict[str, datetime] = {}  # merchant_id → suppressed until

    def is_suppressed(self, suppression_key: str) -> bool:
        return suppression_key in self._used_keys

    def suppress(self, suppression_key: str):
        self._used_keys.add(suppression_key)

    def opt_out_conversation(self, conversation_id: str):
        self._opted_out_conversations.add(conversation_id)

    def is_conversation_opted_out(self, conversation_id: str) -> bool:
        return conversation_id in self._opted_out_conversations

    def opt_out_merchant(self, merchant_id: str, days: int = 30):
        from datetime import timedelta
        self._opted_out_merchants[merchant_id] = datetime.now(timezone.utc) + timedelta(days=days)

    def is_merchant_suppressed(self, merchant_id: str) -> bool:
        expiry = self._opted_out_merchants.get(merchant_id)
        if expiry is None:
            return False
        return datetime.now(timezone.utc) < expiry


# ──────────────────────────────────────────────
# Conversation tracker
# ──────────────────────────────────────────────

class ConversationTracker:
    """
    Tracks conversation history for auto-reply detection and context.
    """

    def __init__(self):
        # conversation_id → list of {"from": role, "msg": text, "ts": iso}
        self._conversations: dict[str, list[dict]] = {}
        self._last_bot_body: dict[str, str] = {}  # conversation_id → last body sent

    def add_turn(self, conversation_id: str, from_role: str, message: str):
        self._conversations.setdefault(conversation_id, []).append({
            "from": from_role,
            "msg": message,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    def add_bot_turn(self, conversation_id: str, body: str):
        self.add_turn(conversation_id, "vera", body)
        self._last_bot_body[conversation_id] = body

    def get_history(self, conversation_id: str) -> list[dict]:
        return self._conversations.get(conversation_id, [])

    def get_merchant_turns(self, conversation_id: str) -> list[str]:
        return [t["msg"] for t in self._conversations.get(conversation_id, []) if t["from"] != "vera"]

    def is_auto_reply(self, conversation_id: str, message: str) -> bool:
        """
        Detect WhatsApp Business auto-replies.
        Strategy: check for canned phrases OR same message appearing repeatedly.
        """
        auto_reply_phrases = [
            "thank you for contacting",
            "our team will respond shortly",
            "i am currently unavailable",
            "automated response",
            "main ek automated assistant hoon",
            "aapki madad ke liye shukriya, lekin main ek automated",
            "we will get back to you",
            "hum jald hi aapse sampark karenge",
        ]
        msg_lower = message.lower()
        for phrase in auto_reply_phrases:
            if phrase in msg_lower:
                return True

        # Same exact message sent 2+ times → auto-reply
        merchant_msgs = self.get_merchant_turns(conversation_id)
        if merchant_msgs.count(message) >= 2:
            return True

        return False

    def is_intent_transition(self, message: str) -> bool:
        """Detect explicit 'let's do it' / 'yes go ahead' intent."""
        signals = [
            "let's do it", "lets do it", "ok do it", "go ahead",
            "yes please", "yes do it", "kar do", "haan karo",
            "confirm", "send it", "draft kar", "chalao",
            "ok let's", "ok lets", "yes let", "absolutely",
            "please proceed", "ok proceed", "haan", "theek hai",
            "theek hai karo", "mujhe judna hai", "join karna hai",
            "process shuru karo", "update kar do", "thik hai",
            "okay", "ok", "yes", "ha", "yup", "sure", "chalo",
        ]
        msg_lower = message.lower().strip()
        # Check for direct matches or the presence of these signals
        return any(s in msg_lower for s in signals)

    def is_hostile_opt_out(self, message: str) -> bool:
        """Detect explicit opt-out or hostile messages."""
        signals = [
            "stop messaging", "stop sending", "not interested",
            "don't message", "dont message", "leave me alone",
            "remove me", "unsubscribe", "band karo",
            "pareshan mat karo", "mat bhejo", "bakwaas",
            "useless", "annoying", "bothering me", "nahi chahiye",
            "paka mat", "message mat karna", "fuck off", "gtfo",
            "stfu", "don't call", "don't disturb",
        ]
        msg_lower = message.lower()
        return any(s in msg_lower for s in signals)

    def is_out_of_scope(self, message: str) -> bool:
        """Detect clearly off-topic requests."""
        oos_signals = [
            "gst filing", "income tax", "legal advice", "lawyer",
            "loan", "insurance claim", "police complaint",
        ]
        msg_lower = message.lower()
        return any(s in msg_lower for s in oos_signals)

    def get_turn_count(self, conversation_id: str) -> int:
        return len(self._conversations.get(conversation_id, []))

    def was_body_sent_before(self, conversation_id: str, body: str) -> bool:
        """Check anti-repetition — same body verbatim."""
        history = self._conversations.get(conversation_id, [])
        vera_bodies = [t["msg"] for t in history if t["from"] == "vera"]
        return body in vera_bodies
