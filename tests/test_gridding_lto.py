"""Where the map phase puts LTO emissions.

Take-off and idle (taxi) are surface emissions, in the 2-D LTO output at the
airport cell. Climb-out and approach cover the flight between the airport and
3,000 ft above it, which the legacy builders' trajectories do not include, so
they go into the 3-D output as vertical columns spread evenly from the airport
up to the trajectory's first point (climb-out) and from its last point down to
the destination airport (approach).
"""

import numpy as np
import pytest
import zarr

from AEIC.commands.trajectories_to_grid import map_phase
from AEIC.config import config
from AEIC.emissions import compute_emissions
from AEIC.gridding.grid import Grid
from AEIC.performance.types import ThrustMode
from AEIC.types import Species
from tests.test_emissions import (
    TERMINAL_HEIGHT,
    DummyPerformanceModel,
    airports_below,
)
from tests.utils import make_test_trajectory

ORIGIN = (10.5, 20.5)  # lat, lon: inside one 1x1 cell
DESTINATION = (40.5, 60.5)
DESTINATION_ELEVATION = 200.0
SPECIES = [Species.CO2, Species.NOx]


@pytest.fixture
def grid():
    # 1x1 degree, 500 m height bins from 0 to 15.5 km.
    return Grid.load(config.file_location('grids/basic-1x1.toml'))


def _flight():
    """A short flight from a sea-level origin to a 200 m destination, starting
    and ending 3,000 ft above them as the legacy builders do."""
    n = 6
    # make_test_trajectory fills fuel flow, speeds etc. from the global RNG:
    # seed it so every call gives the same flight and the same trajectory
    # emissions.
    np.random.seed(20251)
    t = make_test_trajectory(n, 1)
    t.latitude = np.linspace(ORIGIN[0], DESTINATION[0], n)
    t.longitude = np.linspace(ORIGIN[1], DESTINATION[1], n)
    t.altitude = np.array(
        [
            TERMINAL_HEIGHT,
            5000.0,
            11000.0,
            11000.0,
            5000.0,
            DESTINATION_ELEVATION + TERMINAL_HEIGHT,
        ]
    )
    t.fuel_mass = np.linspace(20000.0, 15000.0, n)
    return t


def _gridded(tmp_path, grid, fuel, start_agl, end_agl, name):
    traj = _flight()
    mission = airports_below(traj, start_agl, end_agl)
    traj.add_fields(compute_emissions(DummyPerformanceModel(), fuel, traj, mission))
    out = str(tmp_path / name)
    map_phase(1, SPECIES, iter([traj]), grid, out)
    return (
        traj,
        zarr.open_array(f'{out}.zarr')[:],
        zarr.open_array(f'{out}-lto.zarr')[:],
    )


def _cell(grid, lat, lon):
    return grid.latitude.index(lat), grid.longitude.index(lon)


def test_climb_out_and_approach_are_vertical_columns_above_the_airports(
    tmp_path, grid, fuel
):
    kept, kept3d, _ = _gridded(
        tmp_path, grid, fuel, TERMINAL_HEIGHT, TERMINAL_HEIGHT, 'kept'
    )
    _, covered3d, _ = _gridded(tmp_path, grid, fuel, 0.0, 0.0, 'covered')
    added = kept3d.astype(np.float64) - covered3d
    # The map output is float32 and the columns share cells with trajectory
    # emissions ~1000x larger: allow float32 rounding of what is in each cell.
    rounding = 2 * np.finfo(np.float32).eps * np.abs(kept3d.astype(np.float64))

    oi, oj = _cell(grid, *ORIGIN)
    di, dj = _cell(grid, *DESTINATION)
    for s, sp in enumerate(SPECIES):
        climb = kept.lto_emissions[sp][ThrustMode.CLIMB]
        approach = kept.lto_emissions[sp][ThrustMode.APPROACH]
        assert climb > 0 and approach > 0

        # Origin at sea level: 0-500 m and 500-914.4 m of the column.
        expected = climb * np.array([500.0, TERMINAL_HEIGHT - 500.0, 0.0])
        cells = (oi, oj, slice(0, 3), s)
        assert np.all(
            np.abs(added[cells] - expected / TERMINAL_HEIGHT)
            <= 1e-6 * climb + rounding[cells]
        )
        # Destination at 200 m: 200-500, 500-1000 and 1000-1114.4 m.
        top = DESTINATION_ELEVATION + TERMINAL_HEIGHT
        expected = approach * np.array([300.0, 500.0, top - 1000.0, 0.0])
        cells = (di, dj, slice(0, 4), s)
        assert np.all(
            np.abs(added[cells] - expected / TERMINAL_HEIGHT)
            <= 1e-6 * approach + rounding[cells]
        )
        # Nothing anywhere else, and nothing lost.
        assert abs(added[..., s].sum() - (climb + approach)) <= (
            1e-6 * (climb + approach) + rounding[..., s].sum()
        )


