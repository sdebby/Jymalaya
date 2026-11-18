"""
Claude API client for the recommendation engine and coach chat.

Model: ANTHROPIC_MODEL in .env for plan generation and workout analysis
(defaults to claude-sonnet-4-6 -- the athlete picked Sonnet for speed/cost);
ANTHROPIC_COACH_MODEL for the coach chat (defaults to claude-sonnet-5-5).
The API key comes from ANTHROPIC_API_KEY in .env -- we .strip() it so a stray
leading space in the file doesn't corrupt auth.

Thinking is explicitly turned OFF on every call (_thinking_off: "disabled", or
"between_tools" on Sonnet 5.5, which rejects "disabled"). On Sonnet 5 omitting the
`thinking` parameter silently enables adaptive thinking, and max_tokens caps
thinking + answer combined -- plan generation then spends the whole budget
thinking and returns no JSON. Explicit {"type": "disabled"} restores the
fast non-thinking behaviour this app was designed around (sub-30s plans,
snappy coach replies). Effort (how hard the model works per call) is tunable
via ANTHROPIC_EFFORT in .env; unset means the API default ("high").
"""

from __future__ import annotations

import json
import logging
import os
import re

import anthropic
import pydantic

from app import plan_editor, settings_editor
from app.config import ROOT
from app.plan_editor import PlanEditError

# DEBUG level dumps full Claude API inputs/outputs to the backend terminal
# (handler + level configured in main.py). Never logs the API key.
log = logging.getLogger("jymalaya.claude")

MODEL = (os.getenv("ANTHROPIC_MODEL") or "").strip() or "claude-sonnet-4-6"


