"""Gridding one period of departures, e.g. a month, instead of the whole year.

A period is a window of departure dates (UTC, both ends inclusive). Flights are
placed in the period by their departure, as the rest of the inventory is, so a
flight that crosses midnight at the end of the period counts wholly in it.
"""

from datetime import UTC, date, datetime

import click
import netCDF4 as nc4
import numpy as np
import pytest

from AEIC.commands.trajectories_to_grid import (
    _count_missions,
    _flight_ids,
    _read_period,
    reduce_phase,
)
from AEIC.gridding.grid import Grid
from AEIC.missions import Database, Filter, Query
from AEIC.types import Species
from tests.test_gridding import _make_mission_db, _write_zarr_slice

MARCH = (date(2019, 3, 1), date(2019, 3, 31))


@pytest.fixture
def subset_db(test_data_dir):
    return test_data_dir / 'missions/oag-2019-test-subset.sqlite'


def test_the_count_is_the_flights_departing_in_the_period(subset_db):
    with Database(subset_db) as db:
        expected = db(Query(start_date=MARCH[0], end_date=MARCH[1]))
        expected = len(list(expected))

    count = _count_missions(None, subset_db, None, MARCH)  # type: ignore[arg-type]

    assert 0 < count < 1197
    assert count == expected


def test_the_flight_ids_are_those_of_flights_in_the_period_and_filter(subset_db):
    flt = Filter(min_distance=1000)
    with Database(subset_db) as db:
        in_window = [
            m for m in db(Query(filter=flt, start_date=MARCH[0], end_date=MARCH[1]))
        ]
        everything = [m for m in db(Query(filter=flt))]

    ids = _flight_ids(subset_db, flt, MARCH, limit=10_000, offset=0)

    assert sorted(ids) == sorted(m.flight_id for m in in_window)
    assert len(ids) < len(everything)
    assert all(
        date(2019, 3, 1) <= m.departure.date() <= date(2019, 3, 31) for m in in_window
    )


def test_the_period_bounds_are_both_inclusive_days(subset_db):
    with Database(subset_db) as db:
        first = next(iter(db(Query(start_date=MARCH[0], end_date=MARCH[0]))))

    ids = _flight_ids(subset_db, None, (first.departure.date(),) * 2, 10_000, 0)

    assert first.flight_id in ids


def _period_json(period):
    return f'["{period[0].isoformat()}", "{period[1].isoformat()}"]'


def _reduce(tmp_path, periods):
    grid = Grid.load(
        __import__('AEIC.config', fromlist=['config']).config.file_location(
            'grids/basic-1x1.toml'
        )
    )
    nspecies = 1
    shape = grid.shape + (nspecies,)
    lto = np.zeros((grid.latitude.bins, grid.longitude.bins, nspecies), np.float32)
    prefix = str(tmp_path / 'map')
    for i, period in enumerate(periods):
        _write_zarr_slice(
            f'{prefix}-{i:05d}.zarr',
            np.ones(shape, np.float32),
            lto_data=lto,
            grid=grid,
            period=period,
        )
    db = tmp_path / 'missions.sqlite'
    # The database's own first departure is in January, unlike the period.
    _make_mission_db(db, [1546300800, 1577836799])
    out = tmp_path / 'out.nc'
    reduce_phase(
        grid,
        [Species.CO2],
        prefix,
        out,
        db,
        tmp_path / 'fake_store.nc',
        traj_repro=None,
        traj_comments=[],
    )
    return out


def test_the_output_time_is_the_start_of_the_period(tmp_path):
    out = _reduce(tmp_path, [MARCH, MARCH])

    with nc4.Dataset(str(out), keepweakref=True) as ds:
        start = datetime(2019, 3, 1, tzinfo=UTC).timestamp()
        assert ds.variables['time'][0] == start
        assert ds.period_start_utc == '2019-03-01'
        assert ds.period_end_utc == '2019-03-31'


def test_slices_of_different_periods_cannot_be_combined(tmp_path):
    with pytest.raises(click.UsageError, match='period'):
        _reduce(tmp_path, [MARCH, (date(2019, 4, 1), date(2019, 4, 30))])


def test_slices_with_and_without_a_period_cannot_be_combined(tmp_path):
    with pytest.raises(click.UsageError, match='period'):
        _reduce(tmp_path, [MARCH, None])


def test_without_a_period_the_time_is_the_first_departure_as_before(tmp_path):
    out = _reduce(tmp_path, [None, None])

    with nc4.Dataset(str(out), keepweakref=True) as ds:
        assert ds.variables['time'][0] == 1546300800
        assert 'period_start_utc' not in ds.ncattrs()


def test_a_period_is_read_back_from_the_slices(tmp_path):
    import zarr

    p = tmp_path / 'a.zarr'
    arr = zarr.create_array(store=str(p), dtype='f4', shape=(1,))
    arr.attrs['period_json'] = _period_json(MARCH)

    assert _read_period({0: p}, [0]) == MARCH
