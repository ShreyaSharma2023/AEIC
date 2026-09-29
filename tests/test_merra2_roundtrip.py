"""Round trip: synthetic MERRA2 files -> `convert_day` -> `Weather`.

Converting is only correct if `Weather` then returns the wind that went in, so
these read the result back through the reader. The winds are simple functions of
longitude and time, so the expected values are written out by hand:

    u = 1 + 0.1 * longitude[deg east, -180..180] + 0.5 * hours since 2025-01-01

which is linear in time across day boundaries, so hourly interpolation must
reproduce it exactly, including in the first and last hours of the day.
"""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

from AEIC.config.weather import TemporalResolution
from AEIC.trajectories.ground_track import GroundTrack
from AEIC.types import Location
from AEIC.weather import Weather
from AEIC.weather_conversion.merra2 import (
    PRESSURE_LEVELS_HPA,
    convert_day,
    merra2_path,
    write_day,
)

LAT = np.linspace(-80.0, 80.0, 5)
LON = -180.0 + 45.0 * np.arange(8)  # MERRA2 convention: -180 .. 135
EPOCH = np.datetime64('2025-01-01T00:00')
TERRAIN_LON = 45.0  # a mountain: surface pressure 700 hPa here
TAS = 200.0


def _u(lon, hours):
    return 1.0 + 0.1 * lon + 0.5 * hours


def _write_source_files(root: Path, days: list[date], year_shift: int = 0):
    """MERRA2-style A3dyn and I3 files for each day, on a small grid."""
    for day in days:
        day0 = np.datetime64(day.isoformat())
        hours0 = float((day0 - EPOCH) / np.timedelta64(1, 'h'))
        file_day = day.replace(year=day.year - year_shift)

        a3_hours = hours0 + 1.5 + 3.0 * np.arange(8)
        u = np.zeros((8, 72, len(LAT), len(LON)), dtype=np.float32)
        for i, h in enumerate(a3_hours):
            u[i] = _u(LON, h)[None, None, :]
        a3 = xr.Dataset(
            {
                'U': (('time', 'lev', 'lat', 'lon'), u),
                'V': (('time', 'lev', 'lat', 'lon'), np.zeros_like(u)),
            },
            coords={
                'time': ('time', (90 + 180 * np.arange(8)).astype('int32')),
                'lev': ('lev', np.arange(1.0, 73.0)),
                'lat': ('lat', LAT),
                'lon': ('lon', LON),
            },
        )
        a3['time'].attrs['units'] = f'minutes since {file_day:%Y-%m-%d} 00:00:00.0'

        ps = np.full((8, len(LAT), len(LON)), 101325.0, dtype=np.float32)
        ps[:, :, list(LON).index(TERRAIN_LON)] = 70000.0
        i3 = xr.Dataset(
            {'PS': (('time', 'lat', 'lon'), ps)},
            coords={
                'time': ('time', (180 * np.arange(8)).astype('int32')),
                'lat': ('lat', LAT),
                'lon': ('lon', LON),
            },
        )
        i3['time'].attrs['units'] = f'minutes since {file_day:%Y-%m-%d} 00:00:00.0'

        for stream, ds in (('A3dyn', a3), ('I3', i3)):
            path = merra2_path(root, file_day, stream)
            path.parent.mkdir(parents=True, exist_ok=True)
            ds.to_netcdf(path)


DAY = date(2025, 1, 2)
THREE_DAYS = [DAY - timedelta(1), DAY, DAY + timedelta(1)]


@pytest.fixture(scope='module')
def converted_dir(tmp_path_factory):
    root = tmp_path_factory.mktemp('merra2')
    _write_source_files(root, THREE_DAYS)
    out = tmp_path_factory.mktemp('converted')
    write_day(convert_day(DAY, root), out / f'{DAY:%Y-%m-%d}.nc')
    return out


def _weather(converted_dir):
    return Weather(
        data_dir=converted_dir,
        file_resolution=TemporalResolution.DAILY,
        data_resolution=TemporalResolution.HOURLY,
    )


def _eastward_ground_speed(weather, lon, hour, minute=0, altitude=5000.0, lat=0.0):
    """Ground speed flying east: no cross wind here, so TAS plus the u wind."""
    point = GroundTrack.great_circle(Location(lon, lat), Location(lon + 1.0, lat))[0]
    time = pd.Timestamp(DAY.isoformat()) + pd.Timedelta(hours=hour, minutes=minute)
    return weather.get_ground_speed(time, point, altitude, TAS)


def _hours(hour):
    return (np.datetime64(DAY.isoformat()) - EPOCH) / np.timedelta64(1, 'h') + hour


@pytest.mark.parametrize('lon', [-90.0, 90.0])
def test_each_hemisphere_gets_its_own_wind(converted_dir, lon):
    """MERRA2 longitudes are -180..180 and Weather's are 0..360. Without the
    conversion a western-hemisphere point silently reads the eastern wind."""
    gs = _eastward_ground_speed(_weather(converted_dir), lon, hour=6)

    assert gs == pytest.approx(TAS + _u(lon, _hours(6)), abs=1e-3)