def _env_int(name: str, default: int) -> int:
    """Positive int from the environment; fall back to default on bad values."""
    raw = (os.getenv(name) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


# Max output tokens per call, overridable in .env (see .env.example).
# Plan default 16000: headroom for Sonnet 5's tokenizer (~30% more tokens for
# the same JSON than Sonnet 4.6); the call streams, so the size is safe.
PLAN_MAX_TOKENS = _env_int("ANTHROPIC_PLAN_MAX_TOKENS", 16000)
COACH_MAX_TOKENS = _env_int("ANTHROPIC_COACH_MAX_TOKENS", 2048)
# Analysis default 4000: one workout review, much smaller than a 7-day plan.
ANALYSIS_MAX_TOKENS = _env_int("ANTHROPIC_ANALYSIS_MAX_TOKENS", 4000)
# The season plan's read of the coach instructions: a short JSON object.
SEASON_INSTRUCTIONS_MAX_TOKENS = _env_int("ANTHROPIC_SEASON_INSTRUCTIONS_MAX_TOKENS", 1200)

# The coach chat runs on Sonnet 5.5 (ANTHROPIC_COACH_MODEL); plan generation and
# workout analysis stay on MODEL.
COACH_MODEL = (os.getenv("ANTHROPIC_COACH_MODEL") or "").strip() or "claude-sonnet-5-5"

# See module docstring: without this, Sonnet 5 defaults to adaptive thinking
# and can exhaust max_tokens before emitting any text.
THINKING_OFF = {"type": "disabled"}
# Sonnet 5.5 rejects "disabled" with a 400; "between_tools" is its lowest
# thinking setting (no up-front thinking; only short progress notes between
# tool calls, which come back as thinking blocks). It takes no other field and
# is accepted at low/medium/high effort only.
THINKING_BETWEEN_TOOLS = {"type": "between_tools"}


def _thinking_off(model: str) -> dict:
    """The `thinking` value that keeps `model` running without up-front thinking."""
    if model.startswith("claude-sonnet-5-5"):
        return THINKING_BETWEEN_TOOLS
    return THINKING_OFF

# Effort (ANTHROPIC_EFFORT in .env): how hard the model works per call --
# lower is terser/faster/cheaper. Unset/invalid -> the parameter is omitted
# and the API default ("high") applies. xhigh needs Sonnet 5 / Opus 4.7+.
_EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
EFFORT = (os.getenv("ANTHROPIC_EFFORT") or "").strip().lower()
if EFFORT not in _EFFORT_LEVELS:
    EFFORT = ""
# Splatted into every create/stream call so an unset effort sends nothing.
EFFORT_KWARGS = {"output_config": {"effort": EFFORT}} if EFFORT else {}


def _effort_kwargs(model: str) -> dict:
    """EFFORT_KWARGS for `model`; between_tools models cap effort at "high"."""
    if EFFORT in ("xhigh", "max") and _thinking_off(model) is THINKING_BETWEEN_TOOLS:
        return {"output_config": {"effort": "high"}}
    return EFFORT_KWARGS


SYSTEM_PROMPT_PATH = ROOT / "prompts" / "recommendation_system.txt"
# JSON output template referenced by the prompt; sent right after it.
PLAN_FORMAT_PATH = ROOT / "prompts" / "recommendation_plan_format.json"

# Post-workout analysis (PRD section 7.9) -- same split: rules, then the
# JSON output template appended after them.
ANALYSIS_PROMPT_PATH = ROOT / "prompts" / "analysis_system.txt"
ANALYSIS_FORMAT_PATH = ROOT / "prompts" / "analysis_format.json"
# Season plan: what the athlete's coach instructions change in it.
SEASON_INSTRUCTIONS_PROMPT_PATH = ROOT / "prompts" / "season_instructions_system.txt"
SEASON_INSTRUCTIONS_FORMAT_PATH = ROOT / "prompts" / "season_instructions_format.json"

# Rule 9 of the system prompt (the periodization phase) is not shared: it is
# substituted per race distance from the goal's `type`, since a 5K and a 140.6
# at the same weeks_until_race are nowhere near the same phase.
PROMPTS_DIR = ROOT / "prompts"
PERSONALIZATION_TOKEN = "{{RACE_PERSONALIZATION}}"
DEFAULT_PERSONALIZATION = "Custom_Personalization.txt"
# Keys are the EventType literals in app/models.py (= the dashboard's Distance
# dropdown). Any unknown or missing type falls back to Custom.
PERSONALIZATION_FILES = {
    "sprint": "Sprint_Personalization.txt",
    "olympic": "Olympic_Personalization.txt",
    "70.3": "Half_IM_Personalization.txt",
    "140.6": "Full_IM_Personalization.txt",
    "marathon": "Marathon_Personalization.txt",
    "half-marathon": "Half_Marathon_Personalization.txt",
    "15K": "15K_Personalization.txt",
    "10K": "10K_Personalization.txt",
    "5K": "5K_Personalization.txt",
    "custom": DEFAULT_PERSONALIZATION,
}

# The athlete's zone bands and the strict pace rules, shared verbatim by every
# Claude call: substituted for the token in the plan and analysis prompts, and
# inserted into the coach context by prompt_builder. Pace bands are factors on
# threshold seconds, never percents of a pace -- a percent taken of a pace comes
# out FASTER, which is how the coach once put Z2 ahead of threshold.
ZONES_PROMPT_PATH = PROMPTS_DIR / "training_zones.txt"
ZONES_TOKEN = "{{TRAINING_ZONES}}"

# Max tool-use rounds per coach turn before we force a text-only answer.
MAX_TOOL_ROUNDS = 5

# Prompt caching for the coach chat (5-minute TTL). Every tool-use round, and
# every follow-up message a few minutes later, re-sends the same tools + system
# context, so it is read from the cache instead of re-processed. The marker on
# the system block is the read point that survives the chat's sliding history
# window; the top-level one caches the growing conversation tail. Plan and
# analysis calls do not cache: they run about once a day, so a cache written
# there would expire unread and only add the write cost.
CACHE = {"type": "ephemeral"}

_SESSION_SCHEMA = {
    "type": "object",
    "description": "A complete planned session object.",
    "properties": {
        "discipline": {"type": "string", "enum": ["swim", "bike", "run", "strength"]},
        "title": {"type": "string"},
        "type": {"type": "string", "description": "e.g. endurance, intervals, recovery"},
        "duration_min": {"type": "number", "exclusiveMinimum": 0},
        "estimated_tss": {"type": "number", "minimum": 0},
        "zones_target": {"type": "object", "description": "e.g. {\"Z2\": 80, \"Z4\": 20}"},
        "structure": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "segment": {"type": "string"},
                    "duration_min": {"type": "number"},
                    "zone": {"type": "string"},
                    "target_power_w": {
                        "type": "string",
                        "description": 'Bike power range in watts, e.g. "112-149"',
                    },
                    "notes": {"type": "string"},
                },
            },
        },
        "notes": {"type": "string"},
    },
    "required": ["discipline", "duration_min", "estimated_tss"],
}

