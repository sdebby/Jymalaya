# Jymalaya AI Coach

A locally-run, single-athlete triathlon training intelligence platform. It pulls
workouts from Garmin Connect on demand, stores them in InfluxDB 2.x, computes
training-load metrics, and uses the Claude API to auto-generate a 7-day training
plan and power an interactive coach. A separate dashboard frontend (the
"Option A" design handoff) consumes the backend's REST API and is the only
visualization surface.

> **History:** this repo began as "BrickPlan," a CLI that emitted a TrainingPeaks
> CSV + static HTML report. As of 2026-06-26 the project pivoted to the platform
> described in `assets/PRD.md` (v1.1) under the working name "TriCoach AI"; on
> 2026-07-09 it was renamed "BrickPlanner", and on 2026-08-08 to
> **Jymalaya AI Coach** (repo folder `Jymalaya`). Both renames were branding
> only: the data-bearing `tricoach` identifiers — the InfluxDB bucket/org, the
> `~/.tricoach` Garmin token cache, the `tricoach.*` localStorage keys, and the
> `tricoach-brick-` UUID namespace in the parser — were deliberately kept, since
> changing them orphans stored history, forces a fresh MFA login, or resets the
> athlete's browser state. Docker container names carry the current brand and
> are safe to change. **The PRD is the authoritative
> spec**; this file captures the decisions, the reconciliations, and agent guidance.
> Where the old BrickPlan CLI design conflicts with the PRD, the PRD wins.

## Status

Phase 1 (M1–M8) complete, plus the Option A frontend. Working end-to-end:
incremental Garmin sync (token cache, MFA via dashboard modal or file handoff;
pull window = days since last successful sync + 3-day overlap, HR-zone fetches
only for activities not already stored) → parse/brick-split → TSS +
CTL/ATL/TSB recomputed from the full stored history → InfluxDB;
events CRUD (the PRD's goals) + Open-Meteo weather; Claude recommendation engine
auto-run after sync (rolling next-7-days plan cached in
`data/latest_recommendation.json`; skipped, with a dashboard notice, when
nothing new was synced and the plan is current); athlete-written custom coach
instructions (Settings modal → `GET/PUT /coach/instructions` →
`data/coach_instructions.txt`; sent in the plan payload and coach-chat context
only when non-empty, and editing them makes the cached plan stale so the next
sync regenerates); the athlete's training schedule, fully editable (Settings
"Training schedule" section → `GET/PUT /schedule` → `data/schedule.json` via
`app/schedule_store.py`; per-day rest/disciplines/sessions/type, with the week
constraints derived from the days by `_derive_constraints` — read straight
into the Claude payload, and edits make the cached
plan stale too); coach chat with **plan-editing
tools** (Anthropic tool-use loop in `app/ai/claude.py` → `app/plan_editor.py`;
sessions addressed by `(date, session_index)`; structural validation in Pydantic,
coaching guardrails in the system prompt) plus local slash commands (`/last`,
`/summary` — cached plan summary without a sync or Claude call, `/clear`,
`/help`), an **Include context** switch next to Send (default on; off asks a
general triathlon question with no athlete data and no plan-editing tools — see
the decisions bullet), and a Stop button that replaces Send while a turn is in
flight (aborts the fetch only — the backend turn keeps running, so a plan edit
it had already made stands); live sync-phase reporting on `GET /sync/status`. The React dashboard
covers all panels: a top-bar logo showing the athlete's Garmin profile image
(cached during sync, served at `GET /athlete/avatar`, gradient-square fallback),
MFA modal, sync-progress button, a "Re-sync plan" button
(`POST /recommend/trigger` — fresh plan without a Garmin sync), TSS-by-week
chart, predicted week with attach-to-chat, the "plan updated" refresh flow, a
post-sync coach-summary popup, and modal keyboard UX (Esc cancels, Ctrl+Enter
saves).

Phase 2 is done too: `Dockerfile` + `docker-compose.deploy.yml` package the
backend and the built dashboard into one container ("Jymalaya-C1") with its
own InfluxDB — see "Deployment" below. Since 2026-09-22 there is also a true
**single-container** Jymalaya-C1 (InfluxDB inside), built and packaged from
the sibling folder `..\Jymalaya-Container` to run on another computer with no source —
see "Single container" below. The two-container files were kept as they were.

Also built (2026-09-04): the **Training Days grid** — a 60-day
GitHub-contribution-style history of every training day, full width directly
below the Training Load chart. See the decisions bullet below.

Also built (2026-08-27): **post-workout analysis** — an Analyze workout button on
the completed-workout modal that pulls the activity's detailed Garmin data
(HR/power/speed/elevation/cadence at ~30 s, laps, and the structured workout it
was run against), derives the coaching metrics in code, has Claude review it, and
renders the insights in the modal with an Ask coach handoff into the chat.
See PRD §7.9 and the decisions bullet below.

Also built (2026-09-27): **plan changes survive a re-sync** -- every change the
athlete asks the coach for is saved as a requested day, and Sync Garmin /
Re-sync plan first ask whether to keep those changes or overwrite them with a
new plan. See the decisions bullet below.

Also built (2026-09-29): **Send to device** -- a button on every planned
session's modal (left of Ask coach) that puts the session on the athlete's
intervals.icu calendar as a structured workout, from where intervals.icu passes
it to Garmin Connect and the watch. See the decisions bullet below.

Also built (2026-09-30): the **Participating toggle** -- a switch in the Manage
events footer, default on; off keeps the event on the calendar but the coach
(plan, chat and analysis) never treats it as a goal. See the decisions bullet
below.

Also built (2026-10-19): the **Fatigue Zones chart** and a reorganised load block
-- the Form (TSB) line drawn over its five zones directly under the Training
Load chart, at the same width, with the Load Forecast moved up beside the KPI
tiles. See the decisions bullet below.

Also built (2026-10-20): **HRV and Resting HR trends** -- two equal cards of
the last 28 days directly under the load block, fed by Garmin's nightly HRV and
daily resting HR, which every sync now pulls too. Dashboard only: the coach does
not see them yet. See the decisions bullet below.

Also built (2026-10-23): the **Season plan** -- a persistent week-by-week plan
owned by code, in `data/season_plan.json` (`app/season/`). An **event plan**
works back from the primary participating event (base, build, peak, taper and
race week from the prompt files' own weeks-out table; 3 loading weeks then a
recovery week at 70%); a **fitness plan** (no race) decides one week at a time
through a check-in on the last day of the week (Increase / Stay / Decrease /
Recovery). Every sync adapts it and the 7-day Claude plan fills each week's
TSS budget (prompt rules 25-28). A triathlon plan also splits every week into
swim / bike / run (2026-10-25, rule 29), and every plan reads the athlete's
coach instructions for its sports and load (2026-10-26). The dashboard shows it as a weekly-TSS bar
panel with two template-written lines directly under the events row. See the
decisions bullet below.

Also built (2026-10-30): the **sync summary modal** -- the popup after a sync that
generated a plan is now charts of the last 7 days over the next 7 with two
sentences of coach notes, computed from the workouts and the plan's sessions. See
the decisions bullet below.

Also built (2026-10-31): the **waiting screen** -- the favicon over an animated
heartbeat line, shown until the backend answers, then the dashboard on its own.
See the decisions bullet below.

Also built (2026-11-01): **drag a planned workout to another day** -- a session
card in the Predicted Week panel is carried under the cursor to another day and
saved as a requested day. See the decisions bullet below. Dragging a card into
the coach chat is **not** planned -- the attach-to-chat button already covers
that.

Also built (2026-11-06): **Garmin's official Personal API** for workouts, HRV,
resting HR and the avatar, with each workout's FIT file decoded into 5 s samples
and laps in InfluxDB (the file is then deleted), and **run TSS from Normalized
Graded Pace** computed from those samples. The `garminconnect` library is left
for VO2max only, which the Personal API does not serve. The Analyze button now
reads only the database. See the decisions bullet below.

Also built (2026-11-06): **workout stream graphs** -- the completed-workout
modal draws the activity's recorded streams as stacked time-axis graphs in
place of the "Recorded metrics" tiles, plus time in HR and power zones as
horizontal bars. See the decisions bullet below.

Also built (2026-11-07): **the coach changes settings** -- in the chat, after the
athlete approves a bold approval message, the coach can change the profile
thresholds, weight, the VO2max decline threshold and the custom coach
instructions. See the decisions bullet below.

Also built (2026-11-09): **Garmin Training Effect** -- two 270-degree dials,
Aerobic and Anaerobic (0-5.0), with Garmin's primary benefit centred above them,
in the completed-workout modal directly below Time in zones. Display only. See
the decisions bullet below.

Also built (2026-11-13): the **lighter week after a monotonous load** -- every
plan build checks the last 6 complete weeks of TSS. If they were flat or only
climbing, the athlete is told so and the next 7 days are planned 15% lighter
(prompt rule 32). See the decisions bullet below.

Also built (2026-11-13): the **weekly sleep chart** -- the third card of the
distributions row, in place of TSS by Week (weekly TSS now lives in the first
card), shows average sleep per night for each of the last 8 weeks, pulled from
Garmin on every sync. Display only. See the decisions bullet below.

Also built (2026-11-14): **workout RPE and feel** -- the athlete's own rating of
a workout, read from its FIT file, as two slim tiles directly under the Training
Effect dials in the completed-workout modal. Display only. See the decisions
bullet below.

Also built (2026-11-14): **sub events** -- earlier races the athlete also runs
become stepping stones of an event season plan: a mini-taper sized by distance,
with the main event still the goal of every week and of the 7-day plan. Picked
in a new step of the create modal, offered when an earlier event is saved, and
listed (Add / Remove) on the plan panel. See the decisions bullet below.

Also built (2026-11-15): **the activity class is used** -- it picks a new season
plan's blank defaults (build-up pace, recovery spacing, peak CTL, the
new-athlete start floor), and prompt rule 34 caps the 7-day plan's quality
sessions by it. See the decisions bullet below.

Also built (2026-11-17): **workout load impact** -- three slim tiles, Fitness /
Fatigue / Form, directly above the Training Effect dials in the
completed-workout modal. See the decisions bullet below.

Also built (2026-11-21): **a workout sent to the coach chat carries its full
recorded data** -- the streams, laps, zones, ratings and analysis the modal
shows, as compact text in the coach's context. See the decisions bullet below.

## Authoritative spec

`assets/PRD.md` (Jymalaya AI Coach v1.1) — full architecture, data model, TSS/CTL/ATL/TSB
formulas, API endpoints, system prompt, project structure, and phased milestones.
Read it before implementing any module; don't restate its formulas from memory.

`assets/tss_rtss_calculation_spec.md` is the authority for the **TSS formulas
themselves** (cTSS / rTSS / sTSS / hrTSS and the fallback order) where it is
more specific than PRD §6 — swim sTSS follows it exactly (see below).

The frontend design lives in
`assets/design-handoff/triathlete-dashboard-design/project/Triathlete Dashboard.dc.html`
("Option A — classic dense grid"): topbar → goals + weather → CTL/ATL/TSB load chart
+ KPI tiles → discipline cards → volume/HR-zone/power-zone distributions → 7-day
predicted week → load forecast + pace trend + VO2max → interactive Coach chat.
Recreate it pixel-faithfully. Two deliberate divergences, both from
2026-08-26: the handoff's load-focus row has two panels and the built dashboard
has three (VO2max was added), and that row sits **above** the past/predicted
week rows rather than after them — the athlete asked for the forecast and trends
next to the distributions. (On 2026-10-19 the forecast moved out of that row,
which is two panels again: see "Fatigue Zones and the load block" below.) A
third, from 2026-09-04: the Training Days handoff
places its grid between the discipline cards and Weekly Volume by Discipline,
but the athlete moved it **above the discipline cards**, directly under the
Training Load chart — so it also sits above that row's Window dropdown.
The discipline-cards row also sits on a **tinted band** the handoff does not
have, enclosing the Window dropdown together with the four cards. It was a
bright yellow dashed frame from 2026-09-04; on 2026-11-07 the athlete swapped it
for a left-to-right gradient of swim -> bike -> run -> strength at 10% opacity
(`C.form`/`C.fat`/`C.fit`/`C.strength` + `1a`; strength is a whitish teal since 2026-11-17), so each card sits on a wash of
its own top-edge colour. Keep the stops in the cards' order.
Row order lives in `frontend/src/App.tsx`.

## Finalized decisions

- **Backend**: Python 3.12 + **FastAPI**. REST API only (no server-rendered UI).
- **Storage**: **InfluxDB 2.x**, bucket `tricoach`. All workout/metric/goal data lives
  here (see PRD §5). No TrainingPeaks read/write anywhere in the pipeline.
  **No `recommendations` measurement (2026-11-07, conscious divergence from
  PRD §5/§7.7)**: every plan was also written there as a JSON string, and
  nothing ever read it -- `data/latest_recommendation.json` is the only plan
  store. The write and the stored history (102 plans) were removed.
  The server is **InfluxDB 2.9.1** (upgraded from 2.7.12 on 2026-10-27, pinned
  exactly in both compose files). 2.9 hashes API tokens on its first start
  (the plaintext `.env` token keeps working), and **a downgrade below 2.8 then
  erases every token** -- never roll back by changing the image tag; restore
  the pre-upgrade `influx backup` (`influx-backup-pre-2.9.1-261027` in the
  BrickPlanner backup folder) into a fresh 2.7 volume. The single container's
  Dockerfile (`..\Jymalaya-Container`) copies its binaries from `influxdb:2.9.1`
  too: keep it on the same version.
- **Workout timestamps (invariant — easy to re-break)**: a workout point's time is
  the **true UTC instant**, taken from Garmin's `startTimeGMT` (`start_time_utc`);
  the athlete's local calendar day is a separate stored field, `date`, from
  `startTimeLocal`. Never stamp the local wall clock as UTC: that pushes recent
  points into the future, and since every Flux `range(start: -Nd)` read stops at
  `now()`, a just-synced activity then becomes invisible everywhere (fixed
  2026-07-26; a one-off migration repaired the stored history and was removed
  on 2026-09-28 once applied).
  Anything needing a calendar day must use the `date` field, not `time.date()` —
  the UTC date of an evening workout falls on the wrong day.
- **Visualization**: the **Option A dashboard** (React + Vite) is the only
  visualization surface. Grafana was part of the original design (PRD §7.6) and
  was removed on 2026-07-26 — everything the athlete sees now comes from the
  dashboard reading the backend's REST API.
- **Garmin access**: Garmin's official **Personal API** for workouts (since
  2026-11-06, see the Personal API bullet below); the unofficial `garminconnect`
  login for VO2max only. Manual
  sync only (`POST /sync`), 60-day window pulled **incrementally**: the first sync
  into an empty store (new install, or after a data reset, judged by the stored
  workouts since a reset keeps `last_sync`) pulls the full 60 days
  (`FIRST_SYNC_DAYS`, 28 until 2026-11-08); later syncs pull only the days
  since the last successful sync plus a 3-day overlap (late-uploaded activities). HR
  zones ride on the list rows, and a workout's FIT file is fetched only while it has
  no stored samples.
  Re-sync is idempotent (dedup by `activity_id`); a failed sync preserves the previous
  `last_sync`. Every sync recomputes TSS/IF for ALL stored workouts from the
  current athlete profile (`recompute_stored_metrics` in `app/sync_service.py`,
  also callable standalone for a backfill), then the daily CTL/ATL/TSB series
  from the full stored history (an EWA can't be seeded from a short window) —
  so threshold edits in Settings reach the whole history on the next sync. The
  athlete's Garmin profile image is also cached during sync (`refresh_avatar_from`,
  from the URL on the activity rows, at most one download per week) to `data/athlete_avatar.*` and served at
  `GET /athlete/avatar` as the dashboard logo. Every sync also pulls Garmin's
  daily running/cycling VO2max for the same incremental window into the `vo2max`
  measurement, served at `GET /api/metrics/vo2max` and drawn as the second panel
  of the load-focus row (the third until the forecast left it on 2026-10-19); `scripts/backfill_vo2max.py --apply` fills roughly a
  year retroactively. It also reaches the Claude plan payload, as a 4-week trend
  rather than daily points — see the VO2max-trend bullet below.
