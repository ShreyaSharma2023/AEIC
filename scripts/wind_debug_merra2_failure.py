"""Instrument a single failing MERRA2 flight to find exactly where/why the
wind lookup returns NaN -- rather than guessing from aggregate statistics."""

import sys
from pathlib import Path

import pandas as pd

import AEIC.trajectories.builders as tb
import AEIC.weather as _weather_module
from AEIC.config import Config
from AEIC.missions import Mission
from AEIC.missions.mission import iso_to_timestamp
from AEIC.performance.model_selector import SimplePerformanceModelSelector
from AEIC.utils.standard_atmosphere import pressure_at_altitude_isa_bada4

_original_open_dataset = _weather_module.xr.open_dataset


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


_weather_module.xr.open_dataset = _open_merra2

Config.load(
    weather={
        'use_weather': True,
        'weather_data_dir': '/net/d16/data/shreya22/Paper_1/weather_test_merra2',
        'file_resolution': 'daily',
        'data_resolution': 'hourly',
        'file_format': 'MERRA2_400.inst3_3d_asm_Np.%Y%m%d.nc4',
    }
)

# Wrap get_ground_speed to log every call and highlight the failing one.
_original_get_ground_speed = _weather_module.Weather.get_ground_speed


def _logged_get_ground_speed(
    self, time, gt_point, altitude, true_airspeed, azimuth=None
):
    pressure_hpa = pressure_at_altitude_isa_bada4(altitude) / 100.0
    try:
        result = _original_get_ground_speed(
            self,
            time=time,
            gt_point=gt_point,
            altitude=altitude,
            true_airspeed=true_airspeed,
            azimuth=azimuth,
        )
        return result
    except ValueError:
        print(
            f'FAILED CALL: altitude={altitude:.1f} m -> '
            f'pressure={pressure_hpa:.2f} hPa, '
            f'lat={gt_point.location.latitude:.3f}, '
            f'lon={gt_point.location.longitude:.3f}, '
            f'time={time}'
        )
        raise


_weather_module.Weather.get_ground_speed = _logged_get_ground_speed

manifest = pd.read_csv(
    '/net/d16/data/shreya22/Paper_1/wind_phase1_output/dayrun_manifest.csv'
)
flight_id = int(sys.argv[1]) if len(sys.argv) > 1 else 6139464
row = manifest[manifest['flight_id'] == flight_id].iloc[0]
print(f"Flight {flight_id}: {row['origin']} -> {row['destination']}")

mission = Mission(
    origin=row['origin'],
    destination=row['destination'],
    departure=iso_to_timestamp(row['departure']),
    arrival=iso_to_timestamp(row['arrival']),
    aircraft_type=row['aircraft_type'],
    load_factor=row['load_factor'],
    performance_model_key=row['performance_model_key'],
)

selector = SimplePerformanceModelSelector(
    Path('/home/shreya22/Paper_1_data/performance_models')
)
pm = selector(mission)

builder = tb.LegacyBuilder(options=tb.Options(use_weather=True, iterate_mass=False))
try:
    traj = builder.fly(pm, mission)
    print(
        'Succeeded (unexpectedly) -- final ground distance:', traj.ground_distance[-1]
    )
except Exception as exc:
    print(f'Builder raised: {type(exc).__name__}: {exc}')
