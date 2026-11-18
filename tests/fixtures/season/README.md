# Season plan contract samples

Corrected copies of the samples in `assets/Season plan handoff.zip`. They are
the shared contract between the backend and the dashboard: if code or design
needs a field that is not here, change the sample first.

- `season_plan.event.json` -- the handoff's Eilat olympic plan (22 weeks,
  standard ramp, 9 h cap, baseline 290 TSS/week), as the builder makes it on
  the day it is created. `tests/test_season_builder.py` rebuilds it and requires
  an exact match. It differs from the handoff sample on purpose:
  - phases follow the prompt files' weeks-out table (7 base, 7 build, 6 peak,
    1 taper, race week) rather than the handoff's 40/40/20 percent split;
  - the 9 h cap holds the peak at 495 TSS/week (the handoff sample showed 504,
    above its own cap);
  - it is the plan at creation, with no actuals, pins or sync adjustments --
    those arrive with the adaptation milestone.

- `checkin.pending.json` -- the handoff's pending check-in (closing week 353
  target, 270 done, 82 still planned, TSB -8.9, three loading weeks), as the
  engine builds it. `tests/test_season_fitness.py` requires an exact match.
  Against the handoff it differs in three places: increase is 6.9 h, not 6.8
  (377 TSS at 55 TSS/h); the reason is worded "put it off once"; and it
  carries `return_week` (false here), which a layoff sets.

The handoff's fitness plan sample is not reproduced: its recovery week (231)
disagreed with its own check-in (230), and its projected weeks did not follow
the rule it states (they restarted from 329 instead of stepping up from the
last loading week). The projection and the lifecycle are pinned by the
fitness tests instead.
