"""
The season plan: a persistent week-by-week plan owned by code.

Each week gets a TSS target, a phase and a kind; Claude's 7-day plan fills the
sessions inside that target. Pure logic lives in builder.py (and later adapt.py
and checkin.py) so it is table-testable; store.py is the only file I/O.
"""
