-- Daily calendar required by dbt's semantic layer.
select cast(date_day as date) as date_day
from generate_series(date '2000-01-01', date '2100-12-31', interval 1 day) as days(date_day)
