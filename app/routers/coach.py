"""Coach chat endpoint (powers the dashboard's Coach panel)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.ai.claude import (
    COACH_SUGGESTIONS_RULE,
    ClaudeError,
    coach_reply,
    split_suggestions,
)
from app.ai.prompt_builder import build_coach_context, build_general_coach_context
from app.ai.workout_context import attached_workouts_context
from app.instructions import get_instructions
from app.models import CoachInstructions, CoachRequest
from app.settings_editor import save_instructions_and_notify

router = APIRouter(tags=["coach"])
log = logging.getLogger("jymalaya.coach")


@router.post("/coach")
def coach(body: CoachRequest):
    # include_context=False ("Include context" unticked in the chat) answers a
    # general triathlon question: no athlete data is read or sent, and the turn
    # runs without the plan-editing tools, so a coach that cannot see the plan
    # cannot rewrite it either.
    context = (
        build_coach_context() if body.include_context else build_general_coach_context()
    )
    # A workout attached in the chat brings its full recorded data, once per
    # turn in the system context rather than in the message history. Never
    # with the context off: no training data leaves storage then.
    if body.include_context and body.workout_ids:
        attached = attached_workouts_context(body.workout_ids)
        if attached:
            context = f"{context}\n\n{attached}"
    try:
        reply, plan_updated, settings_updated = coach_reply(
            body.message,
            [m.model_dump() for m in body.history],
            # The rule rides on both contexts: the chips follow the conversation
            # with "Include context" off too.
            f"{context}\n\n{COACH_SUGGESTIONS_RULE}",
            tools=body.include_context,
        )
    except ClaudeError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e
    # The coach's follow-up questions (the chat's chips) come off the end of
    # the reply here, so the athlete and the saved history never see the block.
    reply, suggestions = split_suggestions(reply)
    return {
        "reply": reply,
        "plan_updated": plan_updated,
        "settings_updated": settings_updated,
        "suggestions": suggestions,
    }


@router.get("/coach/instructions")
def coach_instructions():
    """The athlete's standing coach instructions (empty text when none)."""
    return {"text": get_instructions()}


@router.put("/coach/instructions")
def update_coach_instructions(body: CoachInstructions):
    """
    Save the instructions; empty text clears them (nothing sent to Claude). An
    active season plan then reads them again (one small Claude call, only when
    the text changed) and adjusts its weeks from next week on; that never fails
    the save.
    """
    note = save_instructions_and_notify(body.text)
    return {"text": get_instructions(), "season_plan_note": note}
