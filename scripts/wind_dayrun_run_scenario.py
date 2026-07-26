"""Fly the day-run manifest (see wind_dayrun_build_manifest.py) once for a
given wind scenario (off / era5 / merra2), in parallel across flights within
a single job.

Usage: python wind_dayrun_run_scenario.py --scenario {off,era5,merra2} [--workers N]

Each worker process independently sets up AEIC.config.Config and the
xr.open_dataset monkeypatch for the given scenario (Config is a process-global
singleton, so this can't be shared across scenarios in one process -- but is
fine to set up once per worker at pool start, since a whole run only uses one
scenario). Wind source handling (rename mappings, hourly forward-fill for
MERRA2) is identical to the validated Phase 1 scripts
(wind-phase1-compare.py / wind-phase1-merra2-compare.py).
"""

import argparse
import multiprocessing as mp
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

import AEIC.trajectories.builders as tb

OUTPUT_DIR = Path('/net/d16/data/shreya22/Paper_1/wind_phase1_output')
MANIFEST_PATH = OUTPUT_DIR / 'dayrun_manifest.csv'
PERFORMANCE_MODEL_DIR = Path('/home/shreya22/Paper_1_data/performance_models')

ERA5_DIR = '/net/d16/data/travnik/era5/wind_and_temperature'
MERRA2_DIR = '/net/d16/data/shreya22/Paper_1/weather_test_merra2'

_builder = None
_selector = None

import xarray as _xr  # noqa: E402

_original_open_dataset = _xr.open_dataset


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


def _open_a3dyn_converted(path, *args, **kwargs):
    ds = _original_open_dataset(path, *args, **kwargs)
    # Already has correct coord/var names (produced directly by
    # merra2_a3dyn_to_pressure_levels.py). A3dyn's 8 timesteps are 3-hour
    # AVERAGES centered on the half-hour (01:30, 04:30, ...), so 'nearest'
    # is the physically correct reindex method (picks whichever bin's
    # window covers a given hour) -- 'ffill' also leaves hour 0 unfilled
    # since it can't backfill before the first bin-center.
    if 'valid_time' in ds.coords and ds.sizes.get('valid_time', 1) > 1:
        day = pd.Timestamp(ds['valid_time'].values[0]).normalize()
        hourly = pd.date_range(day, day + pd.Timedelta(hours=23), freq='h')
        ds = ds.reindex(valid_time=hourly, method='nearest')
    return ds


def _worker_init(scenario: str):
    global _builder, _selector

    from AEIC.config import Config
    from AEIC.performance.model_selector import SimplePerformanceModelSelector

    if scenario == 'off':
        Config.load()
        _builder = tb.LegacyBuilder(
            options=tb.Options(use_weather=False, iterate_mass=False)
        )
    elif scenario == 'era5':
        import AEIC.weather as weather_module

        weather_module.xr.open_dataset = _open_era5
        Config.load(
            weather={
                'use_weather': True,
                'weather_data_dir': ERA5_DIR,
                'file_resolution': 'daily',
                'data_resolution': 'hourly',
                'file_format': 'ERA5_%d_%m_%Y.nc',
            }
        )
        _builder = tb.LegacyBuilder(
            options=tb.Options(use_weather=True, iterate_mass=False)
        )
    elif scenario == 'merra2':
        import AEIC.weather as weather_module

        weather_module.xr.open_dataset = _open_merra2
        Config.load(
            weather={
                'use_weather': True,
                'weather_data_dir': MERRA2_DIR,
                'file_resolution': 'daily',
                'data_resolution': 'hourly',
                'file_format': 'MERRA2_400.inst3_3d_asm_Np.%Y%m%d.nc4',
            }
        )
        _builder = tb.LegacyBuilder(
            options=tb.Options(use_weather=True, iterate_mass=False)
        )
    elif scenario == 'a3dyn':
        import AEIC.weather as weather_module

        weather_module.xr.open_dataset = _open_a3dyn_converted
        Config.load(
            weather={
                'use_weather': True,
                'weather_data_dir': MERRA2_DIR,
                'file_resolution': 'daily',
                'data_resolution': 'hourly',
                'file_format': 'MERRA2_A3dyn_converted_pressure_levels_%Y%m%d.nc',
            }
        )
        _builder = tb.LegacyBuilder(
            options=tb.Options(use_weather=True, iterate_mass=False)
        )
    else:
        raise ValueError(f'unknown scenario {scenario!r}')

    _selector = SimplePerformanceModelSelector(PERFORMANCE_MODEL_DIR)


def _fly_one(row: dict) -> dict:
    from AEIC.missions import Mission
    from AEIC.missions.mission import iso_to_timestamp

    mission = Mission(
        origin=row['origin'],
        destination=row['destination'],
        departure=iso_to_timestamp(row['departure']),
        arrival=iso_to_timestamp(row['arrival']),
        aircraft_type=row['aircraft_type'],
        load_factor=row['load_factor'],
        carrier=row.get('carrier'),
        flight_number=row.get('flight_number'),
        origin_country=row.get('origin_country'),
        destination_country=row.get('destination_country'),
        service_type=row.get('service_type'),
        engine_type=row.get('engine_type'),
        seat_capacity=row.get('seat_capacity'),
        flight_id=row.get('flight_id'),
        flight_level=row.get('flight_level'),
        performance_model_key=row.get('performance_model_key'),
    )

    try:
        pm = _selector(mission)
        traj = _builder.fly(pm, mission)
        return {
            'flight_id': row['flight_id'],
            'success': True,
            'total_fuel_mass_kg': float(traj.total_fuel_mass),
            'duration_s': float(traj.flight_time[-1]),
            'final_ground_distance_km': float(traj.ground_distance[-1]) / 1000,
            'error': None,
        }
    except Exception as exc:  # noqa: BLE001
        return {
            'flight_id': row['flight_id'],
            'success': False,
            'total_fuel_mass_kg': None,
            'duration_s': None,
            'final_ground_distance_km': None,
            'error': f'{type(exc).__name__}: {exc}\n{traceback.format_exc()}',
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--scenario', required=True, choices=['off', 'era5', 'merra2', 'a3dyn']
    )
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument(
        '--limit', type=int, default=None, help='for quick testing on a slice'
    )
    args = parser.parse_args()

    manifest = pd.read_csv(MANIFEST_PATH)
    if args.limit is not None:
        manifest = manifest.head(args.limit)
    rows = manifest.to_dict(orient='records')

    print(
        f'Flying {len(rows)} flights, scenario={args.scenario}, workers={args.workers}'
    )

    ctx = mp.get_context('spawn')
    results = []
    with ProcessPoolExecutor(
        max_workers=args.workers,
        mp_context=ctx,
        initializer=_worker_init,
        initargs=(args.scenario,),
    ) as pool:
        futures = [pool.submit(_fly_one, row) for row in rows]
        for i, future in enumerate(as_completed(futures)):
            results.append(future.result())
            if (i + 1) % 100 == 0:
                print(f'  {i + 1}/{len(rows)} done')

    df = pd.DataFrame(results)
    n_failed = (~df['success']).sum()
    print(f'Done: {len(df) - n_failed} succeeded, {n_failed} failed')

    out_path = OUTPUT_DIR / f'dayrun_{args.scenario}.csv'
    df.to_csv(out_path, index=False)
    print(f'Saved {out_path}')


if __name__ == '__main__':
    main()