_ADDRESSING = (
    "Sessions are addressed by the day's date (YYYY-MM-DD) plus the session's "
    "zero-based index within that day, exactly as shown in the plan days JSON."
)

# Every edit is saved as an athlete request that survives a plan rebuild
# (app/plan_requests.py). The model's one-line account of what was asked beats
# the athlete's raw message, which is often just "yes" to a proposal.
_ATHLETE_REQUEST = {
    "type": "string",
    "description": (
        "One short sentence, in the athlete's terms, of what they asked for and "
        "why, e.g. \"Group ride Saturday morning, so the long ride moves there\". "
        "Saved with the change so it is kept when the plan is rebuilt."
    ),
}

# Follow-up questions for the chat's three suggestion chips. The coach writes
# them at the end of its own reply (no second Claude call); split_suggestions
# cuts them off before the reply reaches the athlete or the chat history.
SUGGESTIONS_MARKER = "<<suggestions>>"
MAX_SUGGESTIONS = 3
MAX_SUGGESTION_CHARS = 80
COACH_SUGGESTIONS_RULE = (
    "FOLLOW-UP SUGGESTIONS: end every reply with a final line holding only "
    f"{SUGGESTIONS_MARKER} and, on the next line, a JSON array of exactly "
    f"{MAX_SUGGESTIONS} short questions the athlete is likely to ask next. "
    "Write them in the athlete's own voice (first person), specific to this "
    "conversation and to what you just answered, each under 60 characters, "
    "plain text with no emoji or markdown. When your reply asks the athlete a "
    "question, make them likely answers instead. Never mention this block in "
    "the reply itself. Example:\n"
    f"{SUGGESTIONS_MARKER}\n"
    '["Can I move it to Thursday?", "What pace for the intervals?", '
    '"Is my form OK for this?"]'
)


def split_suggestions(text: str) -> tuple[str, list[str]]:
    """
    Cut the suggestions block off a coach reply: (reply, suggestions).

    Everything from the last marker on is removed from the reply. A block that
    is not a JSON list of strings yields no suggestions; a reply with no marker
    comes back untouched.
    """
    head, sep, tail = (text or "").rpartition(SUGGESTIONS_MARKER)
    if not sep:
        return text, []
    found: list[str] = []
    match = re.search(r"\[.*\]", tail, re.DOTALL)
    if match:
        try:
            items = json.loads(match.group(0))
        except ValueError:
            items = []
        for item in items if isinstance(items, list) else []:
            q = " ".join(item.split()) if isinstance(item, str) else ""
            if q and len(q) <= MAX_SUGGESTION_CHARS and q not in found:
                found.append(q)
    return head.rstrip(), found[:MAX_SUGGESTIONS]


# The first line of the coach's bold approval message for a settings change.
# update_athlete_settings is refused unless the coach's previous reply carried
# it: the athlete must have seen the change before it is saved (their call).
SETTINGS_APPROVAL_MARKER = "**Approve settings change?**"
SETTINGS_TOOL = "update_athlete_settings"

