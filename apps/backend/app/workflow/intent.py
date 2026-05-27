"""Intent classification step.

The :class:`IntentStep` is the first executor in the workflow. It receives
the full conversation history, sends a windowed slice to the intent
classifier agent under a ``response_format=IntentResult`` constraint, and
emits an :class:`IntentBundle` carrying the resolved (standalone) user
question alongside the structured classification.

Domain neutrality is enforced by the prompt — this module owns the
plumbing only.
"""

from __future__ import annotations

from agent_framework import Agent, Executor, Message, WorkflowContext, handler

from app.workflow._helpers import (
    HISTORY_TURNS,
    extract_structured_text,
    track_usage,
    windowed_history,
)
from app.workflow.prompts import INTENT_INSTRUCTIONS_TPL
from app.workflow.schemas import IntentBundle, IntentResult


class IntentStep(Executor):
    """First workflow step: classify intent + produce ``resolved_question``."""

    def __init__(
        self,
        agent: Agent,
        bu_id: int,
        usage_tracker: dict,
        id: str = "intent_step",
    ) -> None:
        super().__init__(id=id)
        self._agent = agent
        self._bu_id = bu_id
        self._usage = usage_tracker

    @handler
    async def run(
        self,
        conversation: list[Message],
        ctx: WorkflowContext[IntentBundle],
    ) -> None:
        # Original latest user message — kept for the audit bundle.
        original_question = next(
            (m.text for m in reversed(conversation) if m.role == "user" and m.text),
            "",
        )

        # Build the prompt: system instructions + the windowed conversation.
        # Note: we pass the conversation as actual ``user``/``assistant``
        # messages so the model sees the natural dialog format. This is
        # better than serializing it into a string blob.
        windowed = windowed_history(conversation, HISTORY_TURNS)
        prompt_messages: list[Message] = [
            Message(role="system", contents=[INTENT_INSTRUCTIONS_TPL]),
            *windowed,
        ]

        response = await self._agent.run(
            prompt_messages,
            options={"response_format": IntentResult},
        )
        track_usage(self._usage, "intent", response)
        intent = IntentResult.model_validate_json(extract_structured_text(response))

        # Fallback: if the model returned an empty resolved_question for any
        # reason, fall back to the raw latest user turn.
        resolved = intent.resolved_question.strip() or original_question

        await ctx.send_message(
            IntentBundle(
                user_question=resolved,
                original_question=original_question,
                bu_id=self._bu_id,
                intent_result=intent,
            )
        )


__all__ = ["IntentStep"]
