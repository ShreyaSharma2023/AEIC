"""Tests for the MERRA2 72-level hybrid vertical coordinate."""

import numpy as np
import pytest

from AEIC.utils.geos_levels import (
    AP_HPA,
    BP,
    N_LEVELS,
    edge_pressures_hpa,
    mid_pressures_hpa,
)


def test_the_grid_has_72_levels_and_73_edges():
    assert N_LEVELS == 72
    assert AP_HPA.shape == BP.shape == (73,)


def test_the_surface_and_top_edges_are_where_the_published_grid_puts_them():
    assert AP_HPA[0] == 0.0 and BP[0] == 1.0  # surface: p = PS
    assert AP_HPA[-1] == pytest.approx(0.01) and BP[-1] == 0.0  # model top


@pytest.mark.parametrize('surface_hpa', [500.0, 850.0, 1013.25, 1080.0])
def test_pressure_falls_with_every_level_for_any_surface_pressure(surface_hpa):
    edges = edge_pressures_hpa(np.array(surface_hpa))

    assert edges[0] == pytest.approx(surface_hpa)
    assert np.all(np.diff(edges) < 0)


def test_an_edge_pressure_is_ap_plus_bp_times_the_surface_pressure():
    # Edge 2 (index 1), from the published values written out by hand.
    expected = 4.804826e-02 + 9.849520e-01 * 1000.0

    assert edge_pressures_hpa(np.array(1000.0))[1] == pytest.approx(expected)


def test_a_level_lies_between_its_two_edges():
    surface = np.array([[600.0, 1000.0]])
    edges = edge_pressures_hpa(surface)
    mids = mid_pressures_hpa(surface)

    assert mids.shape == (72, 1, 2)
    assert np.all(mids < edges[:-1]) and np.all(mids > edges[1:])
    assert mids[0, 0, 1] == pytest.approx(0.5 * (edges[0, 0, 1] + edges[1, 0, 1]))
