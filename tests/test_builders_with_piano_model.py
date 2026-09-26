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
from AEIC.performance.model_builder import build_piano_model
from AEIC.performance.types import SpeedData


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
