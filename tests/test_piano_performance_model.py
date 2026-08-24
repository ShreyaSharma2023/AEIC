# TODO: Remove this when we move to Python 3.14+.
from __future__ import annotations

import pytest

from AEIC.performance.models.legacy import PerformanceTableInput, ROCDFilter
from AEIC.performance.models.piano import PianoPerformanceTable
from AEIC.performance.types import AircraftState
from AEIC.units import FL_TO_METERS

# Shared scaffolding, deliberately parallel to tests/test_performance_table.py's
# scaffolding for the legacy (BADA) model -- this exercises the same
# __post_init__ code paths, but PianoPerformanceTable relaxes two of the
# legacy model's checks that only hold for BADA PTF data:
#   - mass count: legacy requires exactly 2-3 masses (climb/cruise) or
#     exactly 1 (descent); piano requires only a minimum (2, 1).
#   - FL-only fields: legacy also requires fuel_flow (climb) and ROCD +
#     fuel_flow (descent) to depend only on FL; piano only requires this of
#     TAS (in every phase), since PIANO can report genuine per-mass
#     fuel_flow/ROCD variation that BADA PTF's single nominal-mass columns
#     never could.

_COLS = ['FL', 'FUEL_FLOW', 'TAS', 'ROCD', 'MASS']
_COL_IDX = {name: i for i, name in enumerate(_COLS)}


def _climb_rows(masses=(60000, 70000, 80000)):
    """2 FLs x N masses, all positive ROCD and fuel_flow varying with mass."""
    rows = []
    for fl in (330, 350):
        for mass in masses:
            tas = 200 + (fl - 300) // 10
            ff = round(0.5 + 0.001 * fl + 0.00001 * mass, 6)
            rocd = 1500.0 - 0.01 * mass
            rows.append([fl, ff, tas, rocd, mass])
    return rows


def _cruise_rows(masses=(60000, 70000, 80000)):
    """2 FLs x N masses, ROCD = 0 (cruise)."""
    rows = []
    for fl in (330, 350):
        for mass in masses:
            tas = 220 + (fl - 300) // 10
            ff = round(0.5 + 0.001 * fl + 0.000001 * mass, 6)
            rows.append([fl, ff, tas, 0.0, mass])
    return rows


def _descent_rows(masses=(70000,)):
    """2 FLs x N masses, all negative ROCD (descent shape), ROCD and
    fuel_flow varying with mass."""
    rows = []
    for fl in (330, 350):
        for mass in masses:
            tas = 240 + (fl - 300) // 10
            ff = round(0.5 + 0.001 * fl + 0.00001 * mass, 6)
            rocd = -500.0 - (fl - 300) * 1.0 - 0.001 * mass
            rows.append([fl, ff, tas, rocd, mass])
    return rows


def _build(rows, rocd_type):
    return PianoPerformanceTable.from_input(
        PerformanceTableInput(cols=_COLS, data=rows), rocd_type=rocd_type
    )


def _verify_rows_recoverable(rows, model):
    for fl, ff, tas, rocd, mass in rows:
        match = model.df[(model.df.fl == fl) & (model.df.mass == mass)]
        assert len(match) == 1, f'no row at fl={fl}, mass={mass}'
        assert match.tas.values[0] == tas
        assert match.fuel_flow.values[0] == ff
        assert match.rocd.values[0] == rocd


def test_piano_performance_table_baselines_valid():
    _build(_climb_rows(), ROCDFilter.POSITIVE)
    _build(_cruise_rows(), ROCDFilter.ZERO)
    _build(_descent_rows(), ROCDFilter.NEGATIVE)


def test_piano_performance_table_more_than_three_masses_allowed():
    """Unlike the legacy (BADA) model, which caps climb/cruise at 2-3
    masses, a PIANO-derived table with more resolution is not capped."""
    masses = (55000, 60000, 65000, 70000, 80000)
    rows = _climb_rows(masses=masses)
    model = _build(rows, ROCDFilter.POSITIVE)
    assert model.mass == list(masses)
    _verify_rows_recoverable(rows, model)


def test_piano_performance_table_climb_fuel_flow_mass_varying_allowed():
    """Unlike the legacy (BADA) model, which requires climb fuel_flow to be
    FL-only, a PIANO-derived table can report genuine per-mass fuel_flow."""
    rows = _climb_rows()
    model = _build(rows, ROCDFilter.POSITIVE)
    _verify_rows_recoverable(rows, model)


def test_piano_performance_table_descent_mass_varying_allowed():
    """Unlike the legacy (BADA) model, which requires exactly one descent
    mass with FL-only ROCD/fuel_flow, a PIANO-derived table can report
    multiple descent masses with genuine per-mass ROCD/fuel_flow."""
    rows = _descent_rows(masses=(60000, 70000))
    model = _build(rows, ROCDFilter.NEGATIVE)
    assert model.mass == [60000, 70000]
    _verify_rows_recoverable(rows, model)


@pytest.mark.parametrize(
    'rocd_type, rows_fn, match',
    [
        (
            ROCDFilter.POSITIVE,
            lambda: _climb_rows(masses=(60000,)),  # 1 mass < 2
            r'Piano performance table \(climb\) has too few mass values',
        ),
        (
            ROCDFilter.ZERO,
            lambda: _cruise_rows(masses=(60000,)),  # 1 mass < 2
            r'Piano performance table \(cruise\) has too few mass values',
        ),
    ],
)
def test_piano_performance_table_wrong_mass_count(rocd_type, rows_fn, match):
    with pytest.raises(ValueError, match=match):
        _build(rows_fn(), rocd_type)


