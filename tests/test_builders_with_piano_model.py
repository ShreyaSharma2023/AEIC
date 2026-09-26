"""Flying a `PianoPerformanceModel` through the trajectory builders.

The fixtures in `tests/data/performance/piano` are dummy data, so these tests
assert that a flight can be built and is internally consistent, never that its
numbers are physically plausible.
"""

import numpy as np
import pytest

import AEIC.trajectories.builders as tb
from AEIC.performance.model_builder import build_piano_model
from AEIC.performance.types import SpeedData

BUILDERS = [tb.LegacyBuilder, tb.AdjustableLegacyBuilder]


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


@pytest.mark.parametrize('builder_class', BUILDERS, ids=lambda c: c.__name__)
def test_a_piano_model_can_be_flown(builder_class, piano_model, sample_missions):
    # Default options, as used by `aeic run`, including mass iteration.
    traj = builder_class(options=tb.Options()).fly(piano_model, sample_missions[0])

    assert traj.n_climb > 0
    assert traj.n_cruise > 0
    assert traj.n_descent > 0
    assert traj.total_fuel_mass > 0
    assert np.all(np.isfinite(traj.fuel_flow))
    # Fuel is only ever burned, so the aircraft never gets heavier.
    assert np.all(np.diff(traj.aircraft_mass) <= 1e-9)


def test_the_two_builders_agree_on_a_piano_model_without_adjustments(
    piano_model, sample_missions
):
    """Without adjustments `AdjustableLegacyBuilder` must reproduce
    `LegacyBuilder`, as it does for a legacy model."""
    mission = sample_missions[0]
    legacy = tb.LegacyBuilder(options=tb.Options()).fly(piano_model, mission)
    adjustable = tb.AdjustableLegacyBuilder(options=tb.Options()).fly(
        piano_model, mission
    )

    assert adjustable.approx_eq(legacy)
    assert adjustable.starting_mass == pytest.approx(legacy.starting_mass)
    assert adjustable.total_fuel_mass == pytest.approx(legacy.total_fuel_mass)
