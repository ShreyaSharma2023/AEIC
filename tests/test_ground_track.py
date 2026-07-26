import pytest

from AEIC.trajectories.ground_track import GroundTrack
from AEIC.types import Location

ORIGIN = Location(longitude=-73.7789, latitude=40.6413)  # JFK
DESTINATION = Location(longitude=-118.4085, latitude=33.9425)  # LAX


def test_location_raises_beyond_track_when_overstep_disallowed():
    gt = GroundTrack.great_circle(ORIGIN, DESTINATION, allow_overstep=False)
    with pytest.raises(GroundTrack.Exception):
        gt.location(gt.total_distance + 1000.0)


def test_location_allows_overstep_when_enabled():
    """Regression test: `location` previously raised even when
    `allow_overstep=True` (unlike `step`, which already handled this case),
    because it called `lookup_waypoint` unconditionally instead of checking
    `allow_overstep` first. This caused real trajectory simulations to fail
    with "distance outside ground track range" during descent whenever wind
    caused the ground distance covered to exceed the route's great-circle
    distance (see legacy.py's `_fly_level_change`, which relies on
    `allow_overstep=True`)."""
    gt = GroundTrack.great_circle(ORIGIN, DESTINATION, allow_overstep=True)

    # Should not raise, and should continue along the final great-circle
    # path past the destination rather than stopping there.
    at_dest = gt.location(gt.total_distance)
    past_dest = gt.location(gt.total_distance + 1000.0)

    assert (past_dest.location.longitude, past_dest.location.latitude) != (
        at_dest.location.longitude,
        at_dest.location.latitude,
    )


def test_location_and_step_agree_on_overstep():
    """`location(from_distance + step)` and `step(from_distance, step)`
    should agree in the overstep case, since `step` is documented as a
    convenience wrapper around `location`."""
    gt = GroundTrack.great_circle(ORIGIN, DESTINATION, allow_overstep=True)

    from_distance = gt.total_distance - 500.0
    step_amount = 2000.0

    via_location = gt.location(from_distance + step_amount)
    via_step = gt.step(from_distance, step_amount)

    assert via_location.location.longitude == pytest.approx(via_step.location.longitude)
    assert via_location.location.latitude == pytest.approx(via_step.location.latitude)
