# Proposal: time-zone-aware time-of-day profiles

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`.

The "within working hours" window for behavior delays, also asked for, is added to `calendar-aware-behavior.md` instead of here (its item 2b and Wanted item 9), because it is a key on the delay states that proposal already changes. It uses the same `sessions` notation as section 2b below.

## 1. The problem

When things happen during the day depends on where and on which day. Card payments peak at lunch and in the evening local time; a support desk is busy from 9 to 17 on weekdays and quiet at weekends; a stock exchange trades in sessions with a lunch break; a holiday looks like a Sunday. A dataset with customers in several time zones stored in UTC shows each zone's peak at a different UTC hour.

The `temporal` strategy cannot express this:

- **One hour profile for every day.** `profiles.hour_of_day` (`src/shape/builtins/strategies/temporal.py:266`, parsed by `_hours` at `temporal.py:294`) is 24 weights or Gaussian peaks (`docs/GENERATION_STRATEGIES.md:390`). The kernel draws the day and then the hour independently (`temporal.py:283` to `291`; `src/shape/kernel/reference/gen.py:485`), so a Saturday has the same hours as a Monday.
- **No time zone.** The column is `timestamp[us]` with no zone; a bound with an offset is converted to that instant in UTC and the offset dropped (`temporal.py:33`, `GENERATION_STRATEGIES.md:371`). An hour profile is therefore read as UTC hours. A profile of "9 to 17 in New York" has to be shifted by hand, and is then wrong for half the year because of daylight saving time.
- **Hour resolution only.** The time of day is a whole hour drawn from 24 weights, then a uniform instant in it (`GENERATION_STRATEGIES.md:390`). A gap that does not fall on an hour boundary (a lunch break from 11:30 to 12:30, a market opening at 09:30) cannot be expressed.
- **No holidays.** The calendars (`docs/GENERATION_CALENDARS.md`) know holidays (`us_federal`, `us_retail`) and give a factor per day. `temporal` does not use calendars at all (`GENERATION_STRATEGIES.md:396`: "Calendars, paydays and trends (`shape.calendars`) are separate"), and the one function that applies a calendar to timestamps, `shape.generation.temporal.sample_timestamps` (`src/shape/generation/temporal.py:85`), has no caller in `src` or `plugins`.
- **Silently ignored.** `hour_of_day` applies only with `pattern: "seasonal"`; any other pattern returns uniform times without a warning (`temporal.py:255`).

What exists and is reused: the zone database (`zoneinfo`, with `tzdata` a core dependency on Windows, `pyproject.toml:26`), the DST vocabulary of the time-series checks (`dst {time_zone, fall_back: repeat|once}`, `src/shape/quality/timeseries.py:133`), and the calendar spec with `holidays(start, end)` (`GENERATION_CALENDARS.md:49`, `src/shape/builtins/calendars/composite.py:85`).

## 2. Proposed design

All new keys are on `temporal` with `pattern: "seasonal"`; without them nothing changes.

### a. Time zone

`"time_zone": "America/New_York"` (an IANA name, resolved with `zoneinfo`; an unknown name is an error naming the column and the missing `tzdata` package where relevant).

- Day selection (month, weekday, calendar) and time of day are in local time. Each local value is converted to the UTC instant.
- **DST.** A local time that does not exist (spring forward) moves forward by the gap. A local time that occurs twice (fall back) takes the first occurrence, or both with equal chance with `"fall_back": "both"` (the `repeat` of the time-series check).
- **Output.** `"output_zone": "utc"` (default: today's naive UTC `timestamp[us]`), `"local"` (naive local wall-clock time, for sources that store local time), or `"aware"` (`timestamp[us, tz=<zone>]`).
- `"time_zone_from": "customer_tz"` reads the zone from a column of the same row (a text column of IANA names), so one table holds customers in several zones, each with its local profile. The rows are grouped by zone and each group is drawn as above; row addressing is kept because the zone is a value of the same row.

### b. Profiles per day type, and sessions

```json
"profiles": {
  "month": {...}, "day_of_week": {...},
  "time_of_day": {
    "weekday": {"sessions": [{"start": "09:30", "end": "11:30", "weight": 1.2},
                             {"start": "12:30", "end": "16:00", "weight": 1.0}]},
    "weekend": {"closed": true},
    "holiday": {"closed": true}
  }
},
"calendar": {"calendars": ["us_federal"]},
"time_zone": "America/New_York"
```

- `time_of_day` maps a day type to a profile. Day types: `weekday`, `weekend`, `holiday`, and the weekday names (`mon` to `sun`), the most specific winning (`holiday` over a weekday name over `weekday`/`weekend`). A day type left out uses `default`, which is the existing `hour_of_day` when given.
- A profile is `hour_of_day` as today (24 weights or peaks), or `sessions`: intervals in local `HH:MM` with relative weights, uniform inside each interval, nothing outside. A weight per interval gives the opening rush. Gaps between sessions (the lunch break) are never landed on.
- `{"closed": true}` gives the day type a factor of 0: no row lands on such a day. A `closed` day is removed before day weights are normalized, as a calendar factor of 0 is today.
- `calendar` is the calendar spec of `GENERATION_CALENDARS.md` (`calendar_from_spec`). Its holidays define the `holiday` day type, and its daily factors (events, paydays, month end, trend) multiply the day weights, which finally connects calendars to the strategy that generates timestamps.

### c. Resolution and the kernel

The kernel draws a day, then an hour from 24 weights, then an instant in the hour (`gen.py:485`). Sessions need finer slots and a profile per day type. Proposed: the kernel takes a per-day profile index (computed from the day type of each local day) and a matrix of slot weights, with slots of one minute when any profile has sessions and of one hour otherwise. When no new key is used, the existing call is made unchanged, so existing output stays byte-identical. The word count per row stays at five.

## 3. Compatibility

- **New keys only.** Every key above is new. An existing spec draws exactly as today, with the same words of the same stream, so output is byte-identical for every seed, and `temporal`'s `generator_version` does not change for it. Specs that use the new keys run the new code path, which starts as version 2 of `temporal` so a later change to it can be pinned (`docs/GENERATION_STABILITY.md`).
- **Older readers.** The generation-spec JSON Schema for `temporal` is closed (`src/shape/schemas/generation-spec-v1.schema.json:2719`), so `time_zone`, `calendar` and `output_zone` fail loudly there. But `profiles` is untyped in that schema (`{}`), so an older reader would accept `profiles.time_of_day` and ignore it silently, generating uniform times. Proposed: `profiles` gets a closed schema in the same change, and a spec that uses `time_of_day` requires `time_zone` (even `"UTC"`), which an older reader refuses.
- **Profiles.** Phase 1 and 2 do not change the profile format. Phase 3 (profiling hours per day type and zone) adds keys to `temporal_profile` output and needs a compatibility test.
- **Silent fallback.** Making `hour_of_day` with a non-seasonal pattern an error (instead of uniform) would change behavior for existing specs. Proposed instead: a warning naming the column, with the error left to the lead (open question 2).

## 4. Wanted (ready to paste into the issue)

Phase 1, time zone:
1. `temporal` `time_zone` (IANA) and `time_zone_from` (a same-row column): day and time of day in local time, converted to UTC; DST rules of section 2a with `fall_back` `first` (default) or `both`.
2. `output_zone`: `utc` (default), `local`, `aware`.
3. An unknown zone, or no time zone database, is an error naming the column (and `tzdata` on systems without one).

Phase 2, day types, sessions and calendars:
4. `profiles.time_of_day` keyed by day type (`weekday`, `weekend`, `holiday`, `mon` to `sun`, `default`), each an `hour_of_day` profile, `sessions` (`start`, `end`, `weight`, minute resolution) or `{"closed": true}`.
5. `calendar` on `temporal`: holidays define the `holiday` day type; daily factors multiply day weights; a factor of 0 is never landed on.
6. The kernel change of section 2c in the native kernel and its twin, with the unchanged call kept for specs without the new keys.
7. A closed JSON Schema for `profiles`; `time_of_day` requires `time_zone`.
8. A warning for `hour_of_day` (or `time_of_day`) with a pattern that ignores it.

Phase 3, profiling:
9. `temporal_profile` reports the hour histogram per day type, and in a given zone (`time_zone` argument), so `profile` then `generate --from` keeps weekday and weekend hours apart.
10. Documentation in `docs/GENERATION_STRATEGIES.md` (`temporal`) and `docs/GENERATION_CALENDARS.md` (calendars on the strategy), with a market-session example and a support-desk example.

Acceptance beyond section 13's common items: in local time, no value falls in a gap between sessions or on a closed day type or a holiday; the local hour histogram per day type matches its profile within a total variation distance of 0.01 over a large sample; around both DST transitions of a zone the local profile is kept and no value lies in the skipped hour; `time_zone: "UTC"` with an `hour_of_day` profile gives the same values as today's spec without it; existing specs give byte-identical output before and after the change, in both kernel modes.

## 5. Open questions for the lead
1. Whether exchange calendars (trading holidays that differ from public holidays) belong in core next to `us_federal`, or are `shape.calendars` plugins.
2. Whether `hour_of_day` with a non-seasonal pattern should become an error in a later release (a behavior change for existing specs) or stay a warning.
3. Whether `time_zone_from` should accept fixed offsets (`+05:30`) as well as IANA names.
