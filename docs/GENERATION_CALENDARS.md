# Calendars and timestamps

How generated timestamps get their month, day-of-week and hour profiles, their holidays, paydays
and trends, and how a profile of real timestamps is read back. Calendars are code, not data: they
work offline and give the same answer everywhere (D-11).

## Rules

`shape.builtins.calendars.rules` is the date rule engine. A rule maps a year to a date:

| Rule | Spec | Example |
|---|---|---|
| fixed date | `{"month": 12, "day": 25}` (`from_year`, `until_year`) | Christmas |
| nth weekday | `{"month": 11, "weekday": "thu", "n": 4}` (`n: -1` for the last) | Thanksgiving |
| Easter | `{"easter": 0}` (days from Easter Sunday, Gregorian computus) | Good Friday: `-2` |
| relative | `{"after": <rule>, "days": 1}` | Black Friday |
| observed | `{"observed": <rule>}` | a Saturday holiday on the Friday, a Sunday one on the Monday |

## Effects

Every effect gives one multiplicative factor per day: 1.0 is no change, above 1.0 a lift, below
1.0 a dip, 0 a closed day. A factor depends on the date only, so any sub-range of a calendar is the
slice of a longer one. Effects combine by multiplication.

* **Event** (`{"name", "lift", "date" / "dates" / "rule", "ramp_up_days", "decay_days", "ramp",
  "decay"}`): on its date the factor is exactly `lift`. Over the `ramp_up_days` before and the
  `decay_days` after, the lift fades: at distance `j` of `S` days the weight of the lift is
  `1 - j / (S + 1)` (`linear`) or `exp(-3 j / (S + 1))` (`exponential`), and the day's factor is
  `1 + (lift - 1) * weight`. Custom events are events with your own dates or rule.
* **Payday** (`{"kind": "semimonthly" | "monthly" | "biweekly", "days": [1, 15], "anchor":
  "2024-01-05", "adjust": "previous_business_day" | "none", "lift", "ramp_up_days",
  "decay_days"}`): semimonthly is the 1st and 15th, monthly takes `days` (`-1` is the last day),
  biweekly is every 14 days from the anchor. A weekend payday moves to the Friday before.
* **Month end / quarter end** (`"month_end": {"lift": 1.2, "days": 3}`, `"quarter_end": {...}`):
  the last `days` days of every month, or of March, June, September and December.
* **Trend** (`{"annual_growth": 0.1, "origin": "2020-01-01", "steps": [{"date", "factor"}],
  "ramps": [{"start", "end", "factor"}]}`): compound growth from `origin`; a step multiplies the
  level from its date on; a ramp rises linearly from 1 at `start` to `factor` at `end` and stays.

## Calendars

`us_federal` (the eleven federal holidays on their observed days; Juneteenth from 2021) and
`us_retail` (New Year, Valentine's Day, Easter, Mother's and Father's Day, Thanksgiving, Black
Friday, Cyber Monday, Christmas Eve and Day) ship in core. Both take `holiday_lift` (all holidays,
default 1.0), `lifts` (per holiday name, which must be one of the calendar's), `ramp_up_days`,
`decay_days`, `ramp` and `decay`, and `holidays(start, end)` lists the dates. Calendars for other
countries are `shape.calendars` plugins.

`composite` combines everything. `calendar_from_spec(spec)` (or
`default_host().get("shape.calendars", "composite").with_spec(spec)`) takes any of:

```json
{
  "calendars": ["us_federal", {"name": "us_retail", "lifts": {"black_friday": 3.0}}],
  "events": [{"name": "launch", "date": "2024-05-01", "lift": 4.0, "decay_days": 3}],
  "payday": {"kind": "semimonthly", "lift": 1.2},
  "month_end": {"lift": 1.1, "days": 3},
  "quarter_end": {"lift": 1.3, "days": 2},
  "trend": {"annual_growth": 0.05, "steps": [{"date": "2024-07-01", "factor": 0.8}]}
}
```

## Generating timestamps

`shape.generation.temporal.sample_timestamps(stream, row_start, n_rows, start, end, month=...,
day_of_week=..., hour=... | hour_peaks=(peaks, std), calendar=..., whole_seconds=False)` returns
`timestamp[us]` values between the two dates (inclusive). It is row addressed (a row's timestamp
does not depend on the chunking). A day's weight is `month[m] * day_of_week[d]` divided by the number
of days in the range with that month and weekday (so each pair carries its own weight however many
days it has) times the calendar's factor; then an hour from `hour` (24 weights) or from Gaussian
peaks, then a uniform instant inside the hour. Weights are relative and default to uniform.

## Reading a profile back

`shape.profile.temporal` is the inverse:

* `temporal_profile(column, calendar=None)`: `month` (12 shares, January first), `day_of_week`
  (7, Monday first), `hour` (24, `None` for dates without a time) and, with a rule calendar,
  `holiday_lifts`. Feed `month`, `day_of_week` and `hour` back to `sample_timestamps` and the
  profiles of the result match to a total variation distance of 0.01.
* `holiday_lifts(column, holidays, exclude_days=3)`: the rows on each holiday divided by the rows
  expected on an ordinary day of the same month and weekday (the mean over days further than
  `exclude_days` from any holiday), pooled over the years. Recovers configured lifts to within 5%.
* `tail_index(values)`: the Hill estimator of the tail index of the largest 5% of the positive
  values.
