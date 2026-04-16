#!/usr/bin/env python3
"""
Agent-to-Agent (A2A) communication channel.

Rules:
- Personal messages only — no broadcasts between actors.
- Each actor has a 500-token outgoing budget per turn; excess is truncated.
- World events are broadcast via broadcast_world_event() (exempt from budget).
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OUTGOING_TOKEN_BUDGET = 500  # tokens per actor per turn


def _approx_token_count(text: str) -> int:
    """Rough token estimate: ~4 characters per token."""
    return max(1, len(text) // 4)


@dataclass
class Message:
    sender: str
    recipient: str    # specific actor name, or "*" for broadcast
    content: str
    year: int
    turn_tokens_used: int = 0   # tokens this message consumed from sender's budget
    message_type: str = "a2a"   # "a2a" | "world_event"
    metadata: Dict[str, Any] = field(default_factory=dict)


class A2AChannel:
    """
    Central message bus. Enforces per-turn outgoing token budgets.
    """

    def __init__(self):
        self._log: List[Message] = []
        # {year: {actor_name: tokens_used}}
        self._budgets: Dict[int, Dict[str, int]] = {}

    # ------------------------------------------------------------------
    # Sending
    # ------------------------------------------------------------------

    def send(self, sender: str, recipient: str, content: str, year: int,
             message_type: str = "a2a",
             metadata: Optional[Dict[str, Any]] = None) -> Optional[Message]:
        """
        Send a personal message from `sender` to a specific `recipient`.
        Broadcasts (recipient="*") are rejected — use broadcast_world_event() for that.
        Truncates content if the sender's outgoing token budget is exhausted.
        """
        if recipient == "*":
            logger.warning(f"A2A [{year}] {sender}: broadcast rejected — use broadcast_world_event()")
            return None
        if year not in self._budgets:
            self._budgets[year] = {}
        used = self._budgets[year].get(sender, 0)
        remaining = OUTGOING_TOKEN_BUDGET - used

        tokens_needed = _approx_token_count(content)

        if remaining <= 0:
            logger.debug(f"A2A [{year}] {sender}: token budget exhausted, message dropped")
            content = "[MESSAGE TRUNCATED — token budget exhausted]"
            tokens_consumed = 0
        elif tokens_needed > remaining:
            # Truncate to remaining budget
            cutoff = remaining * 4  # back-convert tokens → chars
            content = content[:cutoff] + " [TRUNCATED]"
            tokens_consumed = remaining
        else:
            tokens_consumed = tokens_needed

        self._budgets[year][sender] = used + tokens_consumed

        msg = Message(
            sender=sender,
            recipient=recipient,
            content=content,
            year=year,
            turn_tokens_used=tokens_consumed,
            message_type=message_type,
            metadata=metadata or {},
        )
        self._log.append(msg)
        logger.debug(f"A2A [{year}] {sender} → {recipient}: {content[:80]!r}")
        return msg

    def broadcast_world_event(self, description: str, year: int,
                               event_name: str = "") -> Message:
        """Inject a world event as a WORLD→* broadcast (not subject to actor budget)."""
        msg = Message(
            sender="WORLD",
            recipient="*",
            content=description,
            year=year,
            turn_tokens_used=0,
            message_type="world_event",
            metadata={"event_name": event_name},
        )
        self._log.append(msg)
        logger.info(f"A2A WORLD EVENT [{year}]: {description[:100]}")
        return msg

    # ------------------------------------------------------------------
    # Receiving
    # ------------------------------------------------------------------

    def receive(self, recipient: str, year: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Return messages visible to `recipient` for the given year:
        - Personal messages addressed directly to them
        - World event broadcasts (WORLD → *)
        """
        results = []
        for msg in self._log:
            if year is not None and msg.year != year:
                continue
            is_personal = msg.recipient == recipient
            is_world_event = msg.message_type == "world_event"
            if is_personal or is_world_event:
                results.append(self._serialize(msg))
        return results

    def get_history(self, actor: str, before_year: int) -> List[Dict[str, Any]]:
        """
        Return all personal A2A messages that `actor` sent or received in turns
        strictly before `before_year`, in chronological order.
        World events are excluded (they are surfaced separately in the prompt).
        """
        results = []
        for msg in self._log:
            if msg.year >= before_year:
                continue
            if msg.message_type == "world_event":
                continue
            if msg.sender == actor or msg.recipient == actor:
                results.append(self._serialize(msg))
        return results

    def tokens_remaining(self, actor: str, year: int) -> int:
        used = self._budgets.get(year, {}).get(actor, 0)
        return max(0, OUTGOING_TOKEN_BUDGET - used)

    def full_log(self) -> List[Dict[str, Any]]:
        return [self._serialize(m) for m in self._log]

    # ------------------------------------------------------------------

    @staticmethod
    def _serialize(msg: Message) -> Dict[str, Any]:
        return {
            "sender": msg.sender,
            "recipient": msg.recipient,
            "content": msg.content,
            "year": msg.year,
            "message_type": msg.message_type,
            "turn_tokens_used": msg.turn_tokens_used,
        }