COACH_TOOLS = [
    {
        "name": "update_plan_session",
        "description": (
            "Replace one planned session in this week's stored plan. Supply the "
            "COMPLETE new session object (all fields, not just the changed ones). "
            + _ADDRESSING
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Plan day date, YYYY-MM-DD"},
                "session_index": {"type": "integer", "minimum": 0},
                "session": _SESSION_SCHEMA,
                "athlete_request": _ATHLETE_REQUEST,
            },
            "required": ["date", "session_index", "session", "athlete_request"],
        },
    },
    {
        "name": "move_plan_session",
        "description": (
            "Move a planned session to a different day of this week's plan. "
            "Only call this after the athlete confirms, if the target day is a "
            "rest day. " + _ADDRESSING
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "from_date": {"type": "string", "description": "Current day, YYYY-MM-DD"},
                "session_index": {"type": "integer", "minimum": 0},
                "to_date": {"type": "string", "description": "Target day, YYYY-MM-DD"},
                "athlete_request": _ATHLETE_REQUEST,
            },
            "required": ["from_date", "session_index", "to_date", "athlete_request"],
        },
    },
    {
        "name": "remove_plan_session",
        "description": "Delete one planned session from this week's stored plan. " + _ADDRESSING,
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Plan day date, YYYY-MM-DD"},
                "session_index": {"type": "integer", "minimum": 0},
                "athlete_request": _ATHLETE_REQUEST,
            },
            "required": ["date", "session_index", "athlete_request"],
        },
    },
    {
        "name": "add_plan_session",
        "description": "Add a new planned session to a day of this week's stored plan. " + _ADDRESSING,
        "input_schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Plan day date, YYYY-MM-DD"},
                "session": _SESSION_SCHEMA,
                "athlete_request": _ATHLETE_REQUEST,
            },
            "required": ["date", "session", "athlete_request"],
        },
    },
    {
        "name": "adjust_season_plan",
        "description": (
            "Change the athlete's SEASON plan: the weekly TSS targets, not this "
            "week's sessions. Use it only when the athlete asks to change a "
            "week's overall load, take a break, or change their weekly hours or "
            "build-up pace. A week changed this way is pinned -- later syncs "
            "never move it -- and the weeks after it re-flow. The taper and race "
            "week cannot be changed here; they follow the race date. The 7-day "
            "plan picks the change up at the next Sync or Re-sync plan, so say "
            "so. Operations: set_week_target (week_start + tss), make_recovery "
            "(week_start), pause (days from today: the weeks inside the break "
            "rest, the week the athlete returns in is a return week at 60%), "
            "set_hours_cap (hours; 0 removes the limit), set_ramp (ramp), "
            "set_split (week_start + swim_pct, bike_pct, run_pct: whole percents "
            "of the week's TSS adding up to 100; triathlon plans only). Report "
            "any warnings the result carries."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["set_week_target", "make_recovery", "pause", "set_hours_cap", "set_ramp", "set_split"],
                },
                "week_start": {
                    "type": "string",
                    "description": "First day of the season week, YYYY-MM-DD (set_week_target, make_recovery, set_split)",
                },
                "tss": {"type": "number", "minimum": 0, "description": "The week's TSS target (set_week_target)"},
                "days": {"type": "integer", "minimum": 1, "maximum": 60, "description": "Length of the break (pause)"},
                "hours": {"type": "number", "minimum": 0, "maximum": 40, "description": "Weekly hours available (set_hours_cap)"},
                "ramp": {"type": "string", "enum": ["gentle", "standard", "ambitious"], "description": "Build-up pace (set_ramp)"},
                "swim_pct": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Swim share of the week's TSS, percent (set_split)"},
                "bike_pct": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Bike share of the week's TSS, percent (set_split)"},
                "run_pct": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Run share of the week's TSS, percent (set_split)"},
                "athlete_request": _ATHLETE_REQUEST,
            },
            "required": ["operation", "athlete_request"],
        },
    },
    {
        "name": SETTINGS_TOOL,
        "description": (
            "Change the athlete's settings: profile thresholds (bike FTP, LTHR, run "
            "threshold pace, critical swim speed), weight, the VO2max decline "
            "threshold, and the standing coach instructions. Call it ONLY after the "
            "athlete approved the exact change in reply to your bold approval "
            "message (the line " + SETTINGS_APPROVAL_MARKER + " then one bold line "
            "per change); without that message in your previous reply the call is "
            "refused. Send only the profile values that change. coach_instructions "
            "is the COMPLETE new text that replaces the saved one (an empty string "
            "clears it), so keep what is there unless the athlete asked to remove "
            "it. A threshold change also recalculates the last 7 days' TSS. The "
            "birthday, activity class and training schedule cannot be changed here."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "profile": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "ftp_w": {"type": "number", "exclusiveMinimum": 0, "description": "Bike FTP, watts"},
                        "lthr_bpm": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "description": "Lactate threshold heart rate, bpm",
                        },
                        "run_threshold_pace_sec_km": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "description": "Run threshold pace in SECONDS per km (5:21/km = 321)",
                        },
                        "css_sec_100m": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "description": "Critical swim speed in SECONDS per 100 m (1:45/100m = 105)",
                        },
                        "weight_kg": {"type": "number", "exclusiveMinimum": 0},
                        "vo2max_decline_threshold": {
                            "type": "number",
                            "exclusiveMinimum": 0,
                            "maximum": 5,
                            "description": "4-week VO2max drop, ml/kg/min, that asks for a VO2max session",
                        },
                    },
                },
                "coach_instructions": {
                    "type": "string",
                    "maxLength": 4000,
                    "description": "The complete new standing instructions text",
                },
                "athlete_request": _ATHLETE_REQUEST,
            },
            "required": ["athlete_request"],
        },
    },
]


