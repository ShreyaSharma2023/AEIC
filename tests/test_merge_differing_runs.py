"""Merging stores from runs that differ in configuration or code state.

A campaign run as many independent jobs, such as one per day with its own weather
directory, cannot produce stores with identical configuration strings, or always the
same code state. Merging refuses that by default; `allow_differing_runs` accepts
differences in the software version, git state and configuration (recording that they
differed) but still refuses differences that change what the numbers mean: the Python
version and the sampling.
"""

from pathlib import Path

import pytest

from AEIC.config import Config
from AEIC.storage.reproducibility import ReproducibilityData
from AEIC.trajectories import TrajectoryStore
from tests.subproc import run_in_subprocess
from tests.utils import make_test_trajectory


def repro(**changes):
    fields = dict(
        python_version='3.12.11',
        software_version='0.4.0',
        git_commit='abc',
        git_branch='main',
        git_dirty=False,
        config='{"a": 1}',
        sample_fraction=None,
        sample_seed=None,
        files=[],
    )
    fields.update(changes)
    return ReproducibilityData(**fields)


def _create_store(path: Path, fuel: str, index: int):
    Config.load(emissions={'fuel': fuel})
    with TrajectoryStore.create(base_file=path) as ts:
        ts.add(make_test_trajectory(5, index))


def _two_stores(tmp_path):
    a, b = tmp_path / 'a.nc', tmp_path / 'b.nc'
    run_in_subprocess(_create_store, a, 'conventional_jetA', 0)
    run_in_subprocess(_create_store, b, 'SAF', 1)
    return a, b


@pytest.mark.forked
def test_stores_made_with_different_configs_do_not_merge_by_default(tmp_path):
    a, b = _two_stores(tmp_path)

    with pytest.raises(ValueError, match='configuration mismatch'):
        TrajectoryStore.merge(
            output_store=tmp_path / 'm.aeic-store', input_stores=[a, b]
        )

    assert a.exists() and b.exists()  # nothing was moved


@pytest.mark.forked
def test_stores_made_with_different_configs_merge_when_asked_and_say_so(tmp_path):
    a, b = _two_stores(tmp_path)
    merged = tmp_path / 'm.aeic-store'

    TrajectoryStore.merge(
        output_store=merged, input_stores=[a, b], allow_differing_runs=True
    )

    with TrajectoryStore.open(base_file=merged) as ts:
        assert len(ts) == 2
        assert any('differ' in c and 'config' in c for c in ts.comments)


@pytest.mark.parametrize(
    'change',
    [
        dict(config='{"a": 2}'),
        dict(git_commit='def'),
        dict(git_dirty=True),
        dict(git_branch='other'),
        dict(software_version='0.5.0'),
    ],
)
def test_runs_that_differ_cannot_be_combined_by_default(change):
    with pytest.raises(ValueError, match='mismatch'):
        ReproducibilityData.union(repro(), repro(**change))


@pytest.mark.parametrize(
    'change',
    [
        dict(config='{"a": 2}'),
        dict(git_commit='def', git_dirty=True, git_branch='other'),
        dict(software_version='0.5.0'),
    ],
)
def test_runs_that_differ_in_config_or_code_can_be_combined_when_asked(change):
    first = repro(files=[Path('a')])

    combined = ReproducibilityData.union(
        first, repro(files=[Path('b')], **change), allow_differing_runs=True
    )

    assert combined.config == first.config  # the first run's values are kept
    assert combined.files == [Path('a'), Path('b')]


@pytest.mark.parametrize(
    'change',
    [
        dict(python_version='3.13.0'),
        dict(sample_fraction=0.5),
        dict(sample_seed=7),
    ],
)
def test_runs_that_differ_in_python_or_sampling_are_never_combined(change):
    with pytest.raises(ValueError, match='mismatch'):
        ReproducibilityData.union(repro(), repro(**change), allow_differing_runs=True)


def test_the_differences_are_counted_by_field():
    differences = ReproducibilityData.differences(
        repro(),
        repro(config='{"a": 2}'),
        repro(config='{"a": 3}', git_commit='def'),
    )

    assert differences == {'config': 3, 'git_commit': 2}
