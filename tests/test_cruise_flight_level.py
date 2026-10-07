"""Cruising at the flight level a mission database gives each flight.

A mission database can carry a cruise flight level per scheduled flight (from
observed traffic). The adjustable builder aims for it instead of its default of
the model's ceiling less 7000 ft, which it keeps for a mission with no level.
The level is a target: one above the model's ceiling is flown at the ceiling,
and a route too short to reach it climbs only as high as it can. Those, and the
missions with no level, are counted. There are no step climbs: the level is
held for the whole cruise.
"""

import dataclasses
import shutil
import sqlite3

import numpy as np
import pytest

import AEIC.trajectories.builders as tb
from AEIC.missions import Database, Query
from AEIC.units import FEET_TO_METERS, FL_TO_METERS
from tests.test_adjustable_legacy_builder import _miss, _mission


@pytest.fixture
def levelled_db(tmp_path, test_data_dir):
    path = tmp_path / 'levels.sqlite'
    shutil.copy(test_data_dir / 'missions/oag-2019-test-subset.sqlite', path)
    con = sqlite3.connect(path)
    con.execute('UPDATE schedules SET flight_level = 300 + 20 * (id % 3)')
    con.execute('UPDATE schedules SET flight_level = NULL WHERE id % 10 = 0')
    con.commit()
    con.close()
    return path


def test_a_mission_carries_the_flight_level_of_its_schedule_row(levelled_db):
    con = sqlite3.connect(levelled_db)
    stored = dict(con.execute('SELECT id, flight_level FROM schedules'))
    con.close()

    with Database(levelled_db) as db:
        missions = list(db(Query()))

    assert len(missions) == 1197
    assert all(m.flight_level == stored[m.flight_id] for m in missions)
    assert {m.flight_level for m in missions} == {300.0, 320.0, 340.0, None}


def test_a_database_with_no_flight_levels_gives_none(test_data_dir):
    with Database(test_data_dir / 'missions/oag-2019-test-subset.sqlite') as db:
        assert {m.flight_level for m in db(Query(limit=50))} == {None}


def builder(**legacy):
    return tb.AdjustableLegacyBuilder(
        options=tb.Options(iterate_mass=False),
        legacy_options=tb.AdjustableLegacyOptions(**legacy),
    )


def at_level(origin, destination, level):
    return dataclasses.replace(_mission(origin, destination), flight_level=level)


def test_the_flight_cruises_at_the_missions_flight_level(performance_model):
    mission = at_level('BOS', 'LAX', 280.0)

    traj = builder().fly(performance_model, mission)

    assert float(np.max(traj.altitude)) == pytest.approx(280.0 * FL_TO_METERS)
    default = performance_model.maximum_altitude - 7000.0 * FEET_TO_METERS
    assert 280.0 * FL_TO_METERS != pytest.approx(default, abs=100.0)


def test_a_flight_level_above_the_ceiling_is_flown_at_the_ceiling_and_counted(
    performance_model,
):
    b = builder()

    traj = b.fly(performance_model, at_level('BOS', 'LAX', 600.0))

    assert float(np.max(traj.altitude)) == pytest.approx(
        performance_model.maximum_altitude
    )
    assert b.cruise_altitude_limits == {'ceiling': 1}


def test_a_route_too_short_for_the_flight_level_still_lands_and_is_counted(
    performance_model,
):
    mission = at_level('BOS', 'PVD', 350.0)
    b = builder()

    traj = b.fly(performance_model, mission)

    assert float(np.max(traj.altitude)) < 350.0 * FL_TO_METERS - 1000.0
    assert _miss(traj, mission) == pytest.approx(0.0, abs=1.0)
    assert b.cruise_altitude_limits == {'route': 1}


def test_a_flight_that_reaches_its_level_is_not_counted_as_limited(performance_model):
    b = builder()

    b.fly(performance_model, at_level('BOS', 'LAX', 280.0))

    assert b.cruise_altitude_limits == {}


def test_a_mission_without_a_flight_level_cruises_below_the_ceiling_and_is_counted(
    performance_model,
):
    b = builder()

    traj = b.fly(performance_model, _mission('BOS', 'LAX'))

    default = performance_model.maximum_altitude - 7000.0 * FEET_TO_METERS
    assert float(np.max(traj.altitude)) == pytest.approx(default)
    assert b.cruise_altitude_limits == {'no_flight_level': 1}


def test_an_explicit_cruise_altitude_takes_precedence(performance_model):
    traj = builder().fly(
        performance_model, at_level('BOS', 'LAX', 280.0), cruise_altitude=9000.0
    )

    assert float(np.max(traj.altitude)) == pytest.approx(9000.0)


def test_iterating_ground_distance_never_climbs_above_the_flight_level(
    performance_model,
):
    """Correcting a short route's apex can raise it as well as lower it; the
    mission's level is still the most it may reach."""
    mission = at_level('BOS', 'BDL', 120.0)
    b = tb.AdjustableLegacyBuilder(
        options=tb.Options(iterate_mass=False, iterate_ground_distance=True),
    )

    traj = b.fly(performance_model, mission)

    assert float(np.max(traj.altitude)) <= 120.0 * FL_TO_METERS + 1e-6
    assert abs(_miss(traj, mission)) < 1000.0
