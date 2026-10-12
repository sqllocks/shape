# Proposal: calendar-aware behavior modules

Status: proposed 2026-10-12 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`.

## 1. The problem

Real processes cluster around calendar dates. Sales deals close in the last days of a quarter, subscriptions renew on the first of the month, invoices and claims are batched at period end, and work pauses at weekends and holidays. Behavior modules cannot express any of this:

- Delays (`plugins/shape-behavior/src/shape_behavior/dist.py:12`) are `exact`, `constant`, `uniform`, `gaussian`, `normal`, `exponential`, `lognormal` and `bernoulli` durations added to the virtual clock. None knows the date it lands on.
- Guard conditions (`conditions.py`) can compare the absolute time with a fixed `date`, but cannot ask "is it the last week of a quarter" or "is it a weekday". A `date` guard is polled (default every 7 days), so it cannot hit a specific day exactly.
- The engine already has a calendar model for generated timestamps (`src/shape/builtins/calendars/`, `docs/GENERATION_CALENDARS.md`): holidays, paydays, events, `month_end` and `quarter_end` lifts, trends. Behavior modules cannot use it.

So a module can produce plausible durations but not plausible dates. Every simulated pipeline, renewal or billing cycle comes out spread evenly over the month and the week.

## 2. Proposed design

Three additions, all opt-in:

**a. Period-aligned delay kinds.** New duration kinds that wait until a calendar boundary instead of for a length of time:
- `{"kind": "period_end", "period": "month"|"quarter"|"year", "offset_days": 0, "skip_if_within_days": 0}`: the next end of the period, minus `offset_days`. `skip_if_within_days` moves to the following period when the boundary is too close (a deal entering "negotiation" two days before quarter end closes next quarter).
- `{"kind": "period_start", ...}` with the same keys.
- `{"kind": "next_weekday", "weekdays": ["mon", ...], "time": "09:00"}`: the next listed weekday at that time.

**b. Calendar-weighted delays.** An optional `calendar` on a delay state reshapes where the sampled delay lands: the landing day is accepted with probability proportional to the calendar's daily factor (thinning, deterministic per seed), redrawn up to a fixed bound, then shifted to the nearest day with a positive factor. A factor of 0 (a closed day) is never landed on. This turns an exponential "time in stage" into one that bunches at quarter end when the calendar has a `quarter_end` lift.

```json
"negotiation": {"type": "delay",
                "delay": {"kind": "exponential", "mean": 30, "unit": "days"},
                "calendar": {"quarter_end": {"lift": 3.0, "days": 10}, "calendars": ["us_federal"]},
                "transitions": [...]}
```

The calendar spec is the one generation already uses, so one calendar file can drive both.

**c. Calendar conditions in guards.** A new condition leaf `{"type": "calendar", "field": F, "op": OP, "value": V}` with `F` one of `weekday`, `day_of_month`, `month`, `quarter`, `days_to_period_end` (with `period`), `is_holiday` (with a calendar). Each has an exact "ready" time, like `age` conditions today, so the simulator jumps to the right moment instead of polling.

Time zone: the virtual clock is UTC microseconds and stays so; calendar fields are evaluated in UTC unless a module-level `time_zone` is given (IANA name, through the standard library's `zoneinfo`; core already depends on `tzdata` on Windows, where the system has no time zone database).

## 3. Compatibility

- **Spell the delay change as new `kind` values, not as extra keys on old ones.** An unknown `kind` is a hard `ModuleError` in older readers (`dist.check`), so a module that needs the new behavior fails loudly on an old version. An unknown key on a state is silently ignored (`model.py:301`), so `calendar` on a delay state (item b) would be dropped silently by an old reader: the module format must change for it. Proposed: `format` `shape-behavior/2` for modules that use `calendar` or calendar conditions, with `shape-behavior/1` modules unchanged and still accepted.
- `Module.digest()` hashes the whole document, so resume correctly refuses a module whose calendar changed.
- Plugin boundary: the behavior plugin may use only core's public API. The stable `shape.calendars` protocol has `name` and `lift(start, end)` only; building a calendar from a spec goes through `with_spec`, which is not a protocol member. Item 6 below makes the spec-to-calendar step part of the stable surface instead of reaching into `shape.builtins`.

## 4. Wanted (ready to paste into the issue)

1. Duration kinds `period_end`, `period_start` and `next_weekday` in `dist.py`, valid only for delay states, with validation messages naming the module and state.
2. An optional `calendar` on delay states: thinning by the calendar's daily factors, deterministic per seed, bounded redraws, never landing on a zero-factor day.
3. A `calendar` condition leaf with the fields in section 2c, each with an exact ready time (no polling).
4. An optional module-level `time_zone`; UTC by default.
5. Module format `shape-behavior/2`, required when items 2 to 4 are used; version 1 modules unchanged and byte-identical in output.
6. A stable way for plugins to build a calendar from a spec: either `with_spec` added to the `shape.calendars` protocol (plugin API change, `docs/plugins/stability.md`) or a public function listed in `docs/API_STABILITY.md`. The lead picks one.
7. The example `subscription` module gains a month-start renewal using item 1, and a new example module shows quarter-end bunching with item 2.
8. Documentation in `docs/plugins/behavior.md` with each new kind, the condition leaf and the format change.

Acceptance beyond section 13's common items: with a `quarter_end` lift the share of transitions landing in the lifted days exceeds the share without it by the expected ratio within a stated tolerance over a large population; no landing day has a calendar factor of 0; the same seed gives byte-identical events; a version 1 module produces byte-identical output before and after the change; an older reader rejects a version 2 module.

## 5. Open questions for the lead
1. Whether item 2's thinning bound and fallback (shift to the nearest open day) should be configurable.
2. Whether fiscal years that do not start in January need a `fiscal_year_start` month in phase 1 (sales quarters often differ from calendar quarters).