class ClaudeError(RuntimeError):
    """Raised with a user-facing message when a Claude call can't complete."""


def _client() -> anthropic.Anthropic:
    key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise ClaudeError(
            "ANTHROPIC_API_KEY is not set. Add your Anthropic key to .env to enable "
            "the recommendation engine and coach."
        )
    return anthropic.Anthropic(api_key=key)


def _first_text(resp) -> str:
    for block in resp.content:
        if block.type == "text":
            return block.text
    return ""


def _fmt(obj) -> str:
    """Pretty JSON for debug logs; falls back to str() for SDK objects."""
    try:
        return json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(obj)


def _fmt_content(content) -> str:
    """Render a response's content blocks (text + tool calls) for the log."""
    parts = []
    for b in content:
        if b.type == "text":
            parts.append(f"[text]\n{b.text}")
        elif b.type == "tool_use":
            parts.append(f"[tool_use {b.name} id={b.id}]\n{_fmt(b.input)}")
        else:
            parts.append(f"[{b.type}]")
    return "\n".join(parts) or "(empty)"


def _log_response(tag: str, resp) -> None:
    usage = getattr(resp, "usage", None)
    log.debug(
        ">>> Claude response (%s) stop_reason=%s tokens in=%s out=%s "
        "cache_read=%s cache_write=%s\n%s",
        tag,
        resp.stop_reason,
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "output_tokens", "?"),
        getattr(usage, "cache_read_input_tokens", "?"),
        getattr(usage, "cache_creation_input_tokens", "?"),
        _fmt_content(resp.content),
    )


