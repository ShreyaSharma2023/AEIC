"""The GEOS-Chem 72-level vertical grid for gridded inventories.

The levels are the model's terrain-following hybrid sigma-pressure levels, taken
at a standard surface pressure because trajectories carry no local surface
pressure. Their edges are therefore the published Ap and Bp combined with that
one pressure: edge(L) = Ap(L) + Bp(L) * PS.
"""

import netCDF4 as nc4
import numpy as np
import pytest

from AEIC.commands.trajectories_to_grid import reduce_phase
from AEIC.gridding.grid import PRESSURE_GRIDS, GEOSChemGrid, Grid
from AEIC.types import Species
from AEIC.utils.geos_levels import AP_HPA, BP, N_LEVELS
from tests.test_gridding import _make_mission_db, _write_zarr_slice

GRID = {
    'latitude': {'resolution': 0.5},
    'longitude': {'resolution': 0.625},
    'altitude': {'mode': 'geoschem_72'},
}


def test_the_grid_has_the_72_geos_chem_levels():
    grid = Grid.model_validate(GRID)

    assert isinstance(grid.altitude, GEOSChemGrid)
    assert grid.altitude.bins == N_LEVELS == 72
    assert grid.shape == (360, 576, 72)
    assert isinstance(grid.altitude, PRESSURE_GRIDS)


def test_the_edges_are_ap_plus_bp_times_the_standard_surface_pressure():
    """Ascending in pressure, from the model top to the surface."""
    alt = Grid.model_validate(GRID).altitude

    expected = np.sort(AP_HPA + BP * 1013.25)
    assert alt.edges == pytest.approx(expected)
    assert len(alt.edges) == 73
    assert alt.edges[0] == pytest.approx(0.01)  # model top
    assert alt.edges[-1] == pytest.approx(1013.25)  # the surface


def test_the_surface_pressure_can_be_set():
    alt = Grid.model_validate(
        {**GRID, 'altitude': {'mode': 'geoschem_72', 'surface_pressure': 1000.0}}
    ).altitude

    assert alt.edges[-1] == pytest.approx(1000.0)
    assert alt.edges == pytest.approx(np.sort(AP_HPA + BP * 1000.0))


def test_a_pressure_falls_in_the_geos_chem_level_between_its_edges():
    """Level 1 is the surface layer, between edges 1 and 2 of the model."""
    alt = Grid.model_validate(GRID).altitude
    edge1, edge2 = AP_HPA[0] + BP[0] * 1013.25, AP_HPA[1] + BP[1] * 1013.25

    def geos_level(pressure):
        k = np.searchsorted(alt.edges, pressure, side='right') - 1  # ascending
        return N_LEVELS - k  # 1 = surface

    assert geos_level(0.5 * (edge1 + edge2)) == 1
    assert geos_level(edge2 - 0.01) == 2
    # Independent of the edge array: walk down the model's own surface-first edges.
    edges_up = AP_HPA + BP * 1013.25
    for pressure in (900.0, 500.0, 250.0, 100.0, 10.0):
        expected = (
            int(
                np.nonzero((edges_up[:-1] >= pressure) & (pressure > edges_up[1:]))[0][
                    0
                ]
            )
            + 1
        )
        assert geos_level(pressure) == expected
    top_edges = np.sort(AP_HPA + BP * 1013.25)[:2]
    assert geos_level(top_edges.mean()) == 72  # the top layer


def test_the_levels_are_the_layer_mid_pressures_surface_first():
    alt = Grid.model_validate(GRID).altitude
    edges_up = AP_HPA + BP * 1013.25  # surface first, as the model orders them

    assert alt.levels == pytest.approx(0.5 * (edges_up[:-1] + edges_up[1:]))
    assert alt.levels[0] > alt.levels[-1]


def _reduced(tmp_path, data):
    grid = Grid.model_validate(GRID)
    nspecies = 1
    shape = grid.shape + (nspecies,)
    lto = np.zeros((grid.latitude.bins, grid.longitude.bins, nspecies), np.float32)
    prefix = str(tmp_path / 'map')
    _write_zarr_slice(f'{prefix}-00000.zarr', data(shape), lto_data=lto, grid=grid)
    db = tmp_path / 'missions.sqlite'
    _make_mission_db(db, [1546300800, 1577836799])
    out = tmp_path / 'out.nc'
    reduce_phase(
        grid,
        [Species.CO2],
        prefix,
        out,
        db,
        tmp_path / 'fake_store.nc',
        traj_repro=None,
        traj_comments=[],
    )
    return grid, nc4.Dataset(str(out), keepweakref=True)


def test_the_output_has_a_72_level_axis_with_the_hybrid_coefficients(tmp_path):
    _, ds = _reduced(tmp_path, lambda shape: np.ones(shape, dtype=np.float32))
    with ds:
        assert 'lev' in ds.dimensions and 'pressure_level' not in ds.dimensions
        assert ds.dimensions['lev'].size == 72
        assert np.array_equal(ds.variables['lev'][:], np.arange(1, 73))
        assert ds.variables['lev'].positive == 'up'
        # Coefficients are the model's, in hPa, at the 73 interfaces and 72 mids.
        assert np.allclose(ds.variables['hyai'][:], AP_HPA)
        assert np.allclose(ds.variables['hybi'][:], BP)
        assert np.allclose(ds.variables['hyam'][:], 0.5 * (AP_HPA[:-1] + AP_HPA[1:]))
        assert np.allclose(ds.variables['hybm'][:], 0.5 * (BP[:-1] + BP[1:]))
        assert ds.variables['co2'].dimensions == (
            'time',
            'lev',
            'latitude',
            'longitude',
        )


def test_level_one_of_the_output_is_the_surface_layer(tmp_path):
    """The kernel bins in ascending pressure, so its last bin is the surface
    layer; the output puts it first, as GEOS-Chem orders levels."""

    def marked(shape):
        data = np.zeros(shape, dtype=np.float32)
        data[10, 20, shape[2] - 1, 0] = 5.0  # highest-pressure bin
        return data

    _, ds = _reduced(tmp_path, marked)
    with ds:
        co2 = ds.variables['co2'][0]
        assert co2[0, 10, 20] == 5.0
        assert co2[1:, 10, 20].sum() == 0.0


def test_the_bundled_geoschem_grid_loads():
    from AEIC.config import config

    grid = Grid.load(config.file_location('grids/geoschem-0.5x0.625.toml'))

    assert grid.latitude.resolution == 0.5
    assert grid.longitude.resolution == 0.625
    assert isinstance(grid.altitude, GEOSChemGrid)
    assert grid.shape == (360, 576, 72)