def _drop_cell(rows, fl, mass):
    return [
        r for r in rows if not (r[_COL_IDX['FL']] == fl and r[_COL_IDX['MASS']] == mass)
    ]


def _mutate_cell(rows, fl, mass, col, new):
    idx = _COL_IDX[col]
    out = [list(r) for r in rows]
    for r in out:
        if r[_COL_IDX['FL']] == fl and r[_COL_IDX['MASS']] == mass:
            r[idx] = new
    return out


def test_piano_performance_table_rocd_sign_rejects():
    with pytest.raises(
        ValueError,
        match=r'ROCD values in descent performance table are not all negative',
    ):
        _build(
            _mutate_cell(_descent_rows(), fl=330, mass=70000, col='ROCD', new=0.0),
            ROCDFilter.NEGATIVE,
        )
    with pytest.raises(
        ValueError, match=r'ROCD values in cruise performance table are not all zero'
    ):
        _build(
            _mutate_cell(_cruise_rows(), fl=330, mass=60000, col='ROCD', new=10.0),
            ROCDFilter.ZERO,
        )
    with pytest.raises(
        ValueError, match=r'some ROCD values in climb performance table are negative'
    ):
        _build(
            _mutate_cell(_climb_rows(), fl=330, mass=60000, col='ROCD', new=-100.0),
            ROCDFilter.POSITIVE,
        )


def test_piano_performance_table_coverage_rejects():
    with pytest.raises(
        ValueError, match=r'Performance data for climb does not have full coverage'
    ):
        _build(_drop_cell(_climb_rows(), fl=330, mass=60000), ROCDFilter.POSITIVE)
    with pytest.raises(
        ValueError, match=r'Performance data for cruise does not have full coverage'
    ):
        _build(_drop_cell(_cruise_rows(), fl=330, mass=60000), ROCDFilter.ZERO)


def test_piano_performance_table_tas_fl_only_rejects():
    with pytest.raises(
        ValueError, match=r'tas for cruise phase depends on variables other than FL'
    ):
        _build(
            _mutate_cell(_cruise_rows(), fl=330, mass=60000, col='TAS', new=999.0),
            ROCDFilter.ZERO,
        )
    with pytest.raises(
        ValueError, match=r'tas for climb phase depends on variables other than FL'
    ):
        _build(
            _mutate_cell(_climb_rows(), fl=330, mass=60000, col='TAS', new=999.0),
            ROCDFilter.POSITIVE,
        )


_STEP_CLIMB_COLS = _COLS + ['DRAG', 'SFC', 'MCL_AVAIL', 'ROCD_MCL_FIXMACH']


def _cruise_rows_with_step_climb(masses=(60000, 70000, 80000)):
    """Same shape as _cruise_rows, plus the 4 step-climb columns."""
    rows = []
    for fl in (330, 350):
        for mass in masses:
            tas = 220 + (fl - 300) // 10
            ff = round(0.5 + 0.001 * fl + 0.000001 * mass, 6)
            drag = 10000.0 + 0.05 * mass
            sfc = 1.7e-5
            mcl_avail = drag * 2.0 + fl  # varies with fl too, for interpolation to bite
            rocd_mcl_fixmach = 8.0 - 0.00002 * mass
            rows.append(
                [fl, ff, tas, 0.0, mass, drag, sfc, mcl_avail, rocd_mcl_fixmach]
            )
    return rows


def test_piano_performance_table_step_climb_data_absent_by_default():
    """A plain 5-column cruise table (no step-climb fields) reports
    has_step_climb_data=False and interpolate_step_climb returns None."""
    model = _build(_cruise_rows(), ROCDFilter.ZERO)
    assert model.has_step_climb_data is False
    state = AircraftState(
        altitude=340 * FL_TO_METERS,
        true_airspeed=0.0,
        rate_of_climb=0.0,
        aircraft_mass=65000.0,
    )
    assert model.interpolate_step_climb(state) is None


def test_piano_performance_table_step_climb_data_interpolates():
    """With the 4 extra columns present, interpolate_step_climb bilinearly
    interpolates them the same way interpolate() does for tas/rocd/fuel_flow."""
    rows = _cruise_rows_with_step_climb()
    model = PianoPerformanceTable.from_input(
        PerformanceTableInput(cols=_STEP_CLIMB_COLS, data=rows),
        rocd_type=ROCDFilter.ZERO,
    )
    assert model.has_step_climb_data is True

    # Exact grid point (fl=330, mass=60000) should recover the input exactly.
    state = AircraftState(
        altitude=330 * FL_TO_METERS,
        true_airspeed=0.0,
        rate_of_climb=0.0,
        aircraft_mass=60000.0,
    )
    result = model.interpolate_step_climb(state)
    assert result is not None
    assert result.drag_n == pytest.approx(10000.0 + 0.05 * 60000)
    assert result.sfc_si == pytest.approx(1.7e-5)
    assert result.mcl_avail_n == pytest.approx((10000.0 + 0.05 * 60000) * 2.0 + 330)
    assert result.rocd_mcl_fixmach_ms == pytest.approx(8.0 - 0.00002 * 60000)

    # Midpoint mass (interpolated, not a grid point) should land between the
    # two bracketing masses' drag values.
    state_mid = AircraftState(
        altitude=330 * FL_TO_METERS,
        true_airspeed=0.0,
        rate_of_climb=0.0,
        aircraft_mass=65000.0,
    )
    result_mid = model.interpolate_step_climb(state_mid)
    assert result_mid is not None
    drag_60k = 10000.0 + 0.05 * 60000
    drag_70k = 10000.0 + 0.05 * 70000
    assert drag_60k < result_mid.drag_n < drag_70k
