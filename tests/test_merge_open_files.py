"""Merging many stores must not hold them all open at once.

A campaign of thousands of per-slice files crashed `merge-stores` with a segmentation
fault in the NetCDF library: every input was opened and kept open until the end. Only
the metadata of each input is needed, so each is closed as soon as it has been read.
"""

from pathlib import Path

import pytest

from AEIC.config import Config
from AEIC.trajectories import TrajectoryStore
from tests.subproc import run_in_subprocess
from tests.utils import make_test_trajectory


def _create_stores(tmp_path: Path, n: int):
    Config.load()
    for i in range(n):
        with TrajectoryStore.create(base_file=tmp_path / f'test_{i}.nc') as ts:
            ts.add(make_test_trajectory(5, i))


def _merge_counting_open_stores(tmp_path: Path, n: int):
    Config.load()
    open_now = peak = 0
    real_open = TrajectoryStore.open.__func__  # type: ignore[attr-defined]
    real_close = TrajectoryStore.close

    def counting_open(cls, *args, **kwargs):
        nonlocal open_now, peak
        store = real_open(cls, *args, **kwargs)
        open_now += 1
        peak = max(peak, open_now)
        return store

    def counting_close(self):
        nonlocal open_now
        open_now -= 1
        return real_close(self)

    TrajectoryStore.open = classmethod(counting_open)  # type: ignore[method-assign]
    TrajectoryStore.close = counting_close  # type: ignore[method-assign]
    TrajectoryStore.merge(
        output_store=tmp_path / 'merged.aeic-store',
        input_stores=[tmp_path / f'test_{i}.nc' for i in range(n)],
    )
    assert peak == 1, f'{peak} input stores were open at once'
    assert open_now == 0, f'{open_now} input stores were left open'


def test_merging_holds_one_input_open_at_a_time_and_closes_them_all(tmp_path):
    run_in_subprocess(_create_stores, tmp_path, 6)
    run_in_subprocess(_merge_counting_open_stores, tmp_path, 6)

    assert (tmp_path / 'merged.aeic-store' / 'metadata.json').exists()


def _create_named_stores(paths):
    Config.load()
    for i, path in enumerate(paths):
        path.parent.mkdir(parents=True, exist_ok=True)
        with TrajectoryStore.create(base_file=path) as ts:
            ts.add(make_test_trajectory(5, i))


def test_merging_inputs_that_share_a_file_name_is_refused_and_moves_nothing(tmp_path):
    """Merging moves each input into one directory under its own file name. Two
    inputs from different folders named alike, like the per-day `slice-000.nc`
    of a campaign, would replace one another and lose the first one's data."""
    a, b = tmp_path / 'day1' / 'slice-000.nc', tmp_path / 'day2' / 'slice-000.nc'
    run_in_subprocess(_create_named_stores, [a, b])
    merged = tmp_path / 'merged.aeic-store'

    with pytest.raises(ValueError, match=r'slice-000\.nc.*day1.*day2|same file name'):
        TrajectoryStore.merge(output_store=merged, input_stores=[a, b])

    assert a.exists() and b.exists()
    assert not merged.exists()


def test_combining_inputs_that_share_a_file_name_is_fine(tmp_path):
    """Combining copies the data into one new file, so names cannot collide."""
    a, b = tmp_path / 'day1' / 'slice-000.nc', tmp_path / 'day2' / 'slice-000.nc'
    run_in_subprocess(_create_named_stores, [a, b])

    TrajectoryStore.combine(output_store=tmp_path / 'c.nc', input_stores=[a, b])

    assert (tmp_path / 'c.nc').exists() and a.exists() and b.exists()