def _parse_plan_json(text: str) -> dict:
    """Parse the model's JSON, tolerating stray prose or ``` fences."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the outermost {...} span.
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(cleaned[start : end + 1])
            except json.JSONDecodeError as e:
                raise ClaudeError(f"Claude returned unparseable plan JSON: {e}") from e
        raise ClaudeError("Claude did not return JSON for the plan.")


def _personalization_text(goal_type: str | None) -> str:
    """Rule 9 for this race distance, falling back to the Custom file."""
    name = PERSONALIZATION_FILES.get(goal_type or "", DEFAULT_PERSONALIZATION)
    path = PROMPTS_DIR / name
    if not path.exists():
        log.warning(
            "Personalization file %s missing for goal type %r; using %s",
            name,
            goal_type,
            DEFAULT_PERSONALIZATION,
        )
        path = PROMPTS_DIR / DEFAULT_PERSONALIZATION
    return path.read_text(encoding="utf-8").rstrip()


def training_zones_text() -> str:
    """The shared zone bands + pace rules (prompts/training_zones.txt)."""
    return ZONES_PROMPT_PATH.read_text(encoding="utf-8").rstrip()


def _payload_json(payload: dict) -> str:
    """
    A data package as compact JSON for the user message. Indentation tells the
    model nothing and costs tokens: measured 2026-11-07, indent=2 made the plan
    call 5.5% and the workout analysis 23% larger.
    """
    return json.dumps(payload, separators=(",", ":"), default=str)


def _build_plan_system(payload: dict) -> str:
    """
    The plan system prompt: shared rules with rule 9 substituted for the next
    goal's race distance and the training zones filled in, then the JSON
    output template.

    The prompt ends by pointing at the JSON template "that follows this prompt"
    -- appending the template keeps that reference true.
    """
    goal = payload.get("primary_goal") or {}
    return (
        SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
        .replace(PERSONALIZATION_TOKEN, _personalization_text(goal.get("type")))
        .replace(ZONES_TOKEN, training_zones_text())
        .rstrip()
        + "\n\n"
        + PLAN_FORMAT_PATH.read_text(encoding="utf-8")
    )


def _build_analysis_system() -> str:
    """
    The analysis system prompt: its rules with the training zones filled in,
    then the JSON output template -- the same split as _build_plan_system.
    """
    return (
        ANALYSIS_PROMPT_PATH.read_text(encoding="utf-8")
        .replace(ZONES_TOKEN, training_zones_text())
        .rstrip()
        + "\n\n"
        + ANALYSIS_FORMAT_PATH.read_text(encoding="utf-8")
    )


def generate_plan(payload: dict) -> dict:
    """
    Generate the structured 7-day plan from the assembled data package.

    Streamed (via .get_final_message) so a large response can't trip the SDK's
    HTTP idle timeout. Thinking is explicitly disabled -- the guardrail system
    prompt is explicit, and non-thinking generation keeps this well under the
    PRD's 30s target (adaptive thinking pushed it past 2 minutes, and on
    Sonnet 5 it silently turns on when the parameter is omitted).
    """
    client = _client()
    system = _build_plan_system(payload)
    user = (
        "Here is the athlete's data package. Generate the training plan for the "
        "next 7 days (starting today), respecting every hard constraint.\n\n"
        + _payload_json(payload)
    )
    log.debug(
        "<<< Claude request (plan generation) model=%s max_tokens=%d thinking=off "
        "effort=%s\n--- system ---\n%s\n--- user ---\n%s",
        MODEL,
        PLAN_MAX_TOKENS,
        EFFORT or "(default)",
        system,
        user,
    )
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=PLAN_MAX_TOKENS,
            thinking=_thinking_off(MODEL),
            system=system,
            messages=[{"role": "user", "content": user}],
            **_effort_kwargs(MODEL),
        ) as stream:
            resp = stream.get_final_message()
    except anthropic.APIError as e:
        raise ClaudeError(f"Claude API error generating plan: {e}") from e
    _log_response("plan generation", resp)
    return _parse_plan_json(_first_text(resp))


def generate_analysis(payload: dict) -> dict:
    """
    Review one completed workout and return the structured analysis JSON.

    Not streamed, unlike generate_plan: the response is a single session review,
    an order of magnitude smaller than a 7-day plan, so a plain create() stays
    well inside the SDK's idle timeout. Thinking is explicitly disabled for the
    same reason it is everywhere else -- on Sonnet 5 omitting the parameter
    silently enables adaptive thinking, which can eat the whole token budget
    before any JSON is emitted.
    """
    client = _client()
    system = _build_analysis_system()
    user = (
        "Here is the completed session and its context. Analyze how it went.\n\n"
        + _payload_json(payload)
    )
    log.debug(
        "<<< Claude request (workout analysis) model=%s max_tokens=%d thinking=off "
        "effort=%s\n--- system ---\n%s\n--- user ---\n%s",
        MODEL,
        ANALYSIS_MAX_TOKENS,
        EFFORT or "(default)",
        system,
        user,
    )
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=ANALYSIS_MAX_TOKENS,
            thinking=_thinking_off(MODEL),
            system=system,
            messages=[{"role": "user", "content": user}],
            **_effort_kwargs(MODEL),
        )
    except anthropic.APIError as e:
        raise ClaudeError(f"Claude API error analyzing workout: {e}") from e
    _log_response("workout analysis", resp)
    return _parse_plan_json(_first_text(resp))


def generate_season_instructions(payload: dict) -> dict:
    """
    Read the athlete's coach instructions for the season plan: which sports a
    week includes and the load settings the text states, as JSON
    (prompts/season_instructions_format.json). app/season/instructions.py
    validates and clamps whatever comes back -- nothing here is trusted as is.
    Not streamed and thinking off, like generate_analysis: the answer is small.
    """
    client = _client()
    system = (
        SEASON_INSTRUCTIONS_PROMPT_PATH.read_text(encoding="utf-8").rstrip()
        + "\n\n"
        + SEASON_INSTRUCTIONS_FORMAT_PATH.read_text(encoding="utf-8")
    )
    user = (
        "Here are the athlete's coach instructions and the season plan they apply "
        "to. Say what they change in the season plan.\n\n"
        + _payload_json(payload)
    )
    log.debug(
        "<<< Claude request (season instructions) model=%s max_tokens=%d thinking=off "
        "effort=%s\n--- system ---\n%s\n--- user ---\n%s",
        MODEL,
        SEASON_INSTRUCTIONS_MAX_TOKENS,
        EFFORT or "(default)",
        system,
        user,
    )
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=SEASON_INSTRUCTIONS_MAX_TOKENS,
            thinking=_thinking_off(MODEL),
            system=system,
            messages=[{"role": "user", "content": user}],
            **_effort_kwargs(MODEL),
        )
    except anthropic.APIError as e:
        raise ClaudeError(f"Claude API error reading the coach instructions: {e}") from e
    _log_response("season instructions", resp)
    return _parse_plan_json(_first_text(resp))


def _approval_asked(history: list[dict] | None) -> bool:
    """True when the coach's last reply in `history` asked to approve a settings change."""
    for m in reversed(history or []):
        if m.get("role") != "user":
            return SETTINGS_APPROVAL_MARKER in (m.get("text") or "")
    return False