@pytest.mark.parametrize('hour', [0, 1, 6, 23])
def test_the_hourly_winds_are_the_interpolated_three_hourly_ones(converted_dir, hour):
    """Hours 0 and 23 fall outside the day's own 01:30..22:30 records, so they
    need the neighbouring days' files."""
    gs = _eastward_ground_speed(_weather(converted_dir), -90.0, hour)

    assert gs == pytest.approx(TAS + _u(-90.0, _hours(hour)), abs=1e-3)


def test_a_query_gets_the_record_of_its_own_hour_not_the_nearest(converted_dir):
    """Selection is by the hour a time falls in, so 06:45 gets the 06:00 field."""
    weather = _weather(converted_dir)

    assert _eastward_ground_speed(weather, -90.0, 6, 45) == pytest.approx(
        _eastward_ground_speed(weather, -90.0, 6, 0)
    )


def test_a_point_near_sea_level_gets_the_full_surface_wind(converted_dir):
    """The ISA surface is 1013.25 hPa, above the 1000 hPa level ERA5-style
    products stop at, so such a point used to be outside the data. The 1025 hPa
    level is underground; it holds the surface wind, so the reader's linear
    interpolation gives the real wind rather than one dragged towards zero."""
    gs = _eastward_ground_speed(_weather(converted_dir), -90.0, 6, altitude=5.0)

    assert gs == pytest.approx(TAS + _u(-90.0, _hours(6)), abs=1e-3)


def test_wind_inside_a_mountain_is_zero(converted_dir):
    """Near sea level over the 700 hPa terrain column the level is underground."""
    gs = _eastward_ground_speed(_weather(converted_dir), TERRAIN_LON, 6, altitude=5.0)

    assert gs == pytest.approx(TAS)


def test_above_the_mountain_the_wind_is_the_real_one(converted_dir):
    gs = _eastward_ground_speed(
        _weather(converted_dir), TERRAIN_LON, 6, altitude=10_000.0
    )

    assert gs == pytest.approx(TAS + _u(TERRAIN_LON, _hours(6)), abs=1e-3)


def test_the_file_has_no_nan_and_the_dimensions_weather_expects(converted_dir):
    with xr.open_dataset(converted_dir / f'{DAY:%Y-%m-%d}.nc') as ds:
        assert list(ds.u.dims) == [
            'valid_time',
            'pressure_level',
            'latitude',
            'longitude',
        ]
        assert not np.isnan(ds.u.values).any() and not np.isnan(ds.v.values).any()
        assert ds.valid_time.dt.hour.values.tolist() == list(range(24))
        assert ds.valid_time.dt.day.values.tolist() == [DAY.day] * 24
        assert list(ds.longitude.values) == sorted(ds.longitude.values)
        assert ds.longitude.values.min() >= 0 and ds.longitude.values.max() < 360
        assert list(ds.pressure_level.values) == list(PRESSURE_LEVELS_HPA)


def test_a_missing_neighbouring_day_is_an_error_naming_the_file(tmp_path):
    _write_source_files(tmp_path, [DAY - timedelta(1), DAY])  # no next day

    with pytest.raises(FileNotFoundError, match='20250103'):
        convert_day(DAY, tmp_path)


def test_another_years_files_can_stand_in_and_the_result_is_labelled_as_the_day(
    tmp_path,
):
    """2025 has no MERRA2 after October, so Nov/Dec read 2024's files."""
    november = date(2025, 11, 20)
    stand_in_days = [november + timedelta(d) for d in (-1, 0, 1)]
    _write_source_files(tmp_path, stand_in_days, year_shift=1)

    ds = convert_day(november, tmp_path, source_year=2024)

    assert ds.valid_time.dt.year.values.tolist() == [2025] * 24
    assert ds.valid_time.dt.month.values.tolist() == [11] * 24


###########################################
######   A range of days             ######
###########################################


def test_a_range_of_days_is_written_once_and_skipped_when_it_already_exists(tmp_path):
    from AEIC.weather_conversion.merra2 import convert_range

    source = tmp_path / 'source'
    _write_source_files(source, THREE_DAYS)
    out = tmp_path / 'out'

    first = convert_range(source, out, DAY, DAY)
    again = convert_range(source, out, DAY, DAY)
    forced = convert_range(source, out, DAY, DAY, force=True)

    assert [p.name for p in first.written] == [f'{DAY:%Y-%m-%d}.nc']
    assert again.written == [] and len(again.skipped) == 1
    assert len(forced.written) == 1
    assert not list(out.glob('*.part'))


def test_a_converted_day_can_be_verified_through_the_weather_reader(tmp_path):
    from AEIC.weather_conversion.merra2 import convert_range, verify_day

    source = tmp_path / 'source'
    _write_source_files(source, THREE_DAYS)
    convert_range(source, tmp_path / 'out', DAY, DAY)

    verify_day(tmp_path / 'out', DAY)  # raises if the reader cannot use the file


def test_the_command_is_registered_and_documented():
    """Smoke test only: the behaviour is covered above."""
    from click.testing import CliRunner

    from AEIC.commands.make_weather_data import make_weather_data

    result = CliRunner().invoke(make_weather_data, ['merra2', '--help'])

    assert result.exit_code == 0
    assert '--source-dir' in result.output
