# Single-flight comparison: ground speed vs distance for no-wind / ERA5 /
# MERRA2 (Np) / MERRA2 (A3dyn, converted), reusing JFK<->LAX westbound (the
# flight used throughout Phase 1) since MERRA2/Np succeeds for it -- a clean
# 4-way comparison, not one of the terrain-crossing routes where Np fails.

import matplotlib.pyplot as plt
import pandas as pd

import AEIC.trajectories.builders as tb
import AEIC.weather as _weather_module
from AEIC.config import Config, config
from AEIC.missions import Mission
from AEIC.missions.mission import iso_to_timestamp
from AEIC.performance.models import PerformanceModel

OUTPUT_DIR = '/net/d16/data/shreya22/Paper_1/wind_phase1_output'

ERA5_DIR = '/net/d16/data/travnik/era5/wind_and_temperature'
MERRA2_DIR = '/net/d16/data/shreya22/Paper_1/weather_test_merra2'

_original_open_dataset = _weather_module.xr.open_dataset


def _open_era5(path, *args, **kwargs):
    ds = _original_open_dataset(path, *args, **kwargs)
    rename = {
        k: v
        for k, v in {'level': 'pressure_level', 'time': 'valid_time'}.items()
        if k in ds.coords
    }
    return ds.rename(rename) if rename else ds


def _open_merra2_np(path, *args, **kwargs):
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


def _open_a3dyn_converted(path, *args, **kwargs):
    ds = _original_open_dataset(path, *args, **kwargs)
    # Already has correct coord/var names. Bin-centered 3-hour averages ->
    # 'nearest' is the physically correct reindex (see wind_dayrun_run_scenario.py).
    if 'valid_time' in ds.coords and ds.sizes.get('valid_time', 1) > 1:
        day = pd.Timestamp(ds['valid_time'].values[0]).normalize()
        hourly = pd.date_range(day, day + pd.Timedelta(hours=23), freq='h')
        ds = ds.reindex(valid_time=hourly, method='nearest')
    return ds


mission = Mission(
    origin='JFK',
    destination='LAX',
    departure=iso_to_timestamp('2022-11-21T12:00:00'),
    arrival=iso_to_timestamp('2022-11-21T18:30:00'),
    aircraft_type='738',
    load_factor=1.0,
)

trajectories = {}

# Wind off.
Config.load()
performance_model = PerformanceModel.load(
    config.file_location('performance/sample_performance_model.toml')
)
builder_off = tb.LegacyBuilder(
    options=tb.Options(use_weather=False, iterate_mass=False)
)
trajectories['off'] = builder_off.fly(performance_model, mission)

# ERA5.
_weather_module.xr.open_dataset = _open_era5
Config.reset()
Config.load(
    weather={
        'use_weather': True,
        'weather_data_dir': ERA5_DIR,
        'file_resolution': 'daily',
        'data_resolution': 'hourly',
        'file_format': 'ERA5_%d_%m_%Y.nc',
    }
)
builder_era5 = tb.LegacyBuilder(
    options=tb.Options(use_weather=True, iterate_mass=False)
)
trajectories['era5'] = builder_era5.fly(performance_model, mission)

# MERRA2 Np.
_weather_module.xr.open_dataset = _open_merra2_np
Config.reset()
Config.load(
    weather={
        'use_weather': True,
        'weather_data_dir': MERRA2_DIR,
        'file_resolution': 'daily',
        'data_resolution': 'hourly',
        'file_format': 'MERRA2_400.inst3_3d_asm_Np.%Y%m%d.nc4',
    }
)
builder_merra2 = tb.LegacyBuilder(
    options=tb.Options(use_weather=True, iterate_mass=False)
)
trajectories['merra2'] = builder_merra2.fly(performance_model, mission)

# MERRA2 A3dyn (converted).
_weather_module.xr.open_dataset = _open_a3dyn_converted
Config.reset()
Config.load(
    weather={
        'use_weather': True,
        'weather_data_dir': MERRA2_DIR,
        'file_resolution': 'daily',
        'data_resolution': 'hourly',
        'file_format': 'MERRA2_A3dyn_converted_pressure_levels_%Y%m%d.nc',
    }
)
builder_a3dyn = tb.LegacyBuilder(
    options=tb.Options(use_weather=True, iterate_mass=False)
)
trajectories['a3dyn'] = builder_a3dyn.fly(performance_model, mission)

LABELS = {
    'off': 'No wind',
    'era5': 'ERA5',
    'merra2': 'MERRA2 (Np)',
    'a3dyn': 'MERRA2 (A3dyn, converted)',
}
COLORS = {'off': '#888888', 'era5': '#C44E52', 'merra2': '#DD8452', 'a3dyn': '#4C72B0'}

print('Duration and fuel by scenario (JFK->LAX):')
for name, traj in trajectories.items():
    duration_min = traj.flight_time[-1] / 60
    fuel_kg = float(traj.total_fuel_mass)
    print(
        f'  {LABELS[name]:28s} duration={duration_min:7.2f} min'
        f'   fuel={fuel_kg:9.2f} kg'
    )

fig, ax = plt.subplots(figsize=(10, 6))
for name, traj in trajectories.items():
    ax.plot(
        traj.ground_distance / 1000,
        traj.ground_speed,
        color=COLORS[name],
        label=LABELS[name],
        linewidth=2 if name == 'off' else 1.6,
        linestyle='--' if name == 'off' else '-',
    )
ax.set_xlabel('Ground distance [km]')
ax.set_ylabel('Ground speed [m/s]')
ax.set_title(
    'JFK -> LAX (2022-11-21): ground speed vs distance,\n'
    'no wind vs ERA5 vs MERRA2 (Np) vs MERRA2 (A3dyn, converted)'
)
ax.legend(fontsize=9.5)
ax.spines[['top', 'right']].set_visible(False)
fig.tight_layout()

out_path = f'{OUTPUT_DIR}/single_flight_4way_groundspeed.png'
fig.savefig(out_path, dpi=150, bbox_inches='tight')
print(f'\nSaved {out_path}')
