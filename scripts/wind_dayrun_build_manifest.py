"""Build a fixed flight manifest for the wind-resolution day-run comparison.

Samples 1% (seed 42, matching this project's existing sampling convention --
see Paper_1_data/src/13d_aeic_run_1pct_v2.slurm.sh) of the real 2025-11-21
flight schedule from the project's OAG database, then date-shifts each
flight's departure/arrival onto 2022-11-21 (same time-of-day, duration
preserved) so it lines up with the ERA5/MERRA2 wind data already validated
in the Phase 1 wind tests.

Materializing the sample once (rather than re-querying per scenario)
guarantees the wind-off/ERA5/MERRA2 scenario runs all see exactly the same
936-ish flights.
"""

import dataclasses
from datetime import date

import pandas as pd

from AEIC.missions import Database, Query

DB_PATH = '/net/d16/data/shreya22/Paper_1/aeic_input/2025/oag_2025_cirium.sqlite'
SAMPLE_DATE = date(2025, 11, 21)
SHIFT_TO_DATE = date(2022, 11, 21)
SEED = 42
SAMPLE_FRACTION = 0.01
OUT_PATH = '/net/d16/data/shreya22/Paper_1/wind_phase1_output/dayrun_manifest.csv'


def shift_date(ts: pd.Timestamp, new_date: date) -> pd.Timestamp:
    return ts.replace(year=new_date.year, month=new_date.month, day=new_date.day)


with Database(DB_PATH) as db:
    db.set_random_seed(SEED)
    query = Query(sample=SAMPLE_FRACTION, start_date=SAMPLE_DATE, end_date=SAMPLE_DATE)
    missions = list(db(query))

print(f'Sampled {len(missions)} flights on {SAMPLE_DATE}')

rows = []
for m in missions:
    departure_offset = m.departure - pd.Timestamp(SAMPLE_DATE, tz='UTC')
    duration = m.arrival - m.departure

    new_departure = pd.Timestamp(SHIFT_TO_DATE, tz='UTC') + departure_offset
    new_arrival = new_departure + duration

    shifted = dataclasses.replace(m, departure=new_departure, arrival=new_arrival)

    rows.append(
        {
            'flight_id': shifted.flight_id,
            'origin': shifted.origin,
            'destination': shifted.destination,
            'aircraft_type': shifted.aircraft_type,
            'load_factor': shifted.load_factor,
            'performance_model_key': shifted.performance_model_key,
            'departure': shifted.departure.isoformat(),
            'arrival': shifted.arrival.isoformat(),
            'carrier': shifted.carrier,
            'flight_number': shifted.flight_number,
            'service_type': shifted.service_type,
            'engine_type': shifted.engine_type,
            'seat_capacity': shifted.seat_capacity,
            'flight_level': shifted.flight_level,
            'origin_country': shifted.origin_country,
            'destination_country': shifted.destination_country,
        }
    )

df = pd.DataFrame(rows)
df.to_csv(OUT_PATH, index=False)
print(f'Saved manifest ({len(df)} flights) to {OUT_PATH}')

# Sanity check: duration preserved exactly across the date shift.
orig_durations = pd.Series(
    [(m.arrival - m.departure).total_seconds() for m in missions]
)
new_durations = (
    pd.to_datetime(df['arrival']) - pd.to_datetime(df['departure'])
).dt.total_seconds()
assert (orig_durations.values == new_durations.values).all(), (
    'duration mismatch after date shift!'
)
print('Duration-preservation check passed.')
