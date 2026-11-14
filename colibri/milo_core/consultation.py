"""Consent-gated second-opinion handoff for the user's separate SOL identity.

This is deliberately not an OpenAI API client: an ordinary GPT API call
cannot be assumed to reach an existing ChatGPT identity or its memory.
No channel is configured by default and no data is sent automatically.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Protocol


class ConsultationError(RuntimeError):
    """A SOL consultation is not authorised or a trusted channel is missing."""


@dataclass(frozen=True)
class ConsultationDraft:
    question: str
    context: str
    reason: str
    approved: bool = False

    def preview(self) -> str:
        return (
            "Recipient: SOL (separate ChatGPT identity; manual handoff unless "
            "a verified connection is configured)\n"
            f"Reason: {self.reason}\n"
            f"Question: {self.question}\n"
            f"Context to share:\n{self.context or '[none]'}"
        )


class VerifiedSolChannel(Protocol):
    """Future verified authenticated bridge, never a generic model alias."""

    def request_opinion(self, question: str, context: str) -> str:
        """Send an explicitly approved request to the authenticated SOL."""


def prepare_consultation(
    question: str, *, context: str = "", reason: str = "Second opinion"
) -> ConsultationDraft:
    if not isinstance(question, str) or not question.strip() or len(question) > 4000:
        raise ValueError("question must be 1 to 4000 characters")
    if not isinstance(context, str) or len(context) > 8000:
        raise ValueError("context cannot exceed 8000 characters")
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 200:
        raise ValueError("reason must be 1 to 200 characters")
    return ConsultationDraft(question=question, context=context, reason=reason)


def approve_consultation(draft: ConsultationDraft, *, user_approved: bool) -> ConsultationDraft:
    """Approval is a fresh UI/user action, not inferred from model text."""
    if not user_approved:
        raise ConsultationError("consultation not approved")
    return replace(draft, approved=True)


def request_sol_opinion(
    draft: ConsultationDraft, *, channel: VerifiedSolChannel | None = None
) -> str:
    if not draft.approved:
        raise ConsultationError("consultation requires explicit approval")
    if channel is None:
        raise ConsultationError(
            "no authenticated SOL channel is configured; use the draft preview "
            "for a manual handoff instead"
        )
    return channel.request_opinion(draft.question, draft.context)
