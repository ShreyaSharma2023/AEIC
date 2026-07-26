# Phase 1 wind testing: detailed per-variable trajectory comparison.
#
# For the JFK<->LAX mission pair, runs each mission with wind on vs wind off
# and saves/plots altitude, fuel flow, aircraft mass, CAS, TAS, and Mach vs.
# both ground distance and flight time. Builds on wind-phase1-compare.py
# (which only looked at ground speed and totals).
#
# Output: /net/d16/data/shreya22/Paper_1/wind_phase1_output/
#
# Wind data: read directly from /net/d16/data/travnik/era5/wind_and_temperature/
# (Marek T's raw ERA5 source -- see wind-phase1-compare.py header for the
# full explanation of the monkeypatch below and why no local copy is made).

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

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
OUTPUT_DIR = Path('/net/d16/data/shreya22/Paper_1/wind_phase1_output')
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

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

builder_off = tb.LegacyBuilder(
    options=tb.Options(use_weather=False, iterate_mass=False)
)
builder_on = tb.LegacyBuilder(options=tb.Options(use_weather=True, iterate_mass=False))


def cas_from_tas(tas: np.ndarray, altitude: np.ndarray) -> np.ndarray:
    """Calibrated airspeed from true airspeed via the standard compressible
    subsonic airspeed relation (impact pressure equivalence)."""
    p_local = pressure_at_altitude_isa_bada4(altitude)
    a_local = speed_of_sound_at_altitude(altitude)
    mach = tas / a_local
    qc = p_local * ((1 + 0.2 * mach**2) ** 3.5 - 1)
    a0 = calculate_speed_of_sound(T0)
    return a0 * np.sqrt(5 * ((qc / p0 + 1) ** (2 / 7) - 1))


def trajectory_to_df(traj: Trajectory, label: str) -> pd.DataFrame:
    altitude = np.asarray(traj.altitude)
    tas = np.asarray(traj.true_airspeed)
    mach = tas / speed_of_sound_at_altitude(altitude)
    cas = cas_from_tas(tas, altitude)
    return pd.DataFrame(
        {
            'ground_distance_km': np.asarray(traj.ground_distance) / 1000,
            'flight_time_min': np.asarray(traj.flight_time) / 60,
            'altitude_m': altitude,
            'fuel_flow_kg_s': np.asarray(traj.fuel_flow),
            'aircraft_mass_kg': np.asarray(traj.aircraft_mass),
            'cas_ms': cas,
            'tas_ms': tas,
            'mach': mach,
            'wind': label,
        }
    )


VARIABLES = [
    ('altitude_m', 'Altitude [m]'),
    ('fuel_flow_kg_s', 'Fuel flow [kg/s]'),
    ('aircraft_mass_kg', 'Aircraft mass [kg]'),
    ('cas_ms', 'CAS [m/s]'),
    ('tas_ms', 'TAS [m/s]'),
    ('mach', 'Mach [-]'),
]

for mission_name, mission in missions.items():
    traj_off = builder_off.fly(performance_model, mission)
    traj_on = builder_on.fly(performance_model, mission)

    df_off = trajectory_to_df(traj_off, 'wind off')
    df_on = trajectory_to_df(traj_on, 'wind on')
    df = pd.concat([df_off, df_on], ignore_index=True)

    csv_path = OUTPUT_DIR / f'{mission_name}_trajectory.csv'
    df.to_csv(csv_path, index=False)
    print(f'Saved {csv_path}')

    fig, axes = plt.subplots(len(VARIABLES), 2, figsize=(12, 16), sharex='col')
    for row, (col, ylabel) in enumerate(VARIABLES):
        ax_dist, ax_time = axes[row]
        for df_case, color, label in (
            (df_off, 'tab:gray', 'wind off'),
            (df_on, 'tab:red', 'wind on'),
        ):
            ax_dist.plot(
                df_case['ground_distance_km'], df_case[col], color=color, label=label
            )
            ax_time.plot(
                df_case['flight_time_min'], df_case[col], color=color, label=label
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
    png_path = OUTPUT_DIR / f'{mission_name}_variables.png'
    fig.savefig(png_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f'Saved {png_path}')
