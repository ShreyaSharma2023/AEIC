"""Flying a `PianoPerformanceModel` through `AdjustableLegacyBuilder`.

`LegacyBuilder` is for the BADA-derived legacy models only; the adjustable
builder is the one that flies other model types.

The fixtures in `tests/data/performance/piano` are dummy data, so these tests
assert that a flight can be built and is internally consistent, never that its
numbers are physically plausible.
"""

import numpy as np
import pytest

import AEIC.trajectories.builders as tb
from AEIC.missions import Mission
from AEIC.missions.mission import iso_to_timestamp
from AEIC.performance.model_builder import build_piano_model
from AEIC.performance.types import SpeedData
from AEIC.trajectories import GroundTrack


@pytest.fixture
def piano_model(piano_data, lto):
    return build_piano_model(
        piano_data,
        lto,
        aircraft_class='narrow',
        number_of_engines=2,
        maximum_payload=18000,
        operating_empty_mass=37100,
        cruise_speeds=SpeedData(cas_low=128.611, cas_high=154.3332, mach=0.80),
    )


# `aeic run` builds its trajectories without mass iteration; it is on by default.
@pytest.mark.parametrize(
    'iterate_mass', [False, True], ids=['no_mass_iter', 'mass_iter']
)
def test_a_piano_model_can_be_flown(iterate_mass, piano_model, sample_missions):
    builder = tb.AdjustableLegacyBuilder(options=tb.Options(iterate_mass=iterate_mass))

    traj = builder.fly(piano_model, sample_missions[0])

    assert traj.n_climb > 0
    assert traj.n_cruise > 0
    assert traj.n_descent > 0
    assert traj.total_fuel_mass > 0
    assert np.all(np.isfinite(traj.fuel_flow))
    # Fuel is only ever burned, so the aircraft never gets heavier.
    assert np.all(np.diff(traj.aircraft_mass) <= 1e-9)


@pytest.mark.parametrize(
    'iterate_mass', [False, True], ids=['no_mass_iter', 'mass_iter']
)
def test_estimating_the_descent_from_the_model_lands_a_piano_flight_on_the_destination(
    iterate_mass, piano_model
):
    """A PIANO descent is tabulated by mass as well as altitude, unlike the
    legacy tables, and is evaluated here at the lightest mass. This checks the
    estimate still works through that path. The static rule's error for this
    dummy table (it is arbitrary) is not asserted."""
    mission = Mission(
        origin='BOS',
        destination='LAX',
        departure=iso_to_timestamp('2024-09-01T12:00:00'),
        arrival=iso_to_timestamp('2024-09-01T18:00:00'),
        aircraft_type='738',
        load_factor=1.0,
    )
    route = GroundTrack.great_circle(
        mission.origin_position.location,
        mission.destination_position.location,
        allow_overstep=True,
    ).total_distance

    traj = tb.AdjustableLegacyBuilder(
        options=tb.Options(iterate_mass=iterate_mass),
        legacy_options=tb.AdjustableLegacyOptions(descent_distance_from_model=True),
    ).fly(piano_model, mission)

    assert float(traj.ground_distance[-1]) == pytest.approx(route, abs=1.0)


def test_the_legacy_builder_rejects_a_piano_model_with_a_clear_error(
    piano_model, sample_missions
):
    """`LegacyBuilder` is for the BADA-derived legacy models only. Handing it
    anything else used to fail with an AttributeError about a missing
    `performance_table`, which says nothing about what to do."""
    with pytest.raises(
        TypeError, match=r'PianoPerformanceModel.*AdjustableLegacyBuilder'
    ):
        tb.LegacyBuilder(options=tb.Options(iterate_mass=False)).fly(
            piano_model, sample_missions[0]
        )
