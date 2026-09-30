"""Tests for how `aeic run` chooses and configures its trajectory builder.

The choice lives in a plain function so it can be tested directly. Click only
passes the command line values in.
"""

from datetime import UTC, date

import pytest
from click.testing import CliRunner

import AEIC.trajectories.builders as tb
from AEIC.commands.run_simulations import (
    make_trajectory_builder,
    plan_slice,
    run_simulations,
)
from AEIC.missions import Database


def test_the_defaults_are_the_legacy_builder_without_mass_iteration():
    """What `aeic run` did before the options existed."""
    builder = make_trajectory_builder()

    assert type(builder) is tb.LegacyBuilder
    assert builder.options.iterate_mass is False
    assert builder.options.use_weather is False


def test_the_adjustable_builder_can_be_chosen():
    builder = make_trajectory_builder('adjustable')

    assert type(builder) is tb.AdjustableLegacyBuilder
    assert builder.descent_distance_from_model is False


@pytest.mark.parametrize('builder_name', ['legacy', 'adjustable'])
def test_mass_iteration_and_weather_reach_the_builder_options(builder_name):
    builder = make_trajectory_builder(builder_name, iterate_mass=True, use_weather=True)

    assert builder.options.iterate_mass is True
    assert builder.options.use_weather is True


@pytest.mark.parametrize('builder_name', ['legacy', 'adjustable'])
def test_ground_distance_iteration_reaches_the_builder_options(builder_name):
    builder = make_trajectory_builder(builder_name, iterate_ground_distance=True)

    assert builder.options.iterate_ground_distance is True


def test_estimating_the_descent_from_the_model_reaches_the_adjustable_builder():
    builder = make_trajectory_builder('adjustable', descent_distance_from_model=True)

    assert builder.descent_distance_from_model is True


def test_estimating_the_descent_from_the_model_is_an_error_for_the_legacy_builder():
    """`LegacyBuilder` reproduces the legacy code and does not have this option.
    Ignoring it would quietly leave flights landing short of the destination."""
    with pytest.raises(ValueError, match='--builder adjustable'):
        make_trajectory_builder('legacy', descent_distance_from_model=True)


def test_an_unknown_builder_is_an_error():
    with pytest.raises(ValueError, match="Unknown trajectory builder: 'dymos'"):
        make_trajectory_builder('dymos')


def test_the_options_are_on_the_command_line():
    """Smoke test only: the behaviour is covered above."""
    result = CliRunner().invoke(run_simulations, ['--help'])

    assert result.exit_code == 0
    for option in (
        '--builder',
        '--iterate-mass',
        '--no-iterate-mass',
        '--descent-distance-from-model',
        '--load-factor-file',
        '--departure-date',
        '--model-cache-size',
    ):
        assert option in result.output


###########################################
######   Which flights a slice flies ######
###########################################

BUSY_DAY = date(2019, 1, 8)  # 8 flights in the test subset


@pytest.fixture
def db(test_data_dir):
    with Database(test_data_dir / 'missions/oag-2019-test-subset.sqlite') as db:
        yield db


def flights_of(db, query):
    return [m for m in db(query)]


def test_without_a_date_the_slices_partition_all_the_flights(db):
    queries = [plan_slice(db, None, 4, i) for i in range(4)]

    assert sum(q.limit for q in queries) == 1197
    assert [q.offset for q in queries] == sorted(q.offset for q in queries)


def test_with_a_departure_date_the_slices_partition_that_days_flights(db):
    queries = [plan_slice(db, None, 3, i, departure_date=BUSY_DAY) for i in range(3)]

    flights = [f for q in queries for f in flights_of(db, q)]
    assert len(flights) == 8
    assert {f.departure.tz_convert(UTC).date() for f in flights} == {BUSY_DAY}
    assert len({(f.flight_id, f.departure) for f in flights}) == 8


def test_a_day_with_no_flights_is_an_error_not_an_empty_run(db):
    with pytest.raises(ValueError, match='no flights depart on 2030-01-01'):
        plan_slice(db, None, 1, 0, departure_date=date(2030, 1, 1))


def test_sampling_applies_within_the_day(db):
    query = plan_slice(db, 0.5, 1, 0, departure_date=BUSY_DAY)

    assert query.limit == 4
    assert query.start_date == query.end_date == BUSY_DAY