def _run_coach_tool(
    name: str,
    tool_input: dict,
    fallback_note: str | None = None,
    *,
    settings_approved: bool = False,
) -> tuple[str, bool]:
    """
    Execute one coach tool call. Returns (result_text, is_error).

    A plan edit is saved as an athlete request with the model's
    `athlete_request` as its note, or `fallback_note` (the athlete's message)
    when it left it out. A settings change runs only with `settings_approved`:
    the coach's previous reply asked for approval (_approval_asked).
    """
    note = str(tool_input.get("athlete_request") or "").strip() or fallback_note
    try:
        if name == "update_plan_session":
            result = plan_editor.update_session(
                tool_input["date"],
                tool_input["session_index"],
                tool_input["session"],
                note=note,
            )
        elif name == "move_plan_session":
            result = plan_editor.move_session(
                tool_input["from_date"],
                tool_input["session_index"],
                tool_input["to_date"],
                note=note,
            )
        elif name == "remove_plan_session":
            result = plan_editor.remove_session(
                tool_input["date"], tool_input["session_index"], note=note
            )
        elif name == "add_plan_session":
            result = plan_editor.add_session(
                tool_input["date"], tool_input["session"], note=note
            )
        elif name == SETTINGS_TOOL:
            if not settings_approved:
                return (
                    "Not saved: the athlete has not approved this change. Ask first, "
                    "in your reply, with the bold approval message: the line "
                    f"{SETTINGS_APPROVAL_MARKER} then one bold line per change "
                    "(old -> new), and save only after they approve.",
                    True,
                )
            log.info("Coach settings change: %s", note)
            try:
                result = settings_editor.update_settings(
                    tool_input.get("profile") or None,
                    tool_input.get("coach_instructions"),
                )
            except (settings_editor.SettingsEditError, ValueError) as e:
                return str(e), True
        elif name == "adjust_season_plan":
            # Imported here: the season service imports the prompt builder,
            # which imports this module.
            from app.season import service as season_service

            try:
                result = season_service.coach_adjust(tool_input, note)
            except (season_service.NoPlanError, season_service.SeasonEditError, ValueError) as e:
                return str(e).strip("'\""), True
        else:
            return f"Unknown tool: {name}", True
    except (PlanEditError, pydantic.ValidationError, KeyError, TypeError) as e:
        return str(e), True
    return json.dumps(result), False