- **VO2max (built 2026-08-26, spec'd in PRD §7.8)**: it was built ahead of the
  spec and folded into the PRD on 2026-08-27 (v1.2) — read §7.8 for the rules;
  what follows is the implementation detail this file uniquely carries.
  Garmin's daily running (`generic`)
  and cycling (`cycling`) VO2max come from
  `/metrics-service/metrics/maxmet/daily/{start}/{end}` — a **range** endpoint
  reached through `client.connectapi` directly, the only place in the repo that
  bypasses a named `garminconnect` method (the library's `get_max_metrics`
  hardcodes start == end, i.e. one request per day). Requests are chunked at 28
  days since Garmin's span cap is undocumented, and the response is parsed
  defensively (`parse_max_metrics`) because nothing types it. The whole path is
  **best-effort and never raises**: Garmin sends `cycling: null` on days with no
  qualifying ride, cycling VO2max needs a power meter at all, and a metrics
  failure must never fail a sync that pulled activities fine. The pull uses the
  **incremental activity window** (2026-11-02, the athlete's call; it was a fixed
  28 days on every sync, nearly all of it rewriting stored points), and
  `_vo2max_days` in `app/sync_service.py` widens it to the larger of that window
  and 28 days when no reading from the last 28 days is stored. The panel reads its 56 days (8 weeks) from storage, and
  every write is an idempotent upsert keyed on (day, discipline). A value Garmin
  revises further back than the window stays as stored until the backfill script
  is re-run. Points follow the `daily_load`
  convention, **not** the workout one: stamped midnight UTC from the calendar
  date (`_parse_date`), so the point's UTC date *is* the calendar day (see
  `_row_date` in `app/metrics/calculator.py`). The frontend must read that day
  off the ISO string (`time.slice(0, 10)`), never via `new Date()`, which shifts
  it west of UTC. Garmin emits an entry only for days it has an estimate for, so
  the stored series is sparse and `vo2maxTrend` in `frontend/src/derive.ts`
  forward-fills it (back-filling the leading gap); a discipline with no readings
  is omitted from the chart entirely rather than zero-filled, which would crush
  the real series against the top of the y-axis. A full data reset
  (`DELETE /api/data`) wipes the history and the next sync restores only 60 days
  — re-run the backfill.
