# Phase 1 wind testing: full 3-way (wind off / ERA5 / MERRA2) comparison of
# altitude, fuel flow, mass, CAS, TAS, Mach, AND the actual wind speed the
# aircraft experiences along its trajectory (extracted separately from the
# same wind fields used to fly the mission).
#
# Reuses the JFK<->LAX missions, performance model, and wind data sources from
# wind-phase1-variables.py / wind-phase1-merra2-compare.py -- see those files
# and the memory notes for the full background on data sourcing (travnik's
# ERA5, user-transferred MERRA2 Np product, monkeypatch approach, hourly
# forward-fill for MERRA2's native 3-hourly steps).

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import xarray as xr

import AEIC.trajectories.builders as tb
import AEIC.weather as _weather_module
from AEIC.config import Config, config
from AEIC.constants import T0, p0
from AEIC.missions import Mission
from AEIC.missions.mission import iso_to_timestamp
from AEIC.performance.models import PerformanceModel
from AEIC.trajectories.trajectory import Trajectory
from AEIC.utils.standard_atmosphere import (
    calculate_speed_of_sound,
    pressure_at_altitude_isa_bada4,
    speed_of_sound_at_altitude,
)

OUTPUT_DIR = Path('/net/d16/data/shreya22/Paper_1/wind_phase1_output')
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ERA5_DIR = '/net/d16/data/travnik/era5/wind_and_temperature'
MERRA2_DIR = '/net/d16/data/shreya22/Paper_1/weather_test_merra2'
ERA5_FILE = f'{ERA5_DIR}/ERA5_21_11_2022.nc'
MERRA2_FILE = f'{MERRA2_DIR}/MERRA2_400.inst3_3d_asm_Np.20221121.nc4'

missions = {
    'JFK_LAX_westbound': Mission(
        origin='JFK',
        destination='LAX',
        departure=iso_to_timestamp('2022-11-21T12:00:00'),
        arrival=iso_to_timestamp('2022-11-21T18:30:00'),
        aircraft_type='738',
        load_factor=1.0,
    ),
    'LAX_JFK_eastbound': Mission(
        origin='LAX',
        destination='JFK',
        departure=iso_to_timestamp('2022-11-21T12:00:00'),
        arrival=iso_to_timestamp('2022-11-21T17:30:00'),
        aircraft_type='738',
        load_factor=1.0,
    ),
}


# ---------------------------------------------------------------------------
# Monkeypatches: same rename/reindex logic as wind-phase1-compare.py /
# wind-phase1-merra2-compare.py, reused here both for feeding Weather and for
# the standalone wind-speed-along-trajectory extraction below.
# ---------------------------------------------------------------------------

_original_open_dataset = _weather_module.xr.open_dataset


def _open_era5(path, *args, **kwargs):
    ds = _original_open_dataset(path, *args, **kwargs)
    rename = {
        k: v
        for k, v in {'level': 'pressure_level', 'time': 'valid_time'}.items()
        if k in ds.coords
    }
    return ds.rename(rename) if rename else ds


def _open_merra2(path, *args, **kwargs):
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


# ---------------------------------------------------------------------------
# Fly wind-off, ERA5 wind-on, MERRA2 wind-on trajectories for each mission.
# ---------------------------------------------------------------------------

Config.load()
performance_model = PerformanceModel.load(
    config.file_location('performance/sample_performance_model.toml')
)

builder_off = tb.LegacyBuilder(
    options=tb.Options(use_weather=False, iterate_mass=False)
)

trajectories = {}  # mission_name -> {'off': traj, 'era5': traj, 'merra2': traj}

for name, mission in missions.items():
    trajectories[name] = {'off': builder_off.fly(performance_model, mission)}

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
for name, mission in missions.items():
    trajectories[name]['era5'] = builder_era5.fly(performance_model, mission)

_weather_module.xr.open_dataset = _open_merra2
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
for name, mission in missions.items():
    trajectories[name]['merra2'] = builder_merra2.fly(performance_model, mission)


# ---------------------------------------------------------------------------
# Wind speed actually experienced along each trajectory (independent
# extraction from the raw fields -- not something Weather.get_ground_speed
# exposes directly, so recomputed here the same way it's computed
# internally: exact-hour selection, then spatial/pressure interpolation).
# ---------------------------------------------------------------------------


def wind_speed_along_trajectory(
    nc_path: str, open_fn, mission: Mission, traj: Trajectory
) -> np.ndarray:
    ds = open_fn(nc_path)
    altitude = np.asarray(traj.altitude)
    pressure_hpa = pressure_at_altitude_isa_bada4(altitude) / 100.0
    lat = np.asarray(traj.latitude)
    lon = np.asarray(traj.longitude)
    departure = mission.departure
    if departure.tzinfo is not None:
        departure = departure.tz_convert('UTC').tz_localize(None)
    times = departure + pd.to_timedelta(np.asarray(traj.flight_time), unit='s')
    floored = pd.DatetimeIndex(times).floor('h')

    speed = np.empty(len(altitude), dtype=float)
    for hour in pd.unique(floored):
        mask = floored == hour
        field = ds.sel(valid_time=hour, method='nearest')
        pts = 'pts'
        u = (
            field['u']
            .interp(
                pressure_level=xr.DataArray(pressure_hpa[mask], dims=pts),
                latitude=xr.DataArray(lat[mask], dims=pts),
                longitude=xr.DataArray(lon[mask], dims=pts),
            )
            .values
        )
        v = (
            field['v']
            .interp(
                pressure_level=xr.DataArray(pressure_hpa[mask], dims=pts),
                latitude=xr.DataArray(lat[mask], dims=pts),
                longitude=xr.DataArray(lon[mask], dims=pts),
            )
            .values
        )
        speed[mask] = np.hypot(u, v)
    return speed


