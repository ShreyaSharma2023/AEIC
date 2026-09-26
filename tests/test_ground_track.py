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
    """`location` used to raise even with `allow_overstep=True`, unlike `step`,
    which already handled it. The trajectory builders rely on overstepping when
    the distance covered slightly exceeds the route's great-circle distance, so
    those flights failed with "distance outside ground track range"."""
    gt = GroundTrack.great_circle(ORIGIN, DESTINATION, allow_overstep=True)

    at_dest = gt.location(gt.total_distance)
    past_dest = gt.location(gt.total_distance + 1000.0)

    # The track carries on along the final great circle rather than stopping.
    assert (past_dest.location.longitude, past_dest.location.latitude) != (
        at_dest.location.longitude,
        at_dest.location.latitude,
    )


def test_location_and_step_agree_on_overstep():
    """`step` is documented as a wrapper around `location`, so the two must
    agree when the step runs past the end of the track."""
    gt = GroundTrack.great_circle(ORIGIN, DESTINATION, allow_overstep=True)

    from_distance = gt.total_distance - 500.0
    step_amount = 2000.0

    via_location = gt.location(from_distance + step_amount)
    via_step = gt.step(from_distance, step_amount)

    assert via_location.location.longitude == pytest.approx(via_step.location.longitude)
    assert via_location.location.latitude == pytest.approx(via_step.location.latitude)


@pytest.mark.parametrize('allow_overstep', [False, True])
def test_location_at_the_end_of_the_track_is_the_destination(allow_overstep):
    """The end of the track is inside it, not an overstep, so allowing
    overstepping must not change what is returned there."""
    gt = GroundTrack.great_circle(ORIGIN, DESTINATION, allow_overstep=allow_overstep)

    at_end = gt.location(gt.total_distance)

    assert at_end.location.longitude == pytest.approx(DESTINATION.longitude)
    assert at_end.location.latitude == pytest.approx(DESTINATION.latitude)
