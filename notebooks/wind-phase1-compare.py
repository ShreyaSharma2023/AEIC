# Phase 1 wind testing: run a JFK<->LAX flight pair through the trajectory
# builder twice each (wind on / wind off) and compare fuel burn, duration,
# and ground speed. Validates that AEIC.weather.Weather works end-to-end on
# real missions and behaves physically (eastbound tailwind, westbound
# headwind from the November jet stream -- see wind-phase1-inspect.py for
# the wind field itself).
#
# Wind data: read directly from /net/d16/data/travnik/era5/wind_and_temperature/
# (Marek T's raw ERA5 source, traced via his src/weather/directories.py
# WEATHER_DATA_PATH) -- no local copy. Global, hourly, full pressure levels
# 100-1000 hPa, Nov 2022 - Nov 2023. His filename pattern (ERA5_DD_MM_YYYY.nc)
# is handled via a custom `file_format`; his coordinate names (level, time)
# don't match AEIC.weather.Weather's expected (pressure_level, valid_time),
# so xr.open_dataset is monkeypatched below to rename on the fly when Weather
# reads a file -- avoids duplicating multi-GB files just for a coord rename.

import matplotlib.pyplot as plt
import pandas as pd

import AEIC.trajectories.builders as tb
import AEIC.weather as _weather_module
from AEIC.config import Config, config
from AEIC.missions import Mission
from AEIC.missions.mission import iso_to_timestamp
from AEIC.performance.models import PerformanceModel

_original_open_dataset = _weather_module.xr.open_dataset


def _open_dataset_with_renamed_coords(path, *args, **kwargs):
    ds = _original_open_dataset(path, *args, **kwargs)
    rename = {
        k: v
        for k, v in {'level': 'pressure_level', 'time': 'valid_time'}.items()
        if k in ds.coords
    }
    return ds.rename(rename) if rename else ds


_weather_module.xr.open_dataset = _open_dataset_with_renamed_coords

WEATHER_DIR = '/net/d16/data/travnik/era5/wind_and_temperature'

Config.load(
    weather={
        'use_weather': True,
        'weather_data_dir': WEATHER_DIR,
        'file_resolution': 'daily',
        'data_resolution': 'hourly',
        'file_format': 'ERA5_%d_%m_%Y.nc',
    }
)

performance_model = PerformanceModel.load(
    config.file_location('performance/sample_performance_model.toml')
)

missions = {
    'JFK->LAX (westbound)': Mission(
        origin='JFK',
        destination='LAX',
        departure=iso_to_timestamp('2022-11-21T12:00:00'),
        arrival=iso_to_timestamp('2022-11-21T18:30:00'),
        aircraft_type='738',
        load_factor=1.0,
    ),
    'LAX->JFK (eastbound)': Mission(
        origin='LAX',
        destination='JFK',
        departure=iso_to_timestamp('2022-11-21T12:00:00'),
        arrival=iso_to_timestamp('2022-11-21T17:30:00'),
        aircraft_type='738',
        load_factor=1.0,
    ),
}

builder_off = tb.LegacyBuilder(
    options=tb.Options(use_weather=False, iterate_mass=False)
)
builder_on = tb.LegacyBuilder(options=tb.Options(use_weather=True, iterate_mass=False))

results = []
trajectories = {}

for name, mission in missions.items():
    traj_off = builder_off.fly(performance_model, mission)
    traj_on = builder_on.fly(performance_model, mission)
    trajectories[name] = (traj_off, traj_on)

    duration_off = float(traj_off.flight_time[-1])
    duration_on = float(traj_on.flight_time[-1])
    fuel_off = float(traj_off.total_fuel_mass)
    fuel_on = float(traj_on.total_fuel_mass)

    results.append(
        {
            'mission': name,
            'duration_off_min': duration_off / 60,
            'duration_on_min': duration_on / 60,
            'duration_delta_min': (duration_on - duration_off) / 60,
            'fuel_off_kg': fuel_off,
            'fuel_on_kg': fuel_on,
            'fuel_delta_kg': fuel_on - fuel_off,
            'mean_ground_speed_off_ms': float(traj_off.ground_speed.mean()),
            'mean_ground_speed_on_ms': float(traj_on.ground_speed.mean()),
        }
    )

df = pd.DataFrame(results)
pd.set_option('display.width', 120)
print(df.to_string(index=False))

out_csv = '/net/d16/data/shreya22/Paper_1/wind_phase1_output/wind_phase1_compare.csv'
df.to_csv(out_csv, index=False)
print(f'\nSaved comparison table to {out_csv}')

# Ground speed vs ground distance, wind on/off overlaid, one subplot per mission.
fig, axes = plt.subplots(1, len(missions), figsize=(12, 5), sharey=True)
for ax, (name, (traj_off, traj_on)) in zip(axes, trajectories.items()):
    ax.plot(
        traj_off.ground_distance / 1000,
        traj_off.ground_speed,
        label='wind off',
        color='tab:gray',
    )
    ax.plot(
        traj_on.ground_distance / 1000,
        traj_on.ground_speed,
        label='wind on',
        color='tab:red',
    )
    ax.set_title(name)
    ax.set_xlabel('Ground distance [km]')
    ax.legend()
axes[0].set_ylabel('Ground speed [m/s]')
fig.tight_layout()

out_png = (
    '/net/d16/data/shreya22/Paper_1/wind_phase1_output/wind_phase1_groundspeed.png'
)
fig.savefig(out_png, dpi=150, bbox_inches='tight')
print(f'Saved ground speed plot to {out_png}')