def test_surface_output_holds_only_take_off_and_idle(tmp_path, grid, fuel):
    traj, _, lto2d = _gridded(
        tmp_path, grid, fuel, TERMINAL_HEIGHT, TERMINAL_HEIGHT, 'kept'
    )
    oi, oj = _cell(grid, *ORIGIN)
    di, dj = _cell(grid, *DESTINATION)
    for s, sp in enumerate(SPECIES):
        lto = traj.lto_emissions[sp]
        assert lto2d[oi, oj, s] == pytest.approx(
            lto[ThrustMode.TAKEOFF] + 0.5 * lto[ThrustMode.IDLE], rel=1e-6
        )
        assert lto2d[di, dj, s] == pytest.approx(0.5 * lto[ThrustMode.IDLE], rel=1e-6)
        assert lto2d[..., s].sum() == pytest.approx(
            lto[ThrustMode.TAKEOFF] + lto[ThrustMode.IDLE], rel=1e-6
        )


def test_flights_covering_their_own_terminal_phases_grid_as_before(
    tmp_path, grid, fuel
):
    """Trajectories that start and end at the airports (and stores written
    before climb-out and approach were kept) have zero climb-out and approach,
    so no columns are added and the surface output is take-off plus idle."""
    traj, out3d, lto2d = _gridded(tmp_path, grid, fuel, 0.0, 0.0, 'covered')
    for s, sp in enumerate(SPECIES):
        lto = traj.lto_emissions[sp]
        assert lto[ThrustMode.CLIMB] == 0.0 and lto[ThrustMode.APPROACH] == 0.0
        assert out3d[..., s].sum() == pytest.approx(
            np.sum(traj.trajectory_emissions[sp][:-1]), rel=1e-5
        )
        assert lto2d[..., s].sum() == pytest.approx(
            lto[ThrustMode.TAKEOFF] + lto[ThrustMode.IDLE], rel=1e-6
        )


def test_columns_on_a_pressure_grid_stay_in_the_airport_cells(tmp_path, fuel):
    """On a pressure grid (as for GEOS-Chem) the column ends are converted
    like trajectory points; the columns stay in the airport cells, in the
    lowest levels, and conserve mass."""
    grid = Grid.load(config.file_location('grids/basic-1x1-isa-pressure.toml'))
    kept, kept3d, _ = _gridded(
        tmp_path, grid, fuel, TERMINAL_HEIGHT, TERMINAL_HEIGHT, 'kept'
    )
    _, covered3d, _ = _gridded(tmp_path, grid, fuel, 0.0, 0.0, 'covered')
    added = kept3d.astype(np.float64) - covered3d
    rounding = 2 * np.finfo(np.float32).eps * np.abs(kept3d.astype(np.float64))

    oi, oj = _cell(grid, *ORIGIN)
    di, dj = _cell(grid, *DESTINATION)
    airports = np.zeros(added.shape[:2], dtype=bool)
    airports[oi, oj] = airports[di, dj] = True
    for s, sp in enumerate(SPECIES):
        total = sum(
            kept.lto_emissions[sp][m] for m in (ThrustMode.CLIMB, ThrustMode.APPROACH)
        )
        assert abs(added[..., s].sum() - total) <= (
            1e-6 * total + rounding[..., s].sum()
        )
        # Outside the airport cells only rounding noise.
        outside = np.abs(added[~airports][:, :, s])
        assert np.all(outside <= rounding[~airports][:, :, s])