for name in missions:
    trajectories[name]['era5_wind_speed'] = wind_speed_along_trajectory(
        ERA5_FILE, _open_era5, missions[name], trajectories[name]['era5']
    )
    trajectories[name]['merra2_wind_speed'] = wind_speed_along_trajectory(
        MERRA2_FILE, _open_merra2, missions[name], trajectories[name]['merra2']
    )


# ---------------------------------------------------------------------------
# Build dataframes and plot.
# ---------------------------------------------------------------------------


def cas_from_tas(tas: np.ndarray, altitude: np.ndarray) -> np.ndarray:
    p_local = pressure_at_altitude_isa_bada4(altitude)
    a_local = speed_of_sound_at_altitude(altitude)
    mach = tas / a_local
    qc = p_local * ((1 + 0.2 * mach**2) ** 3.5 - 1)
    a0 = calculate_speed_of_sound(T0)
    return a0 * np.sqrt(5 * ((qc / p0 + 1) ** (2 / 7) - 1))


def trajectory_to_df(
    traj: Trajectory, label: str, wind_speed: np.ndarray | None
) -> pd.DataFrame:
    altitude = np.asarray(traj.altitude)
    tas = np.asarray(traj.true_airspeed)
    mach = tas / speed_of_sound_at_altitude(altitude)
    cas = cas_from_tas(tas, altitude)
    df = pd.DataFrame(
        {
            'ground_distance_km': np.asarray(traj.ground_distance) / 1000,
            'flight_time_min': np.asarray(traj.flight_time) / 60,
            'altitude_m': altitude,
            'fuel_flow_kg_s': np.asarray(traj.fuel_flow),
            'aircraft_mass_kg': np.asarray(traj.aircraft_mass),
            'cas_ms': cas,
            'tas_ms': tas,
            'mach': mach,
            'ground_speed_ms': np.asarray(traj.ground_speed),
            'wind_speed_ms': wind_speed if wind_speed is not None else np.nan,
            'wind': label,
        }
    )
    return df


VARIABLES = [
    ('altitude_m', 'Altitude [m]'),
    ('fuel_flow_kg_s', 'Fuel flow [kg/s]'),
    ('aircraft_mass_kg', 'Aircraft mass [kg]'),
    ('cas_ms', 'CAS [m/s]'),
    ('tas_ms', 'TAS [m/s]'),
    ('mach', 'Mach [-]'),
    ('ground_speed_ms', 'Ground speed [m/s]'),
    ('wind_speed_ms', 'Wind speed experienced [m/s]'),
]

COLORS = {
    'wind off': 'tab:gray',
    'ERA5 wind on': 'tab:red',
    'MERRA2 wind on': 'tab:blue',
}

for mission_name in missions:
    t = trajectories[mission_name]
    df_off = trajectory_to_df(t['off'], 'wind off', None)
    df_era5 = trajectory_to_df(t['era5'], 'ERA5 wind on', t['era5_wind_speed'])
    df_merra2 = trajectory_to_df(t['merra2'], 'MERRA2 wind on', t['merra2_wind_speed'])
    df = pd.concat([df_off, df_era5, df_merra2], ignore_index=True)

    csv_path = OUTPUT_DIR / f'{mission_name}_3way_trajectory.csv'
    df.to_csv(csv_path, index=False)
    print(f'Saved {csv_path}')

    fig, axes = plt.subplots(len(VARIABLES), 2, figsize=(12, 20), sharex='col')
    for row, (col, ylabel) in enumerate(VARIABLES):
        ax_dist, ax_time = axes[row]
        for df_case, label in (
            (df_off, 'wind off'),
            (df_era5, 'ERA5 wind on'),
            (df_merra2, 'MERRA2 wind on'),
        ):
            if col == 'wind_speed_ms' and label == 'wind off':
                continue
            ax_dist.plot(
                df_case['ground_distance_km'],
                df_case[col],
                color=COLORS[label],
                label=label,
            )
            ax_time.plot(
                df_case['flight_time_min'],
                df_case[col],
                color=COLORS[label],
                label=label,
            )
        ax_dist.set_ylabel(ylabel)
        if row == 0:
            ax_dist.legend()
            ax_dist.set_title('vs ground distance')
            ax_time.set_title('vs flight time')
        if row == len(VARIABLES) - 1:
            ax_dist.set_xlabel('Ground distance [km]')
            ax_time.set_xlabel('Flight time [min]')

    fig.suptitle(mission_name.replace('_', ' '))
    fig.tight_layout()
    png_path = OUTPUT_DIR / f'{mission_name}_3way_variables.png'
    fig.savefig(png_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f'Saved {png_path}')
