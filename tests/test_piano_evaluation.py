"""Tests for evaluating a `PianoPerformanceModel`.

The fixtures in `tests/data/performance/piano` are dummy data, so these tests
assert interpolation mechanics and input validation only, never that the
numbers are physically plausible.
"""

import pytest
from pydantic import ValidationError

from AEIC.performance.model_builder import build_piano_model
from AEIC.performance.models import PianoPerformanceModel
from AEIC.performance.types import AircraftState, SimpleFlightRules
from AEIC.units import FL_TO_METERS

PHASES = [
    ('climb', SimpleFlightRules.CLIMB),
    ('descent', SimpleFlightRules.DESCEND),
]


@pytest.fixture
def piano_model(piano_data, lto) -> PianoPerformanceModel:
    return build_piano_model(
        piano_data,
        lto,
        aircraft_class='narrow',
        number_of_engines=2,
        maximum_payload=18000,
        operating_empty_mass=37100,
    )


def _rows(model, phase):
    """The phase table as a list of dicts, read without going through the
    model's interpolators."""
    table = getattr(model, f'{phase}_flight_performance')
    return [dict(zip(table.cols, row)) for row in table.data]


def _evaluate(model, rules, fl, mass):
    return model.evaluate(
        AircraftState(altitude=fl * FL_TO_METERS, aircraft_mass=mass), rules
    )


@pytest.mark.parametrize(('phase', 'rules'), PHASES)
def test_every_table_row_is_recovered_at_its_own_flight_level_and_mass(
    piano_model, phase, rules
):
    """Averaging the corners of a cell is symmetric in FL and mass, so it
    cannot catch the two axes being swapped. Recovering each row exactly can."""
    for row in _rows(piano_model, phase):
        perf = _evaluate(piano_model, rules, row['fl'], row['mass'])
        # METERS_TO_FEET is the rounded constant 3.28084, so converting a flight
        # level to metres and back is off by about 3e-8 (relative). That shifts
        # the query slightly off the node. Values that are exactly zero in the
        # dummy tables come back as ~1e-7, and ROCD, which changes fastest with
        # flight level, moves by up to ~1e-5 m/s. A swapped FL/mass axis would
        # be off by orders of magnitude more than these tolerances.
        assert perf.true_airspeed == pytest.approx(row['tas'], abs=1e-5)
        assert perf.rate_of_climb == pytest.approx(row['rocd'], abs=1e-3)
        assert perf.fuel_flow == pytest.approx(row['fuel_flow'], abs=1e-5)


@pytest.mark.parametrize(('phase', 'rules'), PHASES)
def test_the_centre_of_a_grid_cell_averages_its_four_corners(piano_model, phase, rules):
    rows = _rows(piano_model, phase)
    fls = sorted({r['fl'] for r in rows})[:2]
    masses = sorted({r['mass'] for r in rows})[:2]
    corners = [r for r in rows if r['fl'] in fls and r['mass'] in masses]
    assert len(corners) == 4

    perf = _evaluate(piano_model, rules, sum(fls) / 2, sum(masses) / 2)

    assert perf.true_airspeed == pytest.approx(sum(r['tas'] for r in corners) / 4)
    assert perf.rate_of_climb == pytest.approx(sum(r['rocd'] for r in corners) / 4)
    assert perf.fuel_flow == pytest.approx(sum(r['fuel_flow'] for r in corners) / 4)


@pytest.mark.parametrize(('phase', 'rules'), PHASES)
def test_a_state_beyond_the_table_is_clipped_to_its_edge(piano_model, phase, rules):
    """The tables are not extrapolated: beyond an edge the edge value is used."""
    rows = _rows(piano_model, phase)
    top = max(rows, key=lambda r: (r['fl'], r['mass']))

    perf = _evaluate(piano_model, rules, top['fl'] + 50, top['mass'] * 2)

    assert perf.fuel_flow == pytest.approx(top['fuel_flow'])
    assert perf.true_airspeed == pytest.approx(top['tas'])


@pytest.mark.parametrize(('phase', 'rules'), PHASES)
def test_min_and_max_mass_select_the_ends_of_the_mass_range(piano_model, phase, rules):
    rows = _rows(piano_model, phase)
    fl = rows[0]['fl']
    lightest = min(r['mass'] for r in rows)
    heaviest = max(r['mass'] for r in rows)

    for label, mass in (('min', lightest), ('max', heaviest)):
        assert _evaluate(piano_model, rules, fl, label).fuel_flow == pytest.approx(
            _evaluate(piano_model, rules, fl, mass).fuel_flow
        )


@pytest.mark.parametrize('phase', [p for p, _ in PHASES])
def test_a_table_with_a_missing_cell_is_rejected_when_the_model_loads(
    piano_model, phase
):
    """A hole in the (FL, mass) grid must fail once, at load, naming the
    aircraft and phase, and not partway through a fleet run."""
    data = piano_model.model_dump()
    data[f'{phase}_flight_performance']['data'].pop(0)

    with pytest.raises(ValidationError, match=f'{phase}.*every \\(FL, mass\\) pair'):
        PianoPerformanceModel.model_validate(data)