- **Post-workout analysis (built 2026-08-27, spec'd in PRD §7.9)**: the Analyze
  button on a completed workout's modal pulls that one activity's detailed
  Garmin data and has Claude review it. Things easy to re-break:
  (1) **On demand only** — never on the sync path, so `tests/test_sync_phase.py`'s
  pinned phase list stays `["garmin", "parsing", "metrics"]`. `open_session` in
  `app/garmin/detail.py` copies `pull_vo2max`'s token guard for the same reason:
  without it a request thread can park inside `_prompt_mfa` for 300 s.
  (2) **Brick legs carry a synthetic `activity_id`** (`{parent}_{bike|run}`, from
  `split_bricks`) that Garmin does not know — `resolve_garmin_id` walks
  `metadataDTO.childIds` to the real child first. Every detail request must go
  through it.
  (3) **No coordinates**: `maxpoly=0` plus dropping the lat/lon metrics. There is
  no map, and the polyline dwarfs everything else in the payload.
  (4) **Derived metrics are computed in code** (`app/metrics/analysis.py`) and
  Claude only interprets them; a metric the session can't support is
  `not_applicable` with a reason, never a misleading zero. Do not move this
  arithmetic into the prompt.
  (5) **The cache never goes stale.** An analysis is keyed on `activity_id` and
  the activity never changes, so unlike `_plan_is_current` there is no freshness
  check — `?force=true` is the only invalidation.
  (6) `activity_id` becomes a cache filename, so the route constrains it with a
  `^[A-Za-z0-9_-]+$` pattern; that is the path-traversal guard, not a later
  sanitize step.
  (7) **No narrative (2026-11-07, the athlete's call)**: the "Coach analysis"
  headline/paragraph and "What the numbers say" (metric tiles with a note each)
  are gone from the prompt, the JSON template, the modal and the Ask coach
  attachment. What is left: execution vs plan, what went well, what to improve,
  flags, current form. Older cached analyses still hold the dropped keys; the
  modal ignores them. (The sync summary's `headline` is a different field.)
  `form_context` is now a list of points drawn as bullets; a cached one that
  is still a paragraph is split into its sentences (`formPoints`).
- **Athlete age is derived, not stored (2026-08-28, PRD §7.2)**: the profile
  stores the athlete's **`birthday`**; `age` is computed from it on every read
  (`age_on` in `app/config.py`, mirrored for the UI hint by `ageFromBirthday` in
  `frontend/src/format.ts`) and is read-only end to end — Settings edits a date
  input, `AthleteProfile.age` is a Pydantic `computed_field` so an `age` in a PUT
  body is ignored, and `save_athlete_profile` strips it so it can never land back
  in the YAML. A stored age is wrong from the athlete's next birthday until they
  happen to notice. **Profiles written before this keep working with no
  migration script**: `get_athlete_profile` reads a legacy bare `age:` and infers
  Jan 1 of the implied birth year — exact for the rest of that calendar year —
  until the athlete saves a real birthday. Claude gets the derived `age` and not
  the birthday (`_profile_for_claude` in `app/ai/prompt_builder.py`); the
  InfluxDB `athlete_profile` mirror takes numeric fields only, so it stores the
  derived age and skips the birthday on its own. That mirror is written **only
  by `PUT /athlete`, and only when a numeric value changed** (2026-10-20) --
  never on sync, which used to add an identical point every run. So the
  measurement is a history of threshold changes; nothing in the app reads it
  (every reader uses `get_athlete_profile()`, i.e. the YAML). The sync-pipeline
  test fakes deliberately have no `write_athlete_profile`, so a sync that calls
  it again fails them.
- **Activity class (2026-08-31, PRD §7.2)**: the profile carries
  **`activity_class`**, the athlete's habitual activity level on the standard
  0-10 scale (0 inactive, 10 competitive), edited as a slider in Settings ->
  Athlete profile and defaulting to **4** (`DEFAULT_ACTIVITY_CLASS` in
  `app/config.py`). No migration is needed: `athlete.local.yaml` is rewritten
  whole and never merged with `athlete.yaml`, so an install saved before this
  has no key at all, and the default is applied in two places until the
  athlete saves — the Pydantic model (for `GET /athlete`) and
  `get_athlete_profile` itself (2026-09-12). The second one matters: the
  Claude payloads read the profile directly, not through the model, so without
  it they silently go without the value while the slider shows 4. Past that,
  everything downstream is key-agnostic — `write_athlete_profile` mirrors
  every numeric field, and `_profile_for_claude` excludes only the birthday,
  so the plan, analysis and coach-chat payloads all carry it with no
  per-field code. Frontend gotcha: `PROFILE_FIELDS` in
  `SettingsModal.tsx` drives both the draft and a validation loop that rejects
  `<= 0`, so the activity class is excluded from that union and parsed
  separately — 0 is a real answer here, not an empty field. The slider is the
  only `input type="range"` in the app; its blue-to-green track is painted
  inline from `C.form`/`C.fit` and only the thumb and track pseudo-elements
  live in `frontend/src/index.css` (`.activity-slider`), since the codebase has
  no CSS variables or utility classes.
- **Training Days grid (2026-09-04)**: `frontend/src/components/TrainingDays.tsx`
  renders the last 60 days as a GitHub-contribution graph — weekday rows,
  week columns starting on the day chosen in Settings -> Display ("Week starts
  on", `settings.weekStart`, passed in as a prop; Sunday or Monday), every row
  labelled (2026-10-02, the athlete's call; the handoff labelled only
  Mon/Wed/Fri), one cell per day coloured by discipline. Design handoff:
  `assets/260904 Triathlete dashboard design.zip`. Four decisions taken against
  it, all easy to re-break:
  (1) **The window ends today**, not on the Sunday before the current training
  week as the handoff specced — a session synced this morning has to appear. So
  the *last* column is the partial one; the first is partial too whenever the
  window start is not a Monday.
  (2) **Out-of-window cells are transparent, never rest days** — both the lead-in
  before the window start and the days after today. Painting them as rest days
  claims the athlete rested on days the panel simply does not cover. The rest
  fill (`C.restCell` `#262a30`) is deliberately lighter than `C.card`; an earlier
  `#1b1e23` made rest days vanish.
  (3) **Every cell is painted at full brightness, and there is no footer.** The
  handoff shaded each cell by the day's TSS (a floored linear ramp mixed toward
  the card background) under a "Less/More" legend row; the athlete dropped both
  on 2026-09-04. The grid says which disciplines were trained, not how hard —
  the day's load is in the tooltip. Do not reintroduce the shading without the
  legend that explains it.
  (4) The handoff's empty right-hand 2x2 totals panel was **dropped** on the
  athlete's call, so the grid spans the full card width; its cells are `flex: 1`
  and land at ~150px, which is where the handoff's fixed 147px (overflowing its
  own 896px body) evidently came from. No per-discipline totals are computed.
  (5) **A race day inside the window is framed, never repainted** (2026-09-20):
  the cell keeps its discipline colours and gains a `C.yellow` ring, a glow that
  bleeds into the 5px gap (`zIndex: 1`, so it lands on top of its neighbours)
  and a **static** glint laid over its colours — the same yellow the Predicted
  Week's race tile uses. The shine does not move: an animated sweep was built
  first and dropped on the athlete's call, since nothing else on the dashboard
  animates. Race days come from the **goals** (`goals` is now a
  prop), not the workouts: nothing on a synced activity says it was a race. A
  race with nothing logged keeps the rest fill and still gets the ring, since
  there is no discipline to colour it with.
  Days are bucketed by `groupWorkoutsByDate` — moved from `Sections.tsx` to
  `derive.ts` and exported for this panel, along with `localISO`, resolving the
  duplication `derive.ts` already flagged. It keys on the stored `date` field,
  so an evening workout lands on the athlete's calendar day, and it drops stale
  unsplit brick parents. A day with 3+ disciplines splits evenly N ways.
  **The dashboard's workout fetch is now 60 days** (`api.workouts(60)` in
  `useDashboardData.ts`) because this panel needs it; every other consumer
  windows its own range (7-day HR zones, 8-week charts, 28-day discipline cards),
  so the extra rows are inert there. Tooltips are native `title` attributes and a
  cell has no click handler.
- **Fatigue Zones and the load block (2026-10-19)**: the athlete asked for the
  fatigue graph the way intervals.icu draws it -- Form (TSB) over coloured zone
  bands -- at the same width as the Training Load (FFF) chart. That width is the
  left column of the load block (the right column is the fixed 760px KPI grid),
  so the new card sits **directly under the Training Load chart**, and
  everything else in the block was rearranged around it
  (`TrainingLoadAndKpis` in `frontend/src/components/Sections.tsx`). Things
  easy to re-break:
  (1) **Same plot geometry as the chart above.** Both are `LineChart` with the
  default 40px left / 14px right margins and the same 56 points, so each day
  lines up vertically. The Fatigue Zones card is 170px tall and passes
  `h={132}` (card less border, head and padding): the chart widens its viewBox
  to the box's aspect at a fixed `h`, so a short card with the default `h={200}`
  would draw its 9px text at two thirds size. `LoadForecastCard` does the same
  with `h={height - 38}`.
  (2) **The bands live in `frontend/src/formZones.ts`**, not in the chart, and
  follow **Joe Friel, "Managing Training Using TSB" (2015)** -- the athlete's
  source since 2026-10-19, with Science to Sport's "Monitoring Training Load"
  for the caveat that race-day TSB is individual (+10..+20 for some athletes,
  -5..+5 for others). Transitional above +25, Freshness +5 to +25, Grey Zone
  -10 to +5, Optimal Training -30 to -10, High Risk below -30; ticks sit on the
  boundaries. The first build used the intervals.icu split at +20; Friel puts
  it at +25. `LineChart` gained optional `bands` (with an optional `short`
  label, written inside the band when it is at least 11px tall -- Friel titles
  each zone on his chart), `ticks`, `domainPad`, `showLegend` and `padTop`, all
  defaulting to the old behaviour, so no other chart changed. The y range is
  fixed at -45..+35 (+35, not +30, so the Transitional band is tall enough to
  label) and only stretches if form ever leaves it (`formZoneDomain`). Open
  ends are `Infinity` and are clamped to the plot.
  (3) **Colour meaning.** Bands reuse the dashboard accents at 15% opacity:
  Freshness is `C.form` blue, Optimal Training is `C.fit` green, High Risk is red,
  Grey Zone is `C.muted`. Transitional is `C.yellow` (Friel's chart uses orange,
  which here means bike), which elsewhere means race day and planned TSS: it is
  the one deliberate overlap, kept because yellow is the only bright accent with
  no discipline meaning. The form line is one colour, `C.tsb`, as on the
  chart above (the intervals.icu line changes colour with the zone).
  **Load-series colours (2026-10-27, design handoff
  `assets/KpiGridVisual colors.tsx`)**: Fitness / Fatigue / Form have their own
  tokens, `C.ctl` teal, `C.atl` pink, `C.tsb` silver, everywhere they are drawn
  (KPI gauges, both load charts, the Load Forecast's projected CTL, the season
  plan's CTL line), so a load line never reads as swim / bike / run. `C.fit` /
  `C.fat` / `C.form` keep their names but now mean run / bike / swim, primary
  buttons and the zone bands.
  (4) **No per-zone summary card.** A "days in each zone" key under the KPI tiles
  was drawn first and dropped on the athlete's call. The card head shows the
  current zone, how many days in a row form has been in it
  (`formZoneStreak`), and Friel's one-line meaning ("now: High Risk, 4 days ·
  overreaching, ease off within a few days"). The streak, not a total, because
  Friel's High Risk limit is a few days at a time; past three days there the
  count turns red. The hover tooltip names the zone of the day under the cursor.
  (5) **The Load Forecast moved** from the load-focus row into the load block,
  under the KPI tiles, because it is the continuation of the Fitness line beside
  it. The KPI grid shrank from 246 to 216px to make room (tiles ~103px), the
  forecast went from 230 to 200px, and the load-focus row is two panels (Run
  Pace Trend, VO2max) at half width each. This reverses part of the 2026-08-26
  decision to keep the forecast next to the distributions.
  (6) Nothing backend: the Form series was already in `vm.loadChart.tsb`, and
  the zone of "now" comes from `vm.current.tsb`. The x labels are dates a week
  apart plus "now", where the Training Load chart above says "8 wks ago".
- **HRV and resting HR (2026-10-20, conscious divergence from PRD §13)**: the
  PRD puts HRV analysis out of scope; this is **display only**, two 28-day cards
  (`RecoveryTrends` in `Sections.tsx`, between `TrainingLoadAndKpis` and
  `TrainingDays` in `App.tsx`). Spec: `assets/HANDOFF-recovery-trends.md` and the
  design system's `RecoveryTrends` README; the colours are the `C.hrv` /
  `C.restingHr` aliases of purple and red (the athlete kept the system red).
  Things easy to re-break:
  (1) **Source**: two Garmin RANGE endpoints through `connectapi`, both confirmed
  live on 2026-10-20 -- `/userstats-service/wellness/daily/{displayName}` with
  `metricId=60` (resting HR, `allMetrics.metricsMap.WELLNESS_RESTING_HEART_RATE`)
  and `/hrv-service/hrv/daily/{start}/{end}` (`hrvSummaries[].lastNightAvg`, the
  overnight average -- not `weeklyAvg` or `lastNight5MinHigh`). The library
  wrappers fetch one day per call. Chunked at 28 days, parsed defensively
  (`parse_rhr` / `parse_hrv`), never raises, `pull_recovery` copies
  `pull_vo2max`'s token guard.
  (2) **Window** (`_recovery_start` in `app/sync_service.py`): with no
  `recovery` point stored, the pull reaches back to the first stored workout (or
  the earliest activity just pulled, on a fresh install) -- the athlete's "all of
  it, from the first saved activity". After that it is the incremental activity
  window (days since `last_sync` + `OVERLAP_DAYS`). So a full data reset
  backfills itself on the next sync, unlike VO2max. Same "garmin" phase, no new
  phase key. `scripts/backfill_recovery.py` re-runs the full pull on demand
  (`--since`, `--debug`, dry run unless `--apply`).
  (3) **Storage**: measurement `recovery`, fields `hrv` (ms) and `resting_hr`
  (bpm), midnight UTC like `vo2max`; each field is written only when present so
  a one-field re-pull never blanks the other. Served at `GET /api/metrics/recovery`.
  (4) **Gaps stay gaps** (`recoveryTrend` in `derive.ts`): unlike `vo2maxTrend`,
  nothing is forward-filled, since a reading belongs to one night. `LineChart`
  gained null values (the line breaks, no dot, the domain ignores them),
  `padL`/`padR`/`padB`, `refLine` (the dashed 28-day mean) and
  `tipLayout="single"`; the gridline and crosshair colours moved to
  `C.gridline` / `C.crosshair`, at the same values. The y ranges (HRV 40-80 by
  10, RHR 44-60 by 4) stretch in whole steps (`frontend/src/recovery.ts`).
  (5) **Not sent to Claude**: nothing in `prompt_builder.py` reads `recovery`,
  and `tests/test_recovery.py` pins that neither the plan payload nor the coach
  context mentions it. Adding it later is a deliberate decision, not a leak.
  `tests/conftest.py` patches `pull_recovery` for every test so no pipeline test
  makes a real Garmin call.
- **Season plan (2026-10-23, conscious divergence: the PRD has only the
  rolling 7-day plan)**: the macro plan is owned by code, not by Claude.
  Handoff: `assets/Season plan handoff.zip` (build spec, UI brief, samples),
  written against `bd1f95d` and reconciled with the code before building; the
  corrected samples are in `tests/fixtures/season/` and are the contract.
  Layout: `app/season/config.py` (every number), `models.py`, `store.py` (the
  one file, written atomically), `builder.py` (event plans, pure), `adapt.py`
  (sync adaptation, pure), `checkin.py` (fitness engine, pure), `summary.py`
  (statuses and the two lines, on read), `service.py` (the I/O edge), routes in
  `app/routers/season.py`. Things easy to re-break:
  (1) **Phases come from the prompt files**, not from a percent split (the
  athlete's call): `PHASE_WEEKS_OUT` copies each
  `prompts/<Distance>_Personalization.txt` phase line, and
  `tests/test_season_phase_consistency.py` parses the files and requires
  equality, so the season plan and rule 9 always name the same phase. A week's
  weeks-out is `(race_date - week_start).days // 7`, `_goal_block`'s floor. The
  taper length is the phase line too (70.3 tapers 4 weeks, 140.6 5, custom 4);
  `TAPER_FACTORS` size it off the highest loading week.
  (2) **Fatigue numbers are the Friel zones** of the Fatigue Zones chart
  (`frontend/src/formZones.ts`), not the handoff's: bands Fresh >= +5, OK
  -10..+5, Tired -30..-10, High < -30 (or < -20 at two check-ins in a row); the
  sync gate lowers the next loading week 20% below -30 and halves this week /
  makes next week recovery below -40; the mid-week question opens below -30.
  (3) **`reconcile()` runs only in the sync (inside the "metrics" phase, no new
  phase key, best effort) and before `POST /recommend/trigger`, never on a GET.**
  It is pure and idempotent: each rule fires once per trigger, recorded in the
  plan's `adapt_marks`, so a second sync with the same data adds nothing. Rules
  read the week just completed and act on THIS week (the handoff said "next
  week"): the first sync of a week is when the completed week is known. Pinned
  weeks and the taper are never touched; later weeks re-flow under the 10% cap
  and the 3 + 1 cadence. When the peak drops (a lower hours cap, an
  under-delivery re-anchor) the taper is re-sized so it never sits above the
  build. With no plan, `on_sync` returns before any read.
  (4) **Staleness is `payload_changed_at`, not the file's mtime**: the file is
  rewritten every sync (actuals). The stamp moves only when this or next
  week's phase/kind/target, the hours cap, the focus or the status changes;
  `_plan_is_current` compares it with the cached plan's `generated_at`.
  Cancelling marks the plan `cancelled` (the file stays) for the same reason.
  (5) **The 7-day window straddles two season weeks** (it starts today), so the
  `season_plan` payload block carries every season week it touches with its
  `plan_dates`: the current week's `budget_tss` is exactly what is left of its
  target; a later week's is a pro-rata guide. `_check_season_target` flags (a
  `warning_flags` entry, never a rewrite) a current week outside 10% of its
  budget, skips the under-budget flag after a race or below TSB -30, and stamps
  `weekly_tss_target` / `periodization_phase` from code. Rules 25-28 are
  appended after rule 24, nothing renumbered.
  (6) **Weekly actuals are calendar weeks** keyed on `_workout_date` and the
  plan's own `week_start_day` (copied from Settings at creation, never changed
  by a later Settings edit). They match the TSS-by-week chart only for a Monday
  plan: that chart's `weekBuckets` is Monday-fixed.
  (7) **Pins are the athlete's**: a week set by hand (week panel or the coach's
  `adjust_season_plan` tool, which is the same service call and is absent with
  Include context off) is pinned, and a broken 10% cap or 3 + 1 rhythm is
  allowed with a `warnings` entry -- structure in code, coaching soft. Resulting
  targets are stored, never edit operations (the `plan_requests` lesson).
  (8) **The plan follows its event**: `on_event_changed` from the events router
  after an update or delete, and again in every sync as a backstop. Deleted or
  not participating marks it `event_missing` (kept, with a banner offering a
  conversion to a fitness plan); a new date or distance rebuilds it from its
  own first week keeping past weeks and pins; a race moved into the past
  completes it.
  (9) **Fitness check-ins are worked out on read and stored only when
  answered.** Options build on what the athlete did (P = actual so far plus
  what the cached 7-day plan still has; the final actual once closed), never
  on the target -- no catch-up. An answer on the last day is resolved again
  from the final total when the week starts, then frozen; an unanswered one is
  applied by the first sync of the new week (`checkin_auto`, with a notice),
  one week at a time if several were missed. A week that started before any
  sync can still be chosen. A due recovery week can be put off once on fresh
  or OK legs. `tests/fixtures/season/checkin.pending.json` is the handoff's
  sample reproduced exactly (one hours slip fixed).
  (10) **No Claude call** for the two summary lines: templates, under 160
  characters, computed on read. `tests/conftest.py` points `SEASON_PLAN_FILE`
  at a temp file for every test, and `DELETE /api/data` deletes the plan.
  (11) **Triathlon weeks carry a swim / bike / run split (2026-10-25, the
  athlete's ask)** -- `app/season/split.py`. Integer percents of the week's
  **TSS** (not time: a run earns more TSS per hour than a ride) from
  `config.DISCIPLINE_SPLIT` per distance and phase (race week = taper row),
  over which the plan's own `settings.split_by_phase` (create modal, Settings
  row) wins; fitness plans with the triathlon focus use `FITNESS_TRI_SPLIT`.
  Run, custom and non-triathlon fitness plans have no split. `apply_splits`
  runs in `annotate()` and `_stored()`, so no builder, adaptation rule or
  rebuild carries split code: a week follows its phase's row until the
  athlete (week panel) or the coach (`adjust_season_plan` `set_split`) sets
  it by hand -- `split_source: "user"`, which is **not** the TSS pin (the
  target still adapts) and survives rebuilds and table changes. Strength
  gets no share: it counts toward the week total only. Actuals per
  discipline (`actual_by_discipline`) come from the split brick legs;
  `discipline_tss` / `discipline_hours` are worked out on read (hours at each
  discipline's own TSS/hour from the baseline) and stripped before a save.
  The payload adds `split_pct` and `discipline_budget_tss` per week (current
  week: what is left in each sport, never more together than `budget_tss`),
  prompt **rule 29** asks for each within 15%, and `_check_season_target`
  flags a sport off by more than 15% (never under 10 TSS), never rewriting.
  On the bars a triathlon plan is stacked in `DISCIPLINE_COLOR` (swim at the
  bottom). The phase brackets under every plan's bars use their own bright
  accents (2026-10-26: sky base -- teal until 2026-10-27, now `C.ctl` --,
  indigo build, pink peak, lime taper), chosen
  in the hue gaps between the colours that already mean something (swim,
  bike, run, strength/HRV, HR, race-day yellow); the slate ramp stays only as
  the bar fill of a non-triathlon plan.
  (12) **The plan reads the coach instructions (2026-10-26, the athlete's
  ask)** -- `app/season/instructions.py`, so a run plan carries the weekly
  ride the instructions ask for instead of fighting it. One small Claude call
  (`generate_season_instructions`, prompts `season_instructions_system.txt` /
  `_format.json`) turns the free text into **sports** (add / more / less /
  drop a sport with its share of each week's TSS, sized from the text) and
  **load** (hours cap, ramp, recovery spacing 2-4, peak CTL), and lists the
  rest (days, session content) as `left_to_weekly_plan` -- the 7-day plan
  still gets the full text and owns those. `clean_read` keeps known keys and
  clamps every number (one sport at most 40%, all of them together 40%); the
  read is cached in-process per text + plan context, so the create modal's
  live preview pays once; a failed call builds the plan without it, never
  blocks. Applying it: the split now exists beyond triathlon (`has_split`):
  a one-sport event or focus starts from a one-sport row and the read's
  sports layer over it (code row, then instructions, then the athlete's
  `split_by_phase`); **the race week never takes the instructions** (the
  race row). Load fills only what the create form left blank (the form
  wins); `settings_source` records "user" / "instructions" / "default" per
  setting, and recovery spacing is now a plan setting
  (`builder.cadence(plan)`, read by builder, adapt and checkin). The plan
  stores `instructions` = {text_sha, read_at, applied, opted_out, read};
  "Use my instructions" off in the modal opts the plan out for good.
  **Saving new instructions** (PUT /coach/instructions) calls
  `on_instructions_changed`: nothing when there is no active plan, the text
  is unchanged or the plan opted out; otherwise one re-read, applied from
  NEXT week -- weeks up to this one keep their split (`split_source:
  "kept"`), hand splits stay, and only settings the instructions set (or
  still at their default) change, through `update_settings(trigger=
  "instructions")`. An `INSTRUCTIONS` revision, the payload stamp, and
  `season_plan_note` in the response (the dashboard's green strip). Never
  fails the save. `tests/conftest.py` points the instructions file at a temp
  one and stubs the call for every test -- the main checkout's data/ holds
  the athlete's real text. Rule 29 now names any plan with
  `discipline_budget_tss`, and says a sport there is one the athlete asked
  for (rule 4 already lets the instructions bring it in).
  `tests/test_season_*.py` pin all of the above.
- **Activity class in use (2026-11-15, the athlete's ask)**: until this the
  class only rode in `athlete_profile`; nothing acted on it. Two uses.
  Things easy to re-break:
  (1) **Season plan defaults, in code.** Precedence: create form > coach
  instructions > activity class > the constants. `ACTIVITY_CLASS_BANDS` in
  `app/season/config.py`: 0-3 gentle / recovery after 2 loading weeks / peak
  CTL x0.85; 4-6 standard / 3 / x1.00; 7-10 ambitious / 3 / x1.10. The
  new-athlete start floor is `90 + 15 x class` (`start_floor`). **Class 4, the
  profile default, is exactly the constants' plan** (floor 150 =
  `MIN_START_TSS`); a test pins it.
  (2) `_with_profile` in `app/season/service.py` runs after
  `_with_instructions` in `preview()` (so in `create()` too) and marks what it
  fills `settings_source: "profile"`. A fitness plan takes only the spacing and
  the floor. The create form has no spacing field, so on a new plan the spacing
  comes from the instructions or the class.
  (3) **A saved plan is never changed by a later slider move**: the settings
  are stored in the plan. The one exception is `update_settings`: a load
  setting cleared there (the instructions stopped setting it) goes back to the
  class's value, not the constant.
  (4) `tests/conftest.py` pins `service._activity_class` at 4 for every test:
  `config/athlete.local.yaml` holds the athlete's real class.
  (5) **Rule 34, prompt only**: quality (Z3-Z5) sessions in the 7 plan days --
  0-3 one, no two-a-days outside a brick day, a long session at most 10 min
  over last week's; 4-6 two, never consecutive; 7-10 three. Ceilings, not
  targets; it never changes the TSS (rules 26 and 32 own it); rules 1-6, 17-20,
  24, 31 and 32 outrank it and `athlete_custom_instructions` may override it.
  Not enforced in code, like rules 22 and 29. The coach context's profile line
  names the same bands.
  `tests/test_season_activity_class.py` and
  `tests/test_activity_class_prompt.py` pin it.
- **Sub events (2026-11-14, the athlete's ask)**: an event plan stores
  `sub_events` ({event_id, name, type, race_date}, the key absent when there
  are none so the contract sample still matches) -- participating events dated
  from today to before the main race. `tests/test_season_sub_events.py` pins it.
  Things easy to re-break:
  (1) **The mini-taper is code, by distance** (`config.SUB_EVENT_TAPER`): a
  share of the last loading week for the sub event's own week (`sub_race`), and
  for marathon / 70.3 / 140.6 also the week before (`sub_taper`) and a recovery
  week after (kind `recovery`, role `post`). Each such week carries
  `sub_event` (with its role) and `sub_factor`. The new kinds stay out of
  `_taper_start`, which keys on `taper` / `race` only, and the phase stays the
  main event's. A sub event inside the main taper, or before the plan's first
  week, marks no week but is still listed and still reaches Claude.
  (2) **Growth resumes from the last loading week**, as after a recovery week; a
  `sub_race` or `post` week restarts the 3 + 1 count, and no recovery week is
  placed right before a mini-taper (`builder.sub_week_map`, `resets_recovery`,
  `eases_into_sub`, mirrored in `adapt.reflow`). The sync rules never change a
  sub week (`_editable`), but reflow re-sizes it as last loading x
  `sub_factor`, so an under-delivery re-anchor carries it down. A pin wins.
  (3) **The plan follows its sub events** like its main event:
  `on_event_changed` and the sync backstop drop a deleted, not-participating or
  no-longer-earlier one and rebuild on a new date or distance
  (`_checked_sub_events` / `_follow_sub_events`, revisions `SUB_EVENT`); a new
  main race date is checked against them before its one rebuild.
  `POST /season-plan/sub-events` and `DELETE .../{event_id}` add and remove,
  through `_rebuild`, so past weeks and pins stay.
  (4) **`primary_goal` is the plan's main event** while an active event plan
  exists (`season_service.goal_event()`, read in the plan, analysis and so the
  coach payloads), not `active_event()`'s nearest race -- otherwise rule 9
  planned 10K weeks before a 10K sub event of a 70.3 season. With no plan it is
  exactly as before. `sub_events` in the payload (absent when empty) and
  **rule 33**: the race is that day's session (its disciplines may enter outside
  `race_disciplines`), the day before is easy with openers, **not empty** -- rule
  31 and `_clear_pre_race_day` are the main race's only. The `_payload_view`
  includes the sub events, so a change makes the cached 7-day plan stale.
  (5) **Dashboard**: the create modal's steps are named (`StepName`), the "Sub
  events" step only when the chosen event has earlier racing events, all ticked
  by default. `EventModal` asks "Add as a sub event?" after a save that is new
  or moved to a new date, not on "Save and plan". `PlanBars` labels the race
  week "SUB" and hatches the mini-taper.
- **Sync summary modal (2026-10-30)**: `PlanSummaryModal` replaces its chips and
  prose with two rows, the last 7 days (today and the 6 before) over the next 7
  (the 7 after today). Handoff: `assets/Sync summary modal handoff.zip`. It opens
  as before (`coachSummary`, after a plan-producing sync or Re-sync plan). All the
  arithmetic is `syncSummary` in `frontend/src/derive.ts`. Things easy to re-break:
  (1) **The numbers never come from Claude's prose**: `plan.summary` is no longer
  shown here (on 2026-10-30 it disagreed with the sessions about the sport split).
  `/summary` in the chat still renders it.
  (2) **`window_budget_tss` is stamped before the current-week check** in
  `_check_season_target`: a season plan that starts later in the window has no
  current week, and the early return would lose its budget. It is the budget of
  the plan's own window, which starts today, while the row starts tomorrow; on a
  day with a session still planned today the two differ by that day's share.
  `weekly_tss_target` is a different number (the whole season week).
  (3) **`headline`** (rule 30, appended; after `coach_notes` in the format, so the
  model writes it after the sessions) is at most two sentences. A plan cached
  before it falls back to the first two sentences of `coach_notes`.
  (4) **One y scale across all 14 days** (`BAR_MAX_PX` for the largest), so a
  column compares straight down. Bars stack in `ORDER`, swim at the bottom.
  (5) **Only swim/bike/run/strength count** on the logged row: Garmin also stores
  `other` (a 3-minute "Relax and Focus"), which is neither a session nor a colour.
  A day with sessions but 0 TSS shows 0, not Rest.
  (6) **The plan stops one day short of the next row, on purpose**: it starts
  today, so the last column is **Open**. Do not lengthen the plan.
  (7) Planned zones count a range as its higher zone (`highestZone`, "Z1-Z2" is
  Z2), unlike `zoneColor`, which takes the first; segment minutes can differ from
  `duration_min`, so Time and the donut use different sums. "Easy" comes from the
  raw shares, not the rounded ones.
  (8) The status line reads the event's **name** ("15 days to the Eyal RUN"), where
  the handoff's render shows "the 15K".
- **Waiting screen (2026-10-31)**: `start.cmd` opens the browser once Vite answers,
  before uvicorn does, so the first load failed with the proxy's HTTP 500 and
  nothing retried it. `BackendGate` (`frontend/src/components/BackendGate.tsx`,
  wrapped around `App` in `main.tsx`) polls `GET /api/health` every second and
  mounts `App` only once `influxdb_connected` is true. Things easy to re-break:
  (1) **One screen across the data load**: it stays on top, at the same tree
  position so the animation does not restart, until `App` calls `onLoaded` (its
  first load finished or failed), and `App` renders nothing before that. The
  old "Loading dashboard..." placeholder is gone.
  (2) **The red banner is still the failure path**, for a first load that fails
  after health passed and for a backend lost mid-session. The gate runs once per
  page load, so a later `uvicorn --reload` never brings the screen back.
  (3) **It never gives up**: past 60 s it adds a hint and keeps polling. It shows
  only after 800 ms, since a reload with the backend up has its data in about
  500 ms and would otherwise flash it.
  (4) **The favicon, not the `TopBar` logo**: the Garmin image is served by the
  backend. The pulse keeps the dashboard's red on request (design B of the
  three shown), and `prefers-reduced-motion` stops it.
- **Trend charts span 8 weeks (2026-11-08, the athlete's call; they were 4)**:
  Training Load, Fatigue Zones, Weekly Volume by Discipline, TSS by Week, Run
  Pace Trend and VO2max all read `TREND_WEEKS` / `TREND_DAYS` (8 / 56) in
  `frontend/src/derive.ts`; `useDashboardData.ts` asks `/metrics/load` and
  `/metrics/vo2max` for 56 days, and the 60-day workouts fetch already covers 8
  calendar weeks (55 days at most). Unchanged on purpose: the HRV / resting HR
  cards (28 days), the discipline cards' Window dropdown, the KPI tiles' "vs
  4-wk avg" and the 7-day HR zones. To fill them from the first sync, a sync
  into an empty store (new install, or after `DELETE /api/data` -- detected by
  `_history_blank` in `app/sync_service.py`, since a reset keeps `last_sync`)
  pulls `FIRST_SYNC_DAYS` (60) of workouts, and `_vo2max_days` widens VO2max to
  the larger of that window and 28 days.
- **Weekly sleep (2026-11-13, the athlete's ask)**: the Distributions row's
  third card (`Sections.tsx`), bars in `C.sleep` (`#A855F7`, bright purple since 2026-11-16), one per
  Monday-start week (`weeklySleep` in `derive.ts`, same `W##` weeks as the other
  weekly charts). Things easy to re-break:
  (1) **Source**: `/wellness-service/wellness/dailySleep?date=` on the Personal
  API, **one request per night** -- the `/sleep-service/stats/sleep/daily`
  range endpoint answered `null` when probed live. `sleepTimeSeconds` (naps are
  `napTimeSeconds`, left out); Garmin dates a night by the morning after.
  `pull_sleep` runs `SLEEP_WORKERS` (4) threads, fetches the token once before
  them, never raises, skips a night outside 1-16 h.
  (2) **Storage**: field `sleep_sec` on the `recovery` points (midnight UTC),
  written only when present, so it merges with that day's HRV and resting HR;
  served by `GET /api/metrics/recovery`, which the dashboard now asks for 56
  days (the HRV / resting HR cards still window 28 themselves).
  (3) **Its own window** (`_sleep_start`): with no `sleep_sec` stored it pulls
  `SLEEP_BACKFILL_DAYS` (56), else the incremental activity window.
  `latest_recovery_point` ignores `sleep_sec`, so stored sleep never stops the
  HRV backfill. Same "garmin" phase, no new key. `scripts/backfill_recovery.py`
  pulls sleep too.
  (4) **The average skips nights with no reading** (a night the watch was off
  is not zero sleep), the current week averages its nights so far, and the
  tooltip names the week, the average in h/m and the night count.
  (5) **Not sent to Claude**: `tests/test_sleep.py` pins it, like recovery.
- **Swim TSS (conscious PRD divergence)**: sTSS follows
  `assets/tss_rtss_calculation_spec.md` §4, the TrainingPeaks definition — the
  intensity factor is **cubed**, not squared as PRD §6 says (power against
  water drag scales with velocity cubed), and both the pace and the hours
  charged are over **moving time**, rest between sets excluded
  (`sTSS = IF³ × moving_hours × 100`). The spec states it in m/min
  (`NSS / swim_FTP`); the code keeps the athlete profile's `css_sec_100m` and
  computes the same ratio as `css / moving_pace`. Moving time comes from
  Garmin's `movingDuration` or `distance/averageSpeed` (pool `averageSpeed` is
  measured over active lengths only), whichever is smaller and never above the
  timer duration — `_moving_seconds` in the parser, stored as
  `moving_duration_sec` on swims only. Swims stored before 2026-07-27 have no
  moving time and fall back to the timer (the one-off backfill that fetched it
  was applied and removed on 2026-10-26). Bike/run keep the
  PRD IF² form over full duration, as does the HR fallback — including for a
  swim with no pace, since hrTSS counts time at a heart rate, rest included
  (all in `app/metrics/calculator.py`).
- **Rest-day decay (conscious PRD divergence, fixed 2026-08-10)**: the daily
  CTL/ATL/TSB series runs **through today**, not through the last day that has
  a workout. `assets/Jymalaya_AI-rest_day_TSS.md` is the authority here. Two
  parts, both in `app/metrics/calculator.py`:
  (1) the recurrence is the **linear** EWMA `L_today = L_yest + (TSS - L_yest)/τ`
  (`_step`), not PRD §6.2's exponential form — τ unchanged at 42/7, and the two
  agree to ~0.03%/day. TSB stays **same-day** (`ctl_today - atl_today`), not the
  doc's yesterday-based variant.
  (2) `compute_load(..., through=date.today())` walks past the last workout so
  trailing rest days decay, and `pad_load_to` extends a stored series forward at
  read time (`GET /metrics/load`, `_latest_load` in `app/ai/prompt_builder.py`)
  so it still ends today between syncs. Never pad past today — daily_load reads
  are `range(start: -Nd)` with an implicit `stop: now()`, so a future row is
  invisible. Before this, the newest stored row was the athlete's last training
  day, i.e. **peak ATL**: fatigue only ever rose, form never recovered, the
  chart labelled that stale day "now", and Claude got the same frozen
  `current_load` in the plan payload and coach chat. Nothing needs a migration
  script — `recompute_stored_metrics` rebuilds from the full history and Influx
  overwrites each day in place. `frontend/src/forecast.ts` mirrors `_step` and
  must be kept in step with it.
- **AI layer**: Claude API. Two uses — (1) auto-generate the 7-day plan after every
  successful sync — skipped when no new activities came in and the cached plan is
  current (`_plan_is_current` in `app/sync_service.py`: generated today AND after
  the last edit to the custom instructions or schedule overrides; the dashboard
  shows a "plan unchanged" notice) — constrained by the 80/20 guardrails in the system prompt
  (`prompts/recommendation_system.txt`, see PRD §7.7); (2) the interactive Coach chat.
  The athlete's custom coach instructions (`app/instructions.py`, edited in the
  dashboard Settings) ride along as `athlete_custom_instructions` in the plan
  payload and as a line in the coach-chat context — only when non-empty; hard
  schedule constraints win over them (rule 4 in the system prompt). The
  training schedule (`app/schedule_store.py`) goes into the same payload as
  `training_schedule` + `schedule_constraints` exactly as the athlete set it in
  Settings, and the system prompt's rule 3 reads the week limits from
  `schedule_constraints` rather than hardcoding them.
  **Rule 9 (periodization) is per race distance, not shared**: the system prompt
  holds a `{{RACE_PERSONALIZATION}}` placeholder that `_build_plan_system` in
  `app/ai/claude.py` substitutes with `prompts/<Distance>_Personalization.txt`,
  chosen from the next goal's `type` via `PERSONALIZATION_FILES` (the ten
  `EventType` values = the dashboard's Distance dropdown). Unknown types, and no
  upcoming goal at all, fall back to `Custom_Personalization.txt`. Each file
  carries the phase table, long-session ceilings, key sessions and taper length
  for that distance — a 5K and a 140.6 at ten weeks out are not the same phase.
  Edit those files to change distance-specific coaching; the shared rules stay
  in `recommendation_system.txt`.
  The design prototype calls a `window.claude.complete` shim — in production this goes
  through a backend endpoint (`POST /coach`), never directly from the browser.
  The coach's system context opens with a **date anchor** (`Today is <weekday>,
  <ISO date>`, from `today_date`) — everything else in it is dated ISO strings
  (plan days, rest days), and without the anchor the model guessed which one was
  today and answered a Wednesday question as if it were Friday, the long-run day
  (fixed 2026-08-12 in `build_coach_context`). The rest-day list carries weekday
  names for the same reason. The coach additionally gets the athlete profile
  (2026-09-12: the same `_profile_for_claude` dict the plan and analysis
  payloads carry, as one compact JSON line, so every Claude call sees FTP,
  thresholds, weight, age and activity class; "Include context" off still sends
  none of it) with the shared training zones right under it (see "Training
  zones" below), the full plan-days JSON in its system context, and four
  plan-editing tools (update/move/remove/add session) that mutate
  `data/latest_recommendation.json` via `app/plan_editor.py`. Split of enforcement:
  structural validity (disciplines, positive durations) is Pydantic in code;
  coaching guardrails (80/20, the consecutive-days limit, rest-day
  confirmation) stay in the prompt so the athlete can consciously override them.
  **"Include context" (2026-08-31)**: the chat composer's pill switch (default on,
  sticky in `tricoach.coachIncludeContext`) rides along as `include_context` on
  `POST /coach`. Off routes the turn through `build_general_coach_context()` —
  which reads no Influx/profile/schedule/plan source at all, only the date
  anchor — **and drops the tools list** (`coach_reply(..., tools=False)`): a
  coach that cannot see the plan must not be able to rewrite it, and the prompt
  alone is not the place to enforce that. Off is honest rather than partial, so
  the frontend refuses `/last`, detaches an attached workout when the switch
  goes off, and switches it back on when a workout is attached (attaching is an explicit
  request to discuss that session). `/summary` still works — it renders the
  cached plan locally and makes no Claude call.
  Thinking is explicitly disabled on every call — on Sonnet 5, omitting the
  `thinking` parameter silently enables adaptive thinking and can exhaust
  max_tokens before any JSON is emitted. **The coach chat runs on Sonnet 5.5
  (2026-09-30, `ANTHROPIC_COACH_MODEL`, default `claude-sonnet-5-5`); plan and
  analysis stay on `ANTHROPIC_MODEL`.** Sonnet 5.5 rejects
  `thinking: {"type": "disabled"}` with a 400, so `_thinking_off(model)` sends
  `{"type": "between_tools"}` for it (and `disabled` for every other model) on
  all three calls; between_tools also 400s at `xhigh`/`max`, so
  `_effort_kwargs` sends those as `high`. Tunables in `.env` (see `.env.example`):
  `ANTHROPIC_MODEL`, `ANTHROPIC_COACH_MODEL`, `ANTHROPIC_EFFORT`
  (output_config.effort; unset = API default "high"), `ANTHROPIC_PLAN_MAX_TOKENS`,
  `ANTHROPIC_COACH_MAX_TOKENS`.
  Full request/response debug logging runs under the `jymalaya.*` logger
  namespace (handler + level in `main.py`, from `LOG_LEVEL`, default `DEBUG`;
  the container sets `INFO` — the dumps carry the whole plan payload. Never
  logs credentials).
- **Training zones (2026-09-13, conscious divergence from PRD §6.3)**: every
  Claude call gets the same zone bands and strict pace rules from
  `prompts/training_zones.txt` — substituted for `{{TRAINING_ZONES}}` in the plan
  and analysis prompts (`_build_plan_system` / `_build_analysis_system` in
  `app/ai/claude.py`) and inserted under the profile line by
  `build_coach_context`. The "Include context" off coach gets only a one-line
  pace-direction rule, since the file names profile keys. Before this no prompt
  defined any zone but bike power; the coach improvised, took a percent OF the
  threshold pace (321 s x 0.81 = 4:20/km) and quoted Z2 faster than a 5:21/km
  threshold — then inverted again in its correction. Things easy to re-break:
  (1) **Pace bands are multiplication factors on threshold seconds, never
  percents of a pace.** Every Z1-Z3 factor is above 1.00, so following the
  formula cannot put an easy zone ahead of threshold;
  `tests/test_training_zones_prompt.py` pins it.
  (2) Band sources: HR is the athlete's own Garmin zones, %LTHR 65/80/89/96/100
  (`assets/HRZone.pptx`); run pace is Friel's threshold-pace zones folded into
  five; bike is the %FTP bands that used to sit in plan rule 15, kept equal to
  `POWER_ZONE_BANDS` in `app/metrics/analysis.py` by a test; swim is the bike
  boundaries through the cube law sTSS already uses (speed = power^(1/3)),
  rounded to 1.21/1.10/1.03/0.98. Zone names follow the per-distance files (Z3 =
  tempo, the gray zone), not Garmin's "Aerobic", which invites gray-zone work.
  Plan rule 6 is "~80% in Z1-Z2, ~20% in Z3-Z5" (the old "Zones X-5" named a
  zone no band set has) and rule 12's long rides are Z1-Z2, since Z1 is now
  recovery. PRD §6.3's six-zone table is superseded: it contradicts the prompts,
  the Garmin zones and itself ("Z3 threshold" above FTP).
  (3) The arithmetic is still Claude's — the athlete chose strict prompt rules
  over a code-computed table. **Units rule P8 (athlete's call)**: nothing the
  athlete reads carries a pace in seconds — run pace is min:sec per km, swim
  min:sec per 100 m, bike speed km/h with intensity in watts — and that includes
  any working shown ("5:21/km x 1.14-1.29 = 6:06-6:54/km"). Plan rule 14 still
  makes a set-distance segment (race leg, 1 km fast finish) write its steps in
  the notes, in min:sec. Measured trade-off: with thinking off, the written
  seconds were the model's only scratchpad. After P8, a replay of the original
  chat got 5 of 7 coach replies free of seconds, but one miscomputed Z2 as
  5:22-6:08/km; plan notes stayed right. If either keeps recurring, the fix is
  the code-computed table.
  (4) **`recommendation_plan_format.json` lists a segment's `notes` before its
  `duration_min`, and a session's `structure` before its totals.** The model
  fills the template top to bottom; in the old order it committed to a typical
  race time first (a 1500 m swim at a 2:40 CSS set to 25 min) and then wrote a
  calculation that contradicted it. Rule 14 also keeps race legs as structure
  segments of the one race-day session, so a race never breaks `max_sessions`.
- **Race specificity + post-race recovery (2026-09-20)**: two defects in the
  generated plan, both fixed in the prompts rather than in code.
  (1) **The goal picks the disciplines; the schedule picks only the days.** A
  marathon plan kept scheduling swims because the run personalization files said
  "unless the schedule explicitly asks" and `DEFAULT_SCHEDULE` literally asks
  (monday `["swim","run"]`, friday `["swim","bike"]`). `RACE_DISCIPLINES` in
  `app/models.py` (next to `EventType`, with a `race_disciplines()` reader) now
  rides in `primary_goal.race_disciplines`, and rewritten **rule 4** makes it
  authoritative: `preferred_disciplines` says WHEN the athlete trains and how
  many sessions fit, never WHICH sport. `strength` is deliberately in no entry —
  it is support work, and rule 4 keeps it on any day that names it, for a
  triathlete too. `custom`/no goal maps to `None` = no restriction, so the
  schedule governs exactly as before. `athlete_custom_instructions` is the only
  override, which is **why this is not code-enforced**: the opt-in is free text,
  so any `_enforce_rest_days`-style pass would false-positive precisely when the
  athlete asked for the cross-training. The five run files carry the per-distance
  run-day counts (5K/10K 3-4, 15K 3-5, half-marathon/marathon 4-5) and say
  outright that spare available days get no session —
  `max_training_days_per_week` is a ceiling, not a target. No frontend work: an
  empty non-rest day already renders as "Rest" (`Sections.tsx`). Rule 13 gained a
  clause because a one-discipline race has no brick to build.
  (2) **A finished race buys a recovery window.** `active_event()` only ever
  returned races with `race_date >= today`, so the day after a race the goal
  vanished and the plan fell into `Custom_Personalization.txt` or straight into
  the next race's build phase; TSB, the only recovery signal, is a lagging EWMA
  that one race day barely moves. `events_service.last_race(within_days=)` is the
  mirror of `active_event`, and **parses the date instead of comparing strings** —
  `_row_to_event` defaults a missing `race_date` to `""`, and `"" < today` is True,
  so a plain comparison hands back a date-less goal as the last race
  (`active_event`'s `>=` excludes `""` by the same accident, in its favour). The
  payload gains **`recent_race`**, omitted entirely when there is none (the
  `athlete_custom_instructions` precedent), carrying `days_since_race` and
  `measured` — the race day's real load, aggregated across the brick-split legs
  with a **duration-weighted** IF so a 6-min transition jog does not average
  equally with a 5-hour ride. `measured` is `null` (not absent) when nothing was
  logged, because rule 20 tests it by name. `RECENT_RACE_DAYS = 21` **must stay
  under `PLAN_HISTORY_DAYS = 28`**: the race-day activity is looked up in that
  same already-fetched list, so a longer window would silently report every older
  race as "nothing logged". A race dated **today** is deliberately not a recent
  race — it is still `active_event` and rule 5 owns race day; rule 17's first
  sub-bullet covers the plan regenerated on race evening, which would otherwise
  taper for a race already run.
  Both live in new **rules 17-20** under their own heading after rule 16, with a
  cross-reference from rule 8. They were appended rather than inserted because
  `tests/test_personalization_prompt.py` pins the literal rule-9 line and this
  file references rules 3, 4, 6, 9, 12, 13, 14 and 15 by number — stale numbers
  in the agent-guidance file cost more than tidier ordering buys.
  `tests/test_race_disciplines.py` and `tests/test_post_race_recovery.py` pin all
  of it; the first is what stops the "unless the schedule explicitly asks"
  loophole being reintroduced.
  **Three things live generation caught, all worth keeping.** (a) Rule 4's
  inherited "deviate only if TSB or recovery requires it" was a second way back
  in: two days after a race the model read an easy spin as recovery and put a
  bike in a 10K week. Recovery may change which race discipline a day gets and
  how hard, never reach outside `race_disciplines` — rule 17 says the same from
  the other side, because an empty day is the better recovery anyway. (b) The
  per-distance run-day count has to be restated in **rule 4**, not left to the
  rule 9 block alone: with it only in the personalization file the model read
  `max_training_days_per_week` as the operative cap and planned 5 runs for a 10K,
  and when the cross-training route was closed it filled the spare day with an
  extra run instead — the same mistake wearing a different shirt. (c) The
  recovery table's duration column is **calibrated against the TSS column** at
  roughly 70 TSS per racing hour, so a normally paced race lands on the same row
  either way; the first draft's boundaries put a 2h39 olympic triathlon in the
  70.3 row, and "take the longer recovery" then made that the answer. Verified
  end to end against the athlete's live data (10K goal five weeks out, olympic
  triathlon two days back): run-only week, four run days, the spare day emitted
  empty with `is_rest_day` still false, the brick day collapsed to a single run,
  and the peak phase deferred to day 8.
- **VO2max trend in the plan payload (2026-09-20)**: the VO2max pipeline was
  dashboard-only — `grep -i vo2 prompts/` returned nothing but the Z5 zone label
  — so the plan could not react to a fading aerobic ceiling. `build_plan_payload`
  now carries **`vo2max_trend`**: per discipline, four weekly averages oldest
  first (null for a week Garmin had no estimate for), the newest reading, the
  change across the populated weeks, and a `direction`. **The trend only, never
  the 28 daily points** — the athlete's ask, and the daily series stays the
  dashboard's. Easy to re-break:
  (1) **`direction` is computed in code** (`_vo2max_trend` in
  `app/ai/prompt_builder.py`), not left to Claude, because it gates prompt rule
  22 — the `app/metrics/analysis.py` split again. It is classified against the
  athlete's own **`vo2max_decline_threshold`** (profile field, ml/kg/min,
  default `DEFAULT_VO2MAX_DECLINE_THRESHOLD` = 0.5, bounded `0 < v <= 5` so a
  typo like 50 cannot silently disable the rule for good): a smaller change is
  Garmin's jitter and must not schedule a Z5 session. Verified against live
  data the day it was built — bike −1.4 read as declining, run −0.2 as stable,
  which is exactly the false positive the deadband exists to stop. Fewer than
  two populated weeks, **or nothing in the last `VO2MAX_TREND_FRESH_WEEKS`
  (2)**, is `insufficient_data` rather than a decline: readings stop when the
  qualifying activity stops (cycling VO2max needs a power meter at all), and
  three-week-old averages must not put intervals in the week. `change` is
  `null` there, not `0.0`, which would read as a measured no-change.
  (2) The threshold follows the `activity_class` precedent exactly — the default
  is applied in **both** the Pydantic model and `get_athlete_profile`, since the
  Claude payloads read the profile dict directly. The payload read itself still
  falls back (`profile.get(...) or DEFAULT_...`): a monkeypatched or
  hand-assembled profile must not fail a whole plan over one tunable.
  `PROFILE_FIELDS` in `SettingsModal.tsx` gained optional `min`/`max`/`step`
  because its input hardcoded `min={1}` with no step, which rejected 0.5
  outright; `min: 0.1` is effectively "any drop at all", since every reading,
  weekly average and the change are rounded to one decimal.
  (3) The calendar day comes from `_row_date` in `app/metrics/calculator.py`,
  reused rather than re-derived: `vo2max` points follow the `daily_load`
  midnight-UTC convention and a local conversion shifts the day west of UTC.
  (4) The key is **omitted entirely** when nothing survives (the `recent_race`
  precedent) — which is also what keeps every existing payload test green: their
  FakeRepos ignore the flux and answer every read with workout rows, and a
  workout row has a `discipline` but no numeric `vo2max`, so the helper skips it
  in silence. A new fake must discriminate on `_measurement == "vo2max"` in the
  flux.
  (5) `vo2maxTrend` in `frontend/src/derive.ts` is a different thing and must
  stay different: it forward-fills a dense daily line for drawing, this one
  averages weeks and never fills a gap.
  (6) Prompt **rules 21-23**, appended after rule 20 for the same numbering
  reason rules 17-20 were. A declining discipline **converts** one existing hard
  session into a Z5 session rather than adding one (rules 2, 3 and 6 all still
  bind, as does the per-distance "two hard run days, never back-to-back"); rule
  4 still picks the sport, so a declining non-race discipline sends the session
  into a race discipline instead of reopening the bike on a run goal; an
  existing phase VO2 session or rule 16's fartlek satisfies it instead of
  stacking; and rules 1-3, 17-20, TSB under −25 and a taper all outrank it.
  **Not code-enforced, on purpose**: `athlete_custom_instructions` can override
  it, and the rest day stays the only guardrail enforced in code.
  (7) A threshold edit does **not** invalidate the cached plan —
  `_plan_is_current` watches only `coach_instructions.txt` and `schedule.json` —
  so the athlete uses the existing "Re-sync plan" button. Adding the profile
  YAML there would spend a Claude call on every weight tweak.
  `tests/test_vo2max_trend.py` pins all of it.
- **Goals are called events (2026-09-21)**: the athlete renamed "goal" to
  "event" across the dashboard, the REST API (`/api/events`, `event_id`,
  `/api/weather/{event_id}`) and the Python/TypeScript names (`events_service`,
  `Event`/`EventIn`/`EventType`; `RaceEvent`/`RaceEventIn` in the frontend,
  since `Event` is the browser's built-in DOM type). Three things deliberately
  still say goal: (1) **storage** -- the InfluxDB `goals` measurement and
  `goal_id` tag (`EVENTS = "goals"` in `app/influx/client.py`); renaming them
  orphans every saved event, the same reason the `tricoach` names survived the
  rebrands. `_row_to_event` maps the stored `goal_id` to the API's `event_id`.
  (2) **Everything Claude sees** -- the `primary_goal` payload key,
  `_goal_block`, the prompts' "goal" wording and the coach context's `Goal:`
  line; tests pin that text. (3) **Sub-goals** -- targets within an event, not
  events themselves (`sub_goals`, the "Sub-goals" field). The PRD still says
  goals throughout.
- **Past events fade, then hide (2026-09-25)**: a finished event is drawn at
  `PAST_EVENT_OPACITY` in the events row and the Manage events list, and drops
  out of both `PAST_EVENT_VISIBLE_DAYS` (14) after its race date
  (`visibleEvents` / `isPastEvent` in `frontend/src/derive.ts`). **Display only
  -- never delete it from storage**, which is what the athlete first asked for:
  post-race recovery reads 21 days back (`RECENT_RACE_DAYS`, and rule 18's table
  runs to day 22) and the Training Days grid frames race days 60 back, so only
  `EventRow` (and the modal it opens) gets the filtered list; `TrainingDays` and
  `PredictedWeek` keep the full one (less calendar-only events, see the next
  bullet), and `GET /api/events` is unfiltered.
  `visibleEvents` keeps date-less events (`"" < cutoff` is the `last_race`
  trap). The primary is picked from the visible list: the nearest upcoming
  event, else the most recent visible finished one (faded), else none.
- **Participating toggle (2026-09-30)**: each event carries `participating`
  (bool, default true), a switch in the Manage events footer next to Delete. Off
  means calendar only: the coach never treats it as a goal. Design handoff:
  `assets/Triathlete dashboard design-participate toggle.zip` (option 1c). Easy
  to re-break:
  (1) **The filter lives in `events_service.active_event()` and `last_race()`**,
  the only two readers behind `primary_goal` / `recent_race`, so the plan, the
  coach chat and the workout analysis all drop the event with no prompt or
  payload change. `last_race` is filtered too: an event the athlete was not
  racing buys no post-race recovery window. With nothing else upcoming the plan
  falls to `Custom_Personalization.txt` and the coach says "no race goal set".
  (2) **No migration**: events saved earlier have no `participating` column, and
  the reads stringify every value, so `_row_to_event` treats only an explicit
  `"false"` as off. Keep it that way; `is_participating` defaults the same way
  for plain dicts, which is why every older event fixture stays valid.
  (3) **Dashboard split**: the Primary Event panel is chosen from participating
  events only (`primaryOf`), and `TrainingDays` / `PredictedWeek` get
  `racingEvents` (race-day ring and race tile are drawn only for events the
  athlete is racing). A calendar-only event stays in **All events** and the
  Manage events list, dimmed (`NOT_RACING_OPACITY`) with "not racing" in place
  of the countdown: that list is the only way back into the modal to switch it
  on again, so hiding it there would strand it. The README's "removed from the
  goals row" is read as the Primary Event panel.
  (4) A past event's switch is locked (judged on the draft's date, so moving it
  forward unlocks it).
  (5) **Not sent to Claude**: the design's optional "Calendar only, not racing"
  context line was left out on purpose. A toggle change also does not
  invalidate the cached plan (`_plan_is_current` watches only instructions and
  schedule, like an event date edit): it takes effect at the next Re-sync plan.
  `tests/test_event_participation.py` pins the backend half.
- **Plan changes survive a re-sync (2026-09-27)**: before this, every plan
  regeneration rewrote `data/latest_recommendation.json` from scratch and the
  sessions the athlete had asked the coach to change were silently lost.
  `app/plan_requests.py` keeps them in `data/plan_requests.json`. Things easy to
  re-break:
  (1) **The whole day is saved, not the edit.** `_save_plan` in
  `app/plan_editor.py` saves each touched day's full `sessions` list AFTER the
  edit, keyed by date (a move saves both days; an empty list is a day off the
  athlete asked for), and sets `athlete_requested: true` on the plan day. The
  tools address sessions by `(date, session_index)` in the plan they edited, and
  those indices mean nothing in a regenerated plan, so edit operations must
  never be replayed.
  (2) **Expiry**: a request is dropped once its day has passed, never later than
  `MAX_AGE_DAYS` (7) after the edit -- the athlete's call. `active_requests()`
  deletes expired entries on read.
  (3) **The athlete is asked before the run starts**, from both buttons
  (`startSync`/`startReplan` in `useDashboardData.ts` read
  `GET /api/recommend/requests`; `PlanRequestsModal` asks). The answer rides
  as `keep_requests` on `POST /sync` and `POST /recommend/trigger`, and the
  **backend default is keep**, so no caller loses a request by omission. With
  no requests there is no prompt and no parameter, exactly as before.
  **Overwrite always regenerates**, even when the sync would skip the plan
  (`_run_sync` only skips when `keep_requests`); the requests are deleted only
  AFTER the new plan is saved, so a failed Claude call loses nothing. The
  answer is taken from a ref (`promptRef`), not the state: two answers in one
  tick once started two paid runs.
  (4) **Keep is enforced in code**, the `_enforce_rest_days` split:
  `build_plan_payload` sends `athlete_requested_days` (days + code-computed
  `totals`, only dates inside `plan_days`, omitted when empty) and prompt
  **rule 24** tells Claude to copy them verbatim and budget the free days
  against them -- weekly TSS, training days, the consecutive limit, 80/20 and
  TSB. `_apply_athlete_requests` in `app/recommend_service.py` then copies
  them back over Claude's output. `_enforce_rest_days` skips requested dates: a
  session there is the athlete's own, insisted on through the coach, so it is
  neither cleared nor flagged.
  (5) Each coach tool requires an `athlete_request` sentence (the note saved with
  the day), since the athlete's raw message is often just "yes";
  `_run_coach_tool` falls back to the message.
  (6) `tests/conftest.py` points `REQUESTS_FILE` at a temp file for EVERY test:
  a plan edit now writes the store, so a test without it writes into the
  athlete's live `data/`. `DELETE /api/data` also clears the requests.
  `tests/test_plan_requests.py` pins all of it.
- **A finished workout is never a plan session (2026-09-28)**: a plan
  regenerated after a sync sees the morning's workout in
  `last_7_days_workouts`, and Claude wrote it back into today as a session
  ("Completed earlier today", same minutes and TSS). The dashboard already draws
  the logged workout on today's tile, so the athlete saw it twice, and the Load
  Forecast and the week's Planned total counted its TSS on top of the stored
  load. Cause: prompt rule 5 said to "acknowledge it in the day's notes", and a
  plan day has no notes field, so a session was the only place left for it. Rule
  5 now sends the acknowledgement to `summary`/`coach_notes` and forbids the
  session outright; `_drop_completed_sessions` in `app/recommend_service.py`
  removes any that still slip through. It matches **discipline and duration**
  (within the larger of 10 min or 25% of the logged workout; one logged workout
  cancels one session), not discipline alone, which would delete the race on
  race morning after a shakeout jog. It skips athlete-requested dates, like
  `_enforce_rest_days`, and only logs -- it is a data fix, not a coaching
  guardrail, so the rest day stays the only guardrail enforced in code.
  `tests/test_completed_today.py` pins it.
- **Coach changes settings (2026-11-07)**: the `update_athlete_settings`
  coach tool (`app/ai/claude.py`) over `app/settings_editor.py`. Things easy to
  re-break:
  (1) **What it may change**: `COACH_PROFILE_FIELDS` (FTP, LTHR, run threshold
  pace, CSS, weight, VO2max decline threshold) and the coach instructions. The
  birthday, the activity class and the training schedule are Settings-only (the
  athlete's call), refused by name in `update_settings` as well as absent from
  the tool schema (`additionalProperties: false`). "Week starts on" is browser
  storage and out of reach.
  (2) **Approval first, checked in code**: the coach must first reply with the
  bold approval message, `SETTINGS_APPROVAL_MARKER` ("**Approve settings
  change?**") then one bold line per change, old -> new. `_approval_asked` reads
  the last coach message in the chat history, and without the marker there the
  tool returns an error and saves nothing. The guard proves the athlete saw the
  change; whether their reply is a yes is the model's reading. An approval
  covers one turn only: the next change needs a new message.
  (3) **One save path**: `save_profile` and `save_instructions_and_notify` live in
  `settings_editor.py`, and `PUT /athlete` / `PUT /coach/instructions` call the
  same functions, so the Influx mirror rule and the season plan's re-read are the
  same either way. A changed threshold also runs `recompute_stored_metrics(days=7)`,
  like Settings' Re-calculate; a recompute failure is reported, the save stands.
  The instructions are sent whole (the coach keeps the saved text unless asked).
  (4) `coach_reply` returns `(reply, plan_updated, settings_updated)`; the route
  adds `settings_updated`, and the chat refreshes the dashboard and tags the
  turn "settings updated". With "Include context" off there are no tools at all.
  Neither change invalidates the cached plan beyond what Settings already does
  (instructions do, thresholds do not): the athlete uses Re-sync plan.
  `tests/test_coach_settings_tool.py` pins all of it.
- **Chat suggestion chips (2026-11-14, the athlete's ask)**: the three chips
  above the chat composer follow the conversation; they were fixed strings.
  Things easy to re-break:
  (1) **The coach writes them in its own reply, no second Claude call**:
  `COACH_SUGGESTIONS_RULE` (`app/ai/claude.py`) is appended to the context in
  the `POST /coach` route, for "Include context" on and off, and asks for a
  last line `<<suggestions>>` plus a JSON array of 3 questions.
  `split_suggestions` cuts the block off in the route, so the athlete and the
  history sent back never see it; a missing or bad block gives `[]` and the
  reply stands. `coach_reply` is unchanged.
  (2) **A clean chat reads the last sync, in code**: with no question asked yet
  (first load, after /clear), `syncSuggestions` in `frontend/src/derive.ts`
  names the latest workout (last 3 days), tomorrow's planned session and form
  (lighter week, then the Friel limits), each slot with a fixed fallback.
  With "Include context" off a clean chat shows `GENERAL_SUGGESTIONS`, which
  name no athlete data.
  (3) The chips are the newest coach message that has `suggestions` (saved on
  the `ChatMsg`, so they survive a reload); /help and /summary replies have
  none, so the earlier set stays. They are hidden while a turn is in flight.
  `tests/test_coach_suggestions.py` pins the backend half.
- **Dragging a planned session (2026-11-01)**: `POST /api/recommend/move-session`
  over `move_session`, driven by `frontend/src/useSessionDrag.ts` and
  `PredictedWeek`. Things easy to re-break:
  (1) **The rest-day refusal is in the route, not in `move_session`**: the coach
  may still put a session on a rest day when the athlete confirms it in the chat,
  and only a schedule `rest: true` day is refused, not a day the coach left empty
  (`is_rest_day`), which is free to take a session. The route also refuses a past
  source or target. Race-day tiles are refused in the UI only, and their cards do
  not lift. `max_sessions`, the consecutive limit and 80/20 are not checked: a drag
  is a deliberate choice, the coach-edit split.
  (2) **The tiles read the schedule, not `settings.recoveryDays`**: that is this
  browser's copy, empty until Settings has been saved here, so `PredictedWeek`
  fetches `GET /schedule` for its refused styling.
  (3) **The move is optimistic and needs no `refresh()`**: `moveSession` in
  `useDashboardData.ts` sets the plan with `movePlanSession` (`derive.ts`, which
  mirrors `move_session`), then the backend's answer, or the old plan on failure.
  `flushSync` in `finish` commits it so the new slot can be measured for the flight.
  (4) **Pointer events and the Web Animations API, no HTML5 drag and no library**:
  the native ghost cannot tilt or land. Pointer moves write the floating copy's
  transform directly, so they cause no render. `flyTo` races `finished` against a
  timeout, because a hidden tab stalls the animation and would leave the copy on
  screen with every later drag blocked. Mouse and pen only: touch keeps scrolling.
  (5) **A move leaves an attachment stale**: its `(date, session_index)` then names
  another session, so `App.tsx` clears a plan attachment on either day. Dragging is
  locked during a sync or re-plan, which rewrite the plan file.
  (6) **Send to device is not followed**: a session already sent stays on its old
  day in intervals.icu and on the watch, since nothing records what was sent. The
  athlete sends it again from the new day and removes the old entry there.
  `tests/test_plan_move_route.py` pins the route.
- **Workout stream graphs (2026-11-06)**: `WorkoutModal` fetches
  `GET /api/workouts/{id}/streams` (`workout_streams` in
  `app/analysis_service.py`: the stored FIT samples through `bucket_stream`, the
  same 30 s buckets the analysis reads, plus `power_zone_split` seconds for a
  ride) and draws them with `frontend/src/charts/WorkoutStreams.tsx`. Design:
  the design system's `WorkoutStreams` card. Things easy to re-break:
  (1) **Power zones are bike only**: the bands are %FTP and the profile has no
  running FTP, so run power gets a graph but no zone bar.
  (2) **Colours**: pace/speed is the discipline colour, heart rate `C.red`,
  elevation `C.elevation` (muted3), cadence `C.cadence` (indigo) and power
  `C.power` (pink) -- the season plan's build and peak bracket hues, chosen
  because neither may read as a sport or a zone.
  (3) **One hover for all graphs**: the crosshair runs through every graph and
  each head reads its value at that moment; there is no tooltip box. The x axis
  is moving time (`t`, pauses removed by the parser).
  (4) Ranges sit on round steps around the 3rd-97th percentile for pace, speed,
  HR, cadence and power, clamping outliers, so one stop does not flatten the
  line; pace runs fast at the top and is not drawn below 0.5 m/s.
  (5) **No samples, old tiles**: a workout synced before FIT samples were kept
  (the endpoint 404s) keeps the metric tiles; the modal is 70% of the viewport wide
  (`ModalShell` gained a `width` prop, number or CSS string, default 560;
  720px until 2026-11-09, the athlete's call).
  (6) **Efficiency graph (2026-11-14)**: the last graph, below Power, is output
  over HR (`efficiencySeries`): Pw:HR in W/bpm when power is recorded, else
  Pa:HR in metres per beat, smoothed over a centred 5-minute window, in
  `C.efficiency` (= the CTL teal). The dashed half levels and the drift % in
  the head mirror `aerobic_decoupling` in `app/metrics/analysis.py` exactly
  (same output pick, split by count, ratio of half means, drift only from
  45 min), so the graph and Analyze report the same number. Frontend only.
  `tests/test_workout_streams_route.py` pins the endpoint.
- **Garmin Training Effect (2026-11-09)**: `aerobic_te`, `anaerobic_te` (floats,
  0.0-5.0) and `te_label` (Garmin's `trainingEffectLabel` key, upper case:
  TEMPO, LACTATE_THRESHOLD, RECOVERY, ...) on the workout point, read from the
  Personal API activity list row by the parser (each brick leg carries its own).
  Drawn by `TrainingEffectSection` (`frontend/src/charts/TrainingEffect.tsx`)
  below Time in zones in `WorkoutModal`. Design: the "Training Effect Gauges"
  canvas and the design system's `TrainingEffect` card. Things easy to re-break:
  (1) **0.0 is a value, not a blank**: the parser's `_te` keeps it (its `_f`
  turns 0 into None), and "0.0 No Benefit" is the usual anaerobic answer for an
  easy session. Out-of-range values are None.
  (2) **Display only, like recovery**: `_recent_workouts` and `_workout_block`
  in `prompt_builder.py` whitelist their keys, so no plan, chat or analysis
  payload carries Training Effect; `tests/test_training_effect.py` pins it.
  The one exception, since 2026-11-21: a workout the athlete sends to the chat
  (see "Workout data in the coach chat").
  (3) The section is left out when neither score is stored (workouts synced
  before this, activities Garmin does not score). The bands are Garmin's
  (No Benefit, Minor, Maintaining, Impacting, Highly Impacting, Overreaching at
  5.0) on the HR-zone ramp, `C.muted` then `HR_ZONE_COLOR` Z1-Z5; the benefit dot
  follows the same ramp, so Tempo is yellow (orange in Garmin's app).
  (4) Influx merges a point's fields on write, so a recompute that rewrites
  TSS keeps the stored Training Effect. `scripts/backfill_training_effect.py`
  (`--days`, default 7, dry run unless `--apply`) writes only the three fields
  onto stored workouts from the list endpoint, no FIT files; it was applied for
  the last 7 days on 2026-11-09 (4 workouts).
- **Workout RPE and feel (2026-11-14, the athlete's ask)**: `rpe` (1-10) and
  `feel` (0-100) on the workout point, the answers the athlete gives on the
  watch when saving. Drawn by `WorkoutRating`
  (`frontend/src/charts/WorkoutRating.tsx`) in the same modal section as the
  Training Effect dials: two tiles in the dials' two centred columns, a third of
  a dial tile's height (300 x 44 at the design width). Things easy to re-break:
  (1) **Source**: the FIT session message, `workout_rpe` (10-100 in tens,
  stored divided by 10) and `workout_feel` (0/25/50/75/100: Very Weak, Weak,
  Normal, Strong, Very Strong), confirmed live 2026-11-14; `fit._rating` also
  reads them by field number (193 / 192). `fit.decode` returns
  `(records, laps, rating)`.
  (2) **A feel of 0 is "Very Weak", not a blank**, and either answer can be
  there without the other (one tile is then drawn, centred). An indoor ride
  saved with no rating has neither, and the block is left out.
  (3) **Only a fetched FIT file sets them**: the parser reads `_rpe` / `_feel`
  from `enrich_with_fit`, and a workout that already has samples is parsed with
  None, which `write_workouts` skips, so a re-sync never blanks a stored rating.
  A rating changed later in Garmin Connect is not picked up (the FIT file is
  not re-fetched). `scripts/backfill_rpe.py` (`--days`, default 30, dry run
  unless `--apply`) writes only the two fields; applied for 30 days on
  2026-11-14 (14 of 19 workouts had a rating).
  (4) **Not sent to Claude**, like Training Effect: the payload blocks whitelist
  their keys and `tests/test_workout_rating.py` pins it. The one exception,
  since 2026-11-21: a workout the athlete sends to the chat (the next bullet).
- **Workout data in the coach chat (2026-11-21, the athlete's ask)**: Ask coach
  on a completed workout (now on every workout, not only after Analyze) and
  `/last` send the workout's full recorded data. Things easy to re-break:
  (1) **Not in the message**: the user turn carries one header line and
  `workoutId` (`ChatMsg`); `POST /coach` gets `workout_ids` (the distinct ids of
  this turn and the 20-message history window, newest first, at most 3, the
  analysis route's id pattern), and `attached_workouts_context` in
  `app/ai/workout_context.py` appends one block per workout to the system
  context. So the data is never shown in the bubble or repeated through the
  history, and follow-up turns still see it. Ignored with "Include context" off.
  (2) **The block is everything the modal shows**, read from storage: summary,
  load impact against a rest day, Training Effect, RPE / feel, HR and power
  zones, the derived metrics (`build_derived_metrics`; decoupling is the
  efficiency graph's drift), the planned workout, laps, the 30 s streams
  (`bucket_stream`, the graphs' buckets) and the cached analysis.
  (3) **Kept small**: tables are CSV with empty columns dropped, numbers rounded
  to the graphs' precision, paces already min:sec (rule P8) and bike in km/h.
  Measured on live data: about 4.4k characters for a 53-min run, 11.5k for a
  98-min ride. A workout with no samples sends its summary; a failure leaves
  that workout out and never fails the turn.
  `tests/test_coach_workout_attachment.py` pins it.
- **Workout load impact (2026-11-17, the athlete's ask)**: `LoadImpactSection`
  (`frontend/src/charts/LoadImpact.tsx`) in `WorkoutModal`, above Training
  Effect in the same section, for any workout with TSS > 0. Design: the
  "Workout Load Impact" canvas. Frontend only. Things easy to re-break:
  (1) **The change is against a rest day**, the workout's own share of the
  linear EWMA (`_step`): CTL +TSS/42, ATL +TSS/7, Form = their difference.
  Never the day-over-day change, which goes negative for an easy session on a
  high-CTL day and reads as the workout costing fitness.
  (2) **One scale for all three, 1 dot = 1 point, 15 dots**, so the bars show
  ATL moving six times as far as CTL; the last dot fills partially, a bigger
  change fills the bar and the number carries the rest. Colours `C.ctl` /
  `C.atl` / `C.tsb`.
  (3) **Same height as the Feel tile at any width**: every length is
  `min(px, (100cqw - 10px) * px/600)`, the Feel tile's own rule (44px while
  the dial column is 300px). The row runs to 780px, wider than the dials,
  since three tiles do not fit in 610.
- **Lighter week after a monotonous load (2026-11-13, the athlete's ask)**:
  `app/metrics/load_trend.py` (pure, every number at the top) reads the last
  `WEEKS` (6) complete Monday-start calendar weeks of swim/bike/run/strength TSS,
  the same buckets as the TSS by Week chart, by the stored `date` field, with the
  current partial week left out. The load is monotonous when it is a
  **plateau**, meaning every week is within `PLATEAU_TOLERANCE_TSS` (25) of the
  week *before* it, so a slow drift still counts (the athlete's choice over
  "within 25 of the mean"). It is also monotonous when it is **rising**: at least
  4 of the 5 steps go up, and at most one is level or down by any amount. Things
  easy to re-break:
  (1) **Any 0-TSS week disables it**: six empty weeks are not a plateau, and a
  new install has no history.
  (2) `build_plan_payload` makes its own 49-day read (`HISTORY_DAYS`) and leaves
  `PLAN_HISTORY_DAYS` at 28, because `RECENT_RACE_DAYS` depends on it. The
  `load_reduction` key is present only when the check triggers. It carries
  `target_tss` = 85% of the last complete week and a `message` written in code,
  which is the text the athlete reads.
  (3) **Rule 32** (appended, so nothing is renumbered): the 7 days add up to at
  most `target_tss`, cut mostly through intensity (one quality session at most,
  everything else Z1-Z2, no new rule 22 Z5). It outranks the season budget,
  which becomes a ceiling (`_check_season_target` sets `ceiling_only`). Rules
  1-3, 17-20, 24 and 31 still bind, and `headline` and `summary` must say why
  the week is lighter.
  (4) `_check_load_reduction` in `recommend_service.py` stamps the block on the
  plan (adding `planned_tss`). It flags a plan more than 10% over the target and
  never rewrites one. Being under the target is fine.
  (5) The surfaces: `load_reduction_notice` on the sync result (only when a plan
  was generated), the notice strip (it wins over the season notice; Re-sync plan
  shows it too), a "Lighter week" block in `PlanSummaryModal`, and one line in
  the coach context read off the cached plan.
  The detection only changes at a week boundary, so the plan is not
  invalidated for it. `tests/test_load_reduction.py` pins all of it.
- **Send to device (2026-09-29)**: the Send to device button on `SessionModal`
  (Predicted Week only; it needs `sessionIndex`) calls `POST /api/device/send`
  with `(date, session_index)`. The route (`app/routers/device.py`) reads the
  session from the cached plan (`find_session` in `app/plan_editor.py`, a
  read-only copy), turns it into an intervals.icu WORKOUT event
  (`app/intervals/workout.py`, pure) and upserts it
  (`app/intervals/client.py`: `POST /athlete/0/events/bulk?upsert=true`, Basic
  auth `API_KEY:<INTERVALS_API_KEY>`, a non-default User-Agent because
  Cloudflare rejects the library defaults). It started from the athlete's
  working script, `upload_run.py` in the sibling `Intervals` folder. Things easy
  to re-break:
  (1) **Targets**: run steps are **% LTHR** (the athlete's choice), which
  intervals.icu turns into bpm with the LTHR saved **in intervals.icu**, so that
  value must match the Jymalaya one. Bike steps are absolute watts (the plan's
  `target_power_w`, else the %FTP band x `ftp_w`) and swim steps are absolute
  pace per 100 m (`css_sec_100m` x the prompt factors), so neither depends on the
  zones set up in intervals.icu. Strength is time only. The bands are
  `prompts/training_zones.txt` with the open ends **closed** for a watch (run Z5
  100-106% LTHR, bike Z1 floored at 45% FTP, which also raises a "0-104"
  target, swim Z1 1.35 and Z5 0.90); a missing threshold means no target, as in
  P7.
  (2) **Interval sets go as `Nx` repeats** (2026-10-18, the athlete's call;
  sent unrolled before): one `- <cue> <duration> <target>` line per plan
  segment, then `_fold_repeats` folds runs of identical lines into an `Nx`
  block, closed by a blank line, with `groupStructure`'s rules (patterns of
  1-3 steps, widest coverage, shorter pattern on a tie, the run-start guard).
  It compares the rendered lines, so `_cue`'s dropped rep numbers line the
  reps up and notes play no part. Two differences from the modal: a repeat
  needs **two full reps**, and a short last rep (5 hard, 4 easy) is sent as
  `4x` plus the lone hard step, never `5x` -- the watch must get the plan's
  time, not an extra recovery.
  (3) **Only the segment name reaches a step line.** intervals.icu parses the
  text before the first duration as the cue, so `_cue` drops every word that has
  a digit in it, `Z<n>`, single letters and target keywords ("Interval 1" ->
  "Interval"). Segment notes (full of "5:21/km") never reach a step line, and the
  session notes are left out of the description altogether (2026-09-30, the
  athlete's call: short). The description is a heading ("Run intervals"), the
  step lines, then "Benefit - ..." from the session `type` (`TYPE_BENEFIT`;
  intervals with a Z5 segment read "VO2max increase", with Z4 "threshold power
  increase"; no type, no benefit line).
  Durations use `h`/`m`/`s` (`m` is minutes in intervals.icu; metres are `mtr`).
  (4) **`external_id` = `jymalaya-<date>-<session_index>`** is the upsert key:
  sending the same slot again updates the calendar entry instead of adding a
  second one. Verified live on 2026-09-29: the same event id came back, there
  was still one entry, and the athlete found the workout on their Garmin
  Connect calendar. The key is the slot, not the session, so a session
  moved to a new index goes up as a new entry.
  (5) A day that has passed is refused (409) and its button is disabled. Errors
  map as: no key 503, an intervals.icu or network failure 502, nothing with a
  duration to send 422, an unknown date or index 404. The modal shows the
  backend's detail as "Send failed: ...".
  (6) `moving_time_sec` in the response is the length intervals.icu read from the
  steps: a quick check that the text parsed. The first live send read 52 min for
  a session whose `duration_min` said 55. That was right: its segments add up to
  52, and Claude's stated total was 3 min out.
  (7) **Every step line ends in an `intensity=` tag** (2026-11-06): warmup,
  active, rest or cooldown, which Garmin shows as Warm Up, the sport's name,
  Rest and Cool Down. Untagged steps were left to intervals.icu's "auto", and
  the 12 Oct threshold run reached Garmin as six "Run" steps. `_intensity`
  reads the segment name first ("warm", "cool", then work words, then
  recovery words after the first step), then -- only in a session with a Z3+
  step -- makes an easy (Z2 or below) first step the warm-up, last step the
  cool-down, and a middle one next to a hard step a rest. Everything else is
  active, so a steady or recovery run is never sent as Rest. The tag is part
  of the rendered line, so `_fold_repeats` still folds a set: every rep gets
  the same tag. Rest (not `recovery`, Garmin's "Recover") is the athlete's
  choice; switch if a watch drops the target on Rest steps.
  `tests/test_intervals_workout.py` and `tests/test_device_route.py` pin it.
- **Garmin Personal API and NGP (2026-11-06)**: `app/garmin/personal_api.py`
  replaces the library for workouts, HRV, resting HR and the avatar; every
  field Garmin returns (used or not) is listed in `assets/garmin_parameters.md`,
  taken from live responses of a one-off probe script (deleted 2026-11-09).
  Auth: the athlete's key
  (`GARMIN_PERSONAL_API_KEY` in `.env`, made on Garmin Connect's "Personal API
  Key" page, expires after 30 days to a year) is exchanged for a plain-text
  24-hour token, cached in memory, re-requested once on a 401. Things easy to
  re-break:
  (1) **Brick legs come inline**: with `excludeChildren=false` each multisport
  child is its own list row with `parentId`. `_fold_children` moves them into
  the parent's `_legs` and off the top level; left there they would be stored a
  second time. A leg's stored id stays `{parent}_{suffix}` (`leg_activity_id` in
  the parser), and its FIT file is its own real child id.
  (2) **FIT files never stay on disk**: `enrich_with_fit` downloads into
  `data/fit_tmp`, decodes (`app/garmin/fit.py`, `fitdecode`), and deletes in a
  `finally`; `purge_fit_tmp` runs at startup and before every sync. What is kept
  is in InfluxDB: `workout_samples` (5 s buckets on the activity clock, pauses
  over 60 s skipped, true UTC instants, fields t/hr/speed_mps/altitude_m/
  distance_m/power_w/cadence; run cadence is steps/min), `workout_laps` and
  `workout_planned` (the structured workout as JSON). **No coordinates are ever
  read.** These are separate measurements on purpose: every workouts read
  pivots all fields, so detail on the workout point would ride into every
  dashboard and Claude payload.
  (3) **What is fetched is decided by stored samples** (`_sampled_ids`), not by
  `known_ids`: a workout whose FIT failed keeps average-pace TSS and is fetched
  again by the next sync. A FIT failure never fails a sync.
  (4) **NGP (`app/metrics/ngp.py`)**: grade from the samples' altitude and
  distance over a centred 30 s window (clamped to +/-30%, at least 5 m of
  travel), the Minetti (2002) running-cost curve normalised to flat, then the
  spec's 30 s rolling mean and 4th-power mean. The FIT file has no
  grade-adjusted speed (Garmin computes its own server-side). On the
  2026-11-04 fartlek: average pace 55.7, no grade 60.6, Garmin's GAP 62.0,
  this model **66.9**, TrainingPeaks 67 at the same 5:21/km threshold.
  `_intensity` uses `ngp_sec_per_km` when stored, else average pace, else HR;
  `tss_method` (power / ngp / pace / css / hr) is stored on every workout.
  Runs synced before 2026-11-06 keep average pace until
  `scripts/backfill_fit.py --days N --apply` reaches them; the first run was 30
  days. Older runs stay on average pace, so CTL mixes both for about six weeks.
  (5) **Failure prompt**: a `PersonalApiError` (a `GarminPullError`, so status
  handling is unchanged) carries `code` (no_key / key_rejected / garmin_error /
  network) and `action`; `POST /sync` answers 502 with `{code, message,
  action}` and `GarminErrorModal` shows them. A key problem offers a key field:
  `PUT /api/garmin/api-key` checks the key with Garmin first and only then
  writes it into `.env` (`dotenv.set_key`); with no `.env` (a container on an
  env_file) it answers 409 and the athlete edits that file.
  (6) **Analyze workout makes no Garmin call** (the athlete's call): it reads
  the stored samples, re-buckets them to the 30 s / 240-bucket stream the old
  detail fetch produced (`app/workout_detail.py`, same keys, so
  `app/metrics/analysis.py` and the prompt are unchanged), plus laps and the
  planned workout. A workout with no samples is refused with 409 naming the
  backfill command, never fetched. `app/garmin/detail.py` is gone.
  (7) **VO2max stays on the library**, with its token guard: the library login,
  its token cache and the MFA modal now exist for VO2max alone. Without a
  session the panel just stops updating; `python -m app.garmin.client` logs in
  again on a terminal.
  `tests/conftest.py` makes every Personal API request fail as offline, so no
  test can use the real key in `.env`; `tests/test_personal_api.py` restores
  the real functions over a fake httpx.
- **Weather**: **Open-Meteo** (no key) for goal-location forecast / historical climate.
  Dashboard-only: weather is deliberately NOT included in the Claude plan payload
  (a conscious divergence from PRD §7.7's weather rule). **Once a day
  (2026-11-13, the athlete's ask)**: `GET /api/weather/{event_id}` reads through
  `cached_weather_for_event` in `app/weather/openmeteo.py`, an in-memory cache
  keyed on today's date plus the event's id, race date and location, so an event
  edit fetches at once and a restart just fetches again; an `unavailable` result
  is never cached. An open dashboard tab refetches a minute after local midnight
  (`useDashboardData.ts`). `tests/conftest.py` empties the cache for every test.
- **Credentials**: Garmin email/password, InfluxDB token, the Anthropic API key and
  the intervals.icu API key (`INTERVALS_API_KEY`, Send to device only) live only in a local `.env` (gitignored), loaded via `python-dotenv`. Never log them;
  never transmit them anywhere except their official endpoints.
- **API prefix (invariant — the dashboard breaks silently without it)**: every
  router is mounted under **`/api`** (`API_PREFIX` in `main.py`), because the
  container serves the built SPA from the same origin at `/`. The frontend has
  always addressed the backend as `/api` (`frontend/src/api.ts`, plus the
  standalone `/api/athlete/avatar` in `TopBar.tsx`); until 2026-07-28 the Vite
  dev proxy stripped the prefix, so the routers sat at the root. The proxy now
  forwards it untouched and the backend owns it, so dev and container behave
  identically. Never move the routers back to the root — every dashboard call
  404s, and the avatar quietly falls back to the gradient square. Tests reach
  them via `TestClient(app, base_url="http://testserver/api")`, which is why
  they still use bare paths.
- **Deployment (Phase 2, done 2026-07-28)**: `Dockerfile` is a two-stage build —
  `node:22-alpine` runs the Vite build, `python:3.12-slim` runs uvicorn as a
  non-root user and serves that build from `frontend_dist/` (mounted last in
  `main.py`, after `/api` and `/docs`, since it matches everything).
  `docker-compose.deploy.yml` is a **separate compose project**
  (`name: jymalaya-c1`, app container `Jymalaya-C1` + its own
  `influxdb`) so it never collides with the Phase 1 `docker-compose.yml` /
  `jymalaya-influxdb` dev stack or its volumes. Three writable locations need
  volumes: `/app/data`, `/app/config` (for `athlete.local.yaml`), and
  `/home/app/.tricoach` — the last one is `Path.home()`-based, and without it
  every restart forces a fresh Garmin login and MFA. Compose overrides
  `INFLUXDB_URL` to `http://influxdb:8086` and sets `TZ`, since plan freshness
  is decided per the athlete's calendar day. `requirements.txt` is pinned for
  reproducible builds; `pytest` lives in `requirements-dev.txt`.
  **Both compose files pin `name:`** — a Compose project name otherwise defaults
  to the directory name, so a repo-folder rename silently re-points the stack at
  a fresh, empty set of volumes. For the same reason the dev stack's two volumes
  keep their pre-rename `brickplans_` names (explicit `name:` under `volumes:`);
  the deploy stack's moved to the `jymalaya-c1_` prefix, with the copy commands
  in README.
- **Single container (2026-09-22)**: the athlete asked for Jymalaya-C1 as ONE
  container — dashboard, backend and InfluxDB — clean of data like a new
  athlete's install, runnable on another computer, **without deleting the
  two-container stack above** (both still build). **Its files live outside the
  repo, in `C:\Users\shmul\Workspace\python\Jymalaya-Container`** (the
  athlete's call, 2026-11-07, when they were rebuilt: the first `c1/` copies
  were never committed and were lost). Its `Dockerfile` copies the
  statically linked `influxd`/`influx` binaries out of `influxdb:2.9.1` onto the
  Python runtime; `entrypoint.sh` (under `tini`) starts influxd on
  `127.0.0.1:8086` only, provisions it on first start (org/bucket `tricoach`,
  random admin password and token), then runs uvicorn, and exits when either
  process dies so the restart policy restarts both. Its `docker-compose.yml`
  builds with this repo as the context (`../Jymalaya`, so this repo's
  `.dockerignore` applies) and the container folder as the named extra context
  `c1` (the entrypoint), and runs it (project `jymalaya-c1-single`, which must differ from the deploy
  stack's `jymalaya-c1` or Compose merges the two; both use the container name
  `Jymalaya-C1`, so only one runs at a time; the volume is pinned as
  `jymalaya-c1-data`). `build.cmd` there builds it and saves the `docker save`
  tar next to itself with a copy of `.env.example` (never `.env`: the package
  ships blank keys, the athlete's call); `run.cmd` / `run.sh` stop without a
  `.env`, load the tar when the image is missing and `up -d --no-build`;
  `README.txt` is the other computer's guide. The host port defaults to **8090** (2026-11-07):
  on 8080 the athlete reached the dev backend from `start.cmd`, which serves no
  SPA and answers `/` with `{"detail":"Not Found"}`; `build.cmd` rewrites
  `APP_PORT` to 8090 in the copied `.env.example`. The one compose file serves both cases because it has `build:`
  AND `image: jymalaya-c1:latest` — in the package there is no source, and `up`
  runs the loaded image. Things easy to re-break:
  (1) **One volume at `/data`**, reached through symlinks baked into the image
  so no app code knows about it: `/app/data -> /data/app`,
  `/app/config/athlete.local.yaml -> /data/config/athlete.local.yaml` (a
  dangling link until the first Settings save — `_load_yaml`'s `exists()` is
  False, so it falls back to `athlete.yaml`, and `open(..., "w")` creates the
  target), `~/.tricoach -> /data/garmin`. The target directories must exist
  before the app starts: the image creates them (a new named volume is seeded
  from it, ownership included) and the entrypoint does again (bind mounts are
  not seeded).
  (2) **The token lives in a file, `INFLUXDB_TOKEN_FILE`**, which wins over
  `INFLUXDB_TOKEN` in `app/config.py`. An export from the entrypoint alone is
  not enough: `docker exec` processes (scripts) never see it. The file winning
  also means a copied dev `.env` cannot point the app at the wrong token; the
  compose file pins `INFLUXDB_URL`/`ORG`/`BUCKET` over `.env` for the same
  reason. The token is written before `influx setup` runs, so a crash in between
  re-provisions instead of losing the token.
  (3) **No athlete data in the image**: `.dockerignore` keeps out `.env`,
  `data/`, `config/*.local.yaml`. The tar sits outside the build context.
  (4) **Line endings**: the `.cmd` files must be CRLF (cmd.exe misreads labels
  in LF batch files) and `entrypoint.sh` LF; the Dockerfile strips CR from the
  entrypoint anyway. The container folder is not under git, so no
  `.gitattributes` guards them there.
  Verified end to end on 2026-09-22: a package loaded into a source-free folder
  with the local image deleted came up healthy in about 5 s; profile,
  instructions and events survived restarts; `docker stop` exits 0 in under 2 s;
  killing influxd took the container down with exit 1. Verified again on
  2026-11-07 for the rebuilt files: same results on InfluxDB 2.9.1, and the
  restart policy brought the container back after influxd was killed.

## Reconciliations (old BrickPlan CLI → Jymalaya AI Coach)

These resolve conflicts between the retired CLI design and the PRD. Follow the right column.

- **Output**: ~~TrainingPeaks CSV + static HTML report~~ → REST API + live dashboard.
- **Sport vocabulary**: ~~`Bike`/`Run`/`Swim`/`Walk`/`Other`~~ → lowercase
  `swim`/`bike`/`run`, plus **`strength`** as a 4th discipline (PRD + design both show it).
- **Bricks**: ~~kept as one entry~~ → **always split** into a `bike` entry and a `run`
  entry sharing a `session_id`, ordered by timestamp (PRD §5.1, §7.1).
- **Layout**: ~~`src/` flat modules~~ → the PRD `app/` package layout (below).
- **Config**: ~~`config/profile.json`~~ → `config/athlete.yaml` (PRD §7.2),
  mirrored into InfluxDB. The weekly schedule was `config/schedule.yaml`
  (PRD §7.3) until 2026-07-27; it is now athlete-edited JSON — see below.

## Project structure (as built — see PRD §11)

```
main.py                  # FastAPI entry point (uvicorn main:app --port 8080)
Dockerfile               # two-stage: Vite build -> python:3.12-slim runtime
docker-compose.yml       # Phase 1 dev infra: influxdb only (used by start.cmd)
docker-compose.deploy.yml# the portable Jymalaya-C1 stack: app + influxdb
                         # (the single container's build files: ..\Jymalaya-Container)
start.cmd                # Windows: launch backend + frontend dev server
config/athlete.yaml      # FTP, thresholds, LTHR, weight, birthday (age derived)
scripts/                 # one-off backfills/migrations; dry run unless --apply
app/
  config.py, models.py   # env/YAML loading; Pydantic API + plan-edit models
  sync_service.py        # sync pipeline orchestration + live SYNC_PHASE state
  recommend_service.py   # plan generation -> data/latest_recommendation.json
  plan_editor.py         # coach tool backend: mutate the cached plan (owns LATEST_FILE)
  plan_requests.py       # days the athlete asked to change, kept across a re-plan
  settings_editor.py     # coach-approved settings changes + the shared profile/instructions saves
  season/                # the season plan: config, models, store, builder, adapt,
                         # checkin (fitness), summary, service (data/season_plan.json)
  analysis_service.py    # post-workout analysis: stored detail, derive, ask Claude, cache
  workout_detail.py      # a workout's stored samples/laps/planned workout, for the analysis
  events_service.py      # events (called goals in the PRD) CRUD over the `goals` measurement;
                         # active_event/last_race skip calendar-only (participating=false) events
  instructions.py        # athlete's standing coach instructions (data/coach_instructions.txt)
  schedule_store.py      # the athlete's training week (data/schedule.json)
  garmin/   personal_api.py # Personal API: workouts, FIT samples, HRV, resting HR, avatar
            fit.py       # FIT file -> 5 s samples, laps, HR-zone repair (no GPS)
            client.py    # library login (token cache, MFA_STATE + file handoff): VO2max only
            parser.py    # activity parser + brick splitter
  influx/   client.py, queries.py
  metrics/  calculator.py
            ngp.py       # Normalized Graded Pace for run rTSS
            analysis.py  # decoupling, half splits, power zones, stream NP
  weather/  openmeteo.py
  intervals/ client.py   # Send to device: upsert a workout on the intervals.icu calendar
            workout.py   # plan session -> intervals.icu workout text (pure)
  ai/       claude.py    # plan generation + coach tool-use loop (COACH_TOOLS)
            prompt_builder.py
  routers/  sync, events, athlete, schedule, weather, recommend, coach, analysis,
            data, device, season, health
prompts/recommendation_system.txt        # rules; sent with the JSON template appended
prompts/recommendation_plan_format.json  # the plan's JSON output template
prompts/<Distance>_Personalization.txt   # rule 9 (periodization), one per race distance
prompts/training_zones.txt               # zone bands + strict pace rules, shared by every Claude call
prompts/analysis_system.txt              # post-workout analysis rules
prompts/analysis_format.json             # its JSON output template
data/latest_recommendation.json   # cached last plan (gitignored)
data/plan_requests.json           # athlete-requested plan days (gitignored)
data/season_plan.json             # the season plan (gitignored)
data/analyses/<activity_id>.json  # cached workout analyses (gitignored)
tests/                   # pytest; fakes only, no network/credentials
frontend/                # React + Vite implementation of the Option A dashboard
```

## Build order (Phase 1 — see PRD §12) — all done

1. **M1** — Garmin sync → InfluxDB + brick splitter (dedup by `activity_id`).
2. **M2** — TSS per discipline + CTL/ATL/TSB (`app/metrics/`); `daily_load`. Ramp
   rate and the time-in-zone split were also built here, then removed on
   2026-08-12: the dashboard derives both client-side (`weekBuckets` and
   `hrZones` in `frontend/src/derive.ts`), so the backend copies had no caller.
3. **M3** — ~~Grafana dashboards (training load + last week) via provisioning~~
   (built, then dropped — Grafana removed from the project on 2026-07-26).
4. **M4** — Goals CRUD + Open-Meteo weather.
5. **M5** — ~~Goals Grafana dashboard~~ (dropped with M3 on 2026-07-26).
6. **M6** — Schedule config + Claude recommendation engine; auto-run after sync.
7. **M7** — API polish + CORS for the frontend.
8. **M8** — Integration testing + error handling.

Also built beyond the PRD milestones: the Option A frontend; dashboard MFA code
entry (`POST /api/sync/mfa` + modal); live sync-phase in the Sync button; coach chat
plan-editing tools with attach-a-workout; athlete profile settings UI; full
data reset (`DELETE /api/data`); the running/cycling VO2max panel (56 daily
points); post-workout analysis; the 60-day Training Days grid; the Phase 2
container stack; plan changes that survive a re-sync (athlete-requested
days); Send to device through intervals.icu; the event Participating toggle;
the HRV / Resting HR trend cards; the Season plan (event and fitness);
dragging a plan card between days; the workout stream graphs; settings
changed from the coach chat; the Garmin Training Effect dials; the
workout RPE and feel tiles; and the activity class picking season-plan
defaults and capping quality sessions.

## Style rules (agent guidance)

- **Never add icons or emoji.** Not in UI strings, coach/chat text, docs,
  README, commit messages, or log lines — use plain words instead ("plan
  updated", "attach to chat", "Coach"). Decorative emoji already in the code
  are being phased out: remove them when touching that code. Two exceptions
  that stay: the goal icon picker in `EventModal` (a product feature — the
  athlete picks their goal's emoji) and plain typographic control glyphs
  already in the design (the close-button cross, the settings gear, and the
  up/down arrow that folds the Season plan panel -- the athlete asked for it
  on 2026-10-25 in place of "Hide"/"Show"), and the two-bubble Ask coach
  mark (`ChatIcon.tsx`, 2026-11-14, the athlete's pick) on the session and
  workout modals and the Predicted Week cards, tooltip and `aria-label`
  "Ask coach". The Season plan's small "Ask coach" buttons stay words.

## Guardrails the AI plan must never violate (PRD §7.7)

80/20 intensity distribution; **never schedule on a `rest: true` day — the one
guardrail enforced in code, not just the prompt**: `_enforce_rest_days` in
`app/recommend_service.py` clears any session Claude puts on a rest day and adds
a `warning_flags` entry. **The day before the race is the second (2026-11-09,
the athlete's call)**: prompt rule 31 leaves the eve of `primary_goal.race_date`
empty whatever the distance, outranking `athlete_custom_instructions`, and
`_clear_pre_race_day` clears and flags any session there (an athlete-requested
day is kept, as for rest days; `is_rest_day` is left as generated).
`tests/test_pre_race_day.py` pins it. The payload makes the rest-day rule hard to miss —
`_schedule_for_claude` drops the redundant `available` so `rest` is the only
availability signal, and every `plan_days` entry carries its own `is_rest_day`;
never exceed a
day's `max_sessions`; the week limits `max_training_days_per_week` and
`max_consecutive_training_days` (derived from the athlete's week in Settings —
the prompt reads them from the payload, never hardcoded); periodization phase derived from
weeks-to-race (never hardcoded) -- or, with a season plan, given per week by it (rule 25);
each season week's TSS budget (rule 26; checked in code by `_check_season_target`, flagged,
never rewritten); a 15% lighter 7 days after six flat or only-rising weeks of TSS
(rule 32; checked by `_check_load_reduction`, flagged, never rewritten);
a sub event is that day's race session with an easy day before it, never a new
goal (rule 33, prompt only);
TSB-aware recovery; bricks only on `brick_allowed_days`.
Claude reasons freely within these bounds but must never breach the hard numeric/schedule
limits. Plan output is structured JSON only (PRD §7.7 output format).

## Open questions (PRD §14 + new)

- Weather for race dates >7 days: Open-Meteo historical climate avg, or skip? (open)
- Minimum "ready-to-train" CTL threshold? (open)
- ~~Schedule config editable via `PUT /schedule` or YAML-only?~~ Resolved
  2026-07-27: fully UI-editable via `GET/PUT /schedule` → `data/schedule.json`.
  `config/schedule.yaml` is gone (a conscious divergence from PRD §7.3);
  `DEFAULT_SCHEDULE` in `app/schedule_store.py` is the factory default. The
  one-shot migration off the old YAML + overrides pair was dropped on
  2026-08-12, once no install predated the move. Only the
  per-day fields are stored/edited — the week constraints
  (`max_training_days_per_week`, `max_consecutive_training_days`,
  `long_ride_day`/`long_run_day`, `brick_allowed_days`) are computed from the
  days (`_derive_constraints`): the limits fall out of the rest days, the long
  ride/run land on a `long` day preferring that discipline, and
  `brick_allowed_days` are exactly the days typed `brick` — a session type
  that requires at least two preferred disciplines (Pydantic 422 / blocked in
  the UI), forces `max_sessions >= 2`, and makes the plan schedule those
  disciplines back-to-back on that day (rule 13 in the system prompt).
- ~~Frontend as a separate repo or a `frontend/` dir?~~ Resolved: `frontend/` in this repo.

## Known caveats to preserve in user-facing docs/output

- The Garmin Personal API key expires (30 days to a year, Garmin emails about a
  week ahead); the dashboard then asks for a new one. Workouts sync with it alone.
- VO2max still uses Garmin's unofficial login, which can break or hit
  MFA/bot-detection without warning. MFA codes are entered in the dashboard modal
  (or `~/.tricoach/mfa_code` / `GARMIN_MFA_INTERACTIVE=1` outside it).
- The Anthropic API key is the user's own, set in `.env`.
- InfluxDB must be running (locally or via Docker Compose) for the app to work;
  only Garmin sync and Claude calls require internet.
