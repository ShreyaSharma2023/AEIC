# Phase 1 MERRA2-vs-ERA5 resolution test: run the same JFK<->LAX missions used
# in wind-phase1-compare.py against MERRA2 wind (coarser space: 0.5x0.625 deg
# vs ERA5's 0.25 deg; coarser time: 3-hourly vs hourly) and compare against the
# already-saved ERA5 wind-on results, to gauge how much wind-aware routing
# results depend on input wind resolution.
#
# Wind data: /net/d16/data/shreya22/Paper_1/weather_test_merra2/
#            MERRA2_400.inst3_3d_asm_Np.20221121.nc4
# (NASA's pressure-level MERRA2 product -- "Np" -- transferred by the user from
# another cluster; already on fixed pressure levels like ERA5, so no vertical
# regridding needed, just a coordinate rename. Its 3-hourly instantaneous time
# steps (00,03,...21 UTC) are expanded to hourly via forward-fill before
# AEIC.weather.Weather reads them, since Weather's data_resolution='hourly'
# path requires an exact match on the floored hour and has no continuous
# time-interpolation across valid_time entries -- see weather.py:160-162 and
# the Phase 1 plan for the full reasoning. Forward-fill is the conservative,
# honest representation of "only known every 3 hours."

import matplotlib.pyplot as plt
import pandas as pd

import AEIC.trajectories.builders as tb
import AEIC.weather as _weather_module
from AEIC.config import Config, config
from AEIC.missions import Mission
from AEIC.missions.mission import iso_to_timestamp
from AEIC.performance.models import PerformanceModel

_original_open_dataset = _weather_module.xr.open_dataset


def _open_merra2_as_weather_dataset(path, *args, **kwargs):
    ds = _original_open_dataset(path, *args, **kwargs)
    rename = {
        k: v
        for k, v in {
            'lev': 'pressure_level',
            'lat': 'latitude',
            'lon': 'longitude',
            'time': 'valid_time',
            'U': 'u',
            'V': 'v',
            'T': 't',
        }.items()
        if k in ds.coords or k in ds.data_vars
    }
    ds = ds.rename(rename) if rename else ds

    if 'valid_time' in ds.coords and ds.sizes.get('valid_time', 1) > 1:
        day = pd.Timestamp(ds['valid_time'].values[0]).normalize()
        hourly = pd.date_range(day, day + pd.Timedelta(hours=23), freq='h')
        ds = ds.reindex(valid_time=hourly, method='ffill')

    return ds


_weather_module.xr.open_dataset = _open_merra2_as_weather_dataset

WEATHER_DIR = '/net/d16/data/shreya22/Paper_1/weather_test_merra2'

Config.load(
    weather={
        'use_weather': True,
        'weather_data_dir': WEATHER_DIR,
        'file_resolution': 'daily',
        'data_resolution': 'hourly',
        'file_format': 'MERRA2_400.inst3_3d_asm_Np.%Y%m%d.nc4',
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

builder_on = tb.LegacyBuilder(options=tb.Options(use_weather=True, iterate_mass=False))

OUTPUT_DIR = '/net/d16/data/shreya22/Paper_1/wind_phase1_output'

# Load the already-saved ERA5 results (wind-off baseline + wind-on) instead of
# rerunning ERA5. These CSVs have ground_distance_km/flight_time_min but not a
# ground_speed column directly -- derive it via central differences.
era5 = {
    'JFK->LAX (westbound)': pd.read_csv(
        f'{OUTPUT_DIR}/JFK_LAX_westbound_trajectory.csv'
    ),
    'LAX->JFK (eastbound)': pd.read_csv(
        f'{OUTPUT_DIR}/LAX_JFK_eastbound_trajectory.csv'
    ),
}
for name, edf in era5.items():
    edf['ground_speed_ms'] = (
        edf['ground_distance_km'].diff() * 1000 / (edf['flight_time_min'].diff() * 60)
    )

# Mean wind-on/wind-off ground speed already computed in wind-phase1-compare.py.
era5_summary = pd.read_csv(f'{OUTPUT_DIR}/wind_phase1_compare.csv').set_index('mission')

results = []
merra2_trajectories = {}

for name, mission in missions.items():
    traj_merra2 = builder_on.fly(performance_model, mission)
    merra2_trajectories[name] = traj_merra2

    summary = era5_summary.loc[name]
    duration_off = float(summary['duration_off_min'])
    duration_era5 = float(summary['duration_on_min'])
    duration_merra2 = float(traj_merra2.flight_time[-1]) / 60

    fuel_off = float(summary['fuel_off_kg'])
    fuel_era5 = float(summary['fuel_on_kg'])
    fuel_merra2 = float(traj_merra2.total_fuel_mass)

    results.append(
        {
            'mission': name,
            'duration_off_min': duration_off,
            'duration_era5_min': duration_era5,
            'duration_merra2_min': duration_merra2,
            'era5_vs_off_min': duration_era5 - duration_off,
            'merra2_vs_off_min': duration_merra2 - duration_off,
            'merra2_vs_era5_min': duration_merra2 - duration_era5,
            'fuel_off_kg': fuel_off,
            'fuel_era5_kg': fuel_era5,
            'fuel_merra2_kg': fuel_merra2,
            'mean_ground_speed_era5_ms': float(summary['mean_ground_speed_on_ms']),
            'mean_ground_speed_merra2_ms': float(traj_merra2.ground_speed.mean()),
        }
    )

df = pd.DataFrame(results)
pd.set_option('display.width', 140)
print(df.to_string(index=False))

out_csv = f'{OUTPUT_DIR}/merra2_vs_era5_compare.csv'
df.to_csv(out_csv, index=False)
print(f'\nSaved comparison table to {out_csv}')

fig, axes = plt.subplots(1, len(missions), figsize=(12, 5), sharey=True)
for ax, name in zip(axes, missions):
    era5_off = era5[name][era5[name]['wind'] == 'wind off']
    era5_on = era5[name][era5[name]['wind'] == 'wind on']
    traj_merra2 = merra2_trajectories[name]

    ax.plot(
        era5_off['ground_distance_km'],
        era5_off['ground_speed_ms'],
        color='tab:gray',
        label='wind off',
    )
    ax.plot(
        era5_on['ground_distance_km'],
        era5_on['ground_speed_ms'],
        color='tab:red',
        label='ERA5 wind on',
    )
    ax.plot(
        traj_merra2.ground_distance / 1000,
        traj_merra2.ground_speed,
        color='tab:blue',
        label='MERRA2 wind on',
    )
    ax.set_title(name)
    ax.set_xlabel('Ground distance [km]')
    ax.legend()
axes[0].set_ylabel('Ground speed [m/s]')
fig.tight_layout()

out_png = f'{OUTPUT_DIR}/merra2_vs_era5_groundspeed.png'
fig.savefig(out_png, dpi=150, bbox_inches='tight')
print(f'Saved ground speed plot to {out_png}')