def coach_reply(
    message: str, history: list[dict], context: str, *, tools: bool = True
) -> tuple[str, bool, bool]:
    """
    Answer one coach-chat turn. `history` items are {role: user|coach, text}.

    Runs an agentic tool-use loop: the model may call the plan-editing tools
    (COACH_TOOLS) to modify data/latest_recommendation.json before answering.
    Returns (reply_text, plan_updated, settings_updated) — plan_updated is True
    if a plan tool call changed the stored plan, settings_updated if
    update_athlete_settings saved a change (allowed only when the coach's
    previous reply asked for approval, see _approval_asked).

    `tools=False` answers in one round with no tools at all — the "Include
    context" toggle is off, so the model cannot see the plan and must not be
    able to edit it either.
    """
    client = _client()

    conv: list[dict] = []
    for m in history or []:
        role = "user" if m.get("role") == "user" else "assistant"
        conv.append({"role": role, "content": m.get("text", "")})
    # The API requires the first message to be from the user; drop the greeting.
    while conv and conv[0]["role"] == "assistant":
        conv.pop(0)
    conv.append({"role": "user", "content": message})

    # No tools when the athlete asked without context: the model has no plan to
    # reason about, so an edit would be blind. With the key absent the first
    # response can never stop on tool_use, and the loop below returns in round 1.
    tool_kwargs = {"tools": COACH_TOOLS} if tools else {}
    # Below the model's minimum cacheable length (the short no-context prompt)
    # the markers are ignored at no cost.
    system = [{"type": "text", "text": context, "cache_control": CACHE}]

    log.debug(
        "<<< Claude request (coach) model=%s max_tokens=%d effort=%s tools=%s "
        "messages=%d\n--- system ---\n%s\n--- messages ---\n%s",
        COACH_MODEL,
        COACH_MAX_TOKENS,
        EFFORT or "(default)",
        "on" if tools else "off",
        len(conv),
        context,
        _fmt(conv),
    )
    plan_updated = False
    settings_updated = False
    settings_approved = _approval_asked(history)
    try:
        for round_no in range(MAX_TOOL_ROUNDS):
            resp = client.messages.create(
                model=COACH_MODEL,
                max_tokens=COACH_MAX_TOKENS,
                thinking=_thinking_off(COACH_MODEL),
                system=system,
                messages=conv,
                cache_control=CACHE,
                **tool_kwargs,
                **_effort_kwargs(COACH_MODEL),
            )
            _log_response(f"coach round {round_no + 1}", resp)
            if resp.stop_reason != "tool_use":
                return _first_text(resp).strip(), plan_updated, settings_updated

            conv.append({"role": "assistant", "content": resp.content})
            results = []
            for block in resp.content:
                if block.type != "tool_use":
                    continue
                text, is_error = _run_coach_tool(
                    block.name,
                    block.input or {},
                    fallback_note=message[:300],
                    settings_approved=settings_approved,
                )
                if not is_error:
                    if block.name == SETTINGS_TOOL:
                        # A change saved, not a call that found nothing to change.
                        settings_updated = settings_updated or bool(
                            json.loads(text).get("changed")
                        )
                    else:
                        plan_updated = True
                log.debug(
                    "=== coach tool %s -> %s%s",
                    block.name,
                    "ERROR: " if is_error else "",
                    text,
                )
                results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": text,
                        "is_error": is_error,
                    }
                )
            # All tool results for a turn must land in ONE user message.
            conv.append({"role": "user", "content": results})

        # Tool budget exhausted — force a plain-text answer. (Unreachable with
        # tools off: that first response can't stop on tool_use. tool_choice
        # rides with tool_kwargs so it is never sent without a tools list.)
        log.debug(
            "<<< Claude request (coach, tool budget exhausted) model=%s "
            "tool_choice=none messages=%d",
            COACH_MODEL,
            len(conv),
        )
        resp = client.messages.create(
            model=COACH_MODEL,
            max_tokens=COACH_MAX_TOKENS,
            thinking=_thinking_off(COACH_MODEL),
            system=system,
            messages=conv,
            cache_control=CACHE,
            **(dict(tool_kwargs, tool_choice={"type": "none"}) if tools else {}),
            **_effort_kwargs(COACH_MODEL),
        )
        _log_response("coach final", resp)
    except anthropic.APIError as e:
        raise ClaudeError(f"Claude API error in coach chat: {e}") from e
    return _first_text(resp).strip(), plan_updated, settings_updated
