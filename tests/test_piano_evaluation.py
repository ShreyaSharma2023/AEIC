"""Tests for evaluating a `PianoPerformanceModel`.

The fixtures in `tests/data/performance/piano` are dummy data, so these tests
assert interpolation mechanics and input validation only, never that the
numbers are physically plausible.
"""

import pandas as pd
import pytest
from pydantic import ValidationError

from AEIC.performance.interpolation import MachSweepInterpolator
from AEIC.performance.model_builder import build_piano_model
from AEIC.performance.models import PianoPerformanceModel
from AEIC.performance.models.piano import cruise_mach_at_altitude
from AEIC.performance.types import AircraftState, SimpleFlightRules, SpeedData
from AEIC.units import FEET_TO_METERS, FL_TO_METERS

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


###########################################
######      Cruise speed schedule      ######
###########################################

CAS_LOW = 128.611  # 250 kts [m/s]
CAS_HIGH = 154.3332  # 300 kts [m/s]
SEA_LEVEL_SPEED_OF_SOUND = 340.294  # ISA [m/s]


def _schedule(mach=0.82, cas_low=CAS_LOW, cas_high=CAS_HIGH):
    return SpeedData(cas_low=cas_low, cas_high=cas_high, mach=mach)


def test_at_sea_level_the_schedule_flies_the_low_cas_as_a_mach_number():
    """At sea level CAS equals TAS, so the Mach number is just CAS over the
    speed of sound."""
    assert cruise_mach_at_altitude(_schedule(), 0.0) == pytest.approx(
        CAS_LOW / SEA_LEVEL_SPEED_OF_SOUND, rel=1e-4
    )


@pytest.mark.parametrize('altitude', [0.0, 3000.0, 6000.0, 9000.0, 12000.0])
def test_the_schedule_never_exceeds_the_design_mach(altitude):
    assert cruise_mach_at_altitude(_schedule(), altitude) <= 0.82


def test_above_the_crossover_the_schedule_flies_the_design_mach():
    assert cruise_mach_at_altitude(_schedule(), 12000.0) == 0.82


def test_below_the_crossover_a_higher_design_mach_is_not_reached():
    assert cruise_mach_at_altitude(_schedule(), 6000.0) < 0.75


def test_the_cas_steps_up_at_flight_level_100():
    """The schedule flies the low CAS under FL100 and the high CAS above."""
    fl100 = 10000 * FEET_TO_METERS
    assert cruise_mach_at_altitude(_schedule(), fl100 + 1) > cruise_mach_at_altitude(
        _schedule(), fl100 - 1
    )


@pytest.mark.parametrize(
    'speeds',
    [
        SpeedData(mach=0.82),
        SpeedData(cas_low=CAS_LOW, mach=0.82),
        SpeedData(cas_high=CAS_HIGH, mach=0.82),
    ],
)
def test_a_schedule_without_both_cas_values_is_rejected(speeds):
    with pytest.raises(ValueError, match='cas_low and cas_high'):
        cruise_mach_at_altitude(speeds, 6000.0)


@pytest.fixture
def cruise_model(piano_data, lto):
    """Build a PIANO model with a cruise speed schedule."""

    def _build(cruise_speeds=None) -> PianoPerformanceModel:
        return build_piano_model(
            piano_data,
            lto,
            aircraft_class='narrow',
            number_of_engines=2,
            maximum_payload=18000,
            operating_empty_mass=37100,
            cruise_speeds=cruise_speeds,
        )

    return _build


def _cruise_df(model):
    table = model.cruise_flight_performance
    return pd.DataFrame(table.data, columns=table.cols)


def test_cruise_above_the_crossover_is_evaluated_at_the_design_mach(cruise_model):
    """At FL150-160 the CAS-equivalent Mach is about 0.6, so a design Mach of
    0.55 is the lower of the two and is what the schedule flies. 0.55 is a
    tabulated Mach, so each table row must be recovered exactly."""
    model = cruise_model(_schedule(mach=0.55))
    df = _cruise_df(model)

    rows = df[(df.mach - 0.55).abs() < 1e-9]
    # Two flight levels by three masses. Guards against the selection matching
    # nothing, which would let the loop below pass without checking anything.
    assert len(rows) == 6

    for row in rows.itertuples():
        perf = _evaluate(model, SimpleFlightRules.CRUISE, row.fl, row.mass)
        assert perf.true_airspeed == pytest.approx(row.tas, abs=1e-4)
        assert perf.fuel_flow == pytest.approx(row.fuel_flow, abs=1e-4)
        assert perf.rate_of_climb == pytest.approx(0.0, abs=1e-3)


def test_cruise_below_the_crossover_is_evaluated_at_the_cas_equivalent_mach(
    cruise_model,
):
    model = cruise_model(_schedule(mach=0.82))
    df = _cruise_df(model)
    reference = MachSweepInterpolator(df)

    for fl, mass in df[['fl', 'mass']].drop_duplicates().itertuples(index=False):
        mach = cruise_mach_at_altitude(model.speeds.cruise, fl * FL_TO_METERS)
        perf = _evaluate(model, SimpleFlightRules.CRUISE, fl, mass)
        assert perf.fuel_flow == pytest.approx(
            reference(fl, mass, mach).fuel_flow, abs=1e-4
        )
        # The design Mach is not what is flown here, and the table is not
        # flat in Mach, so flying it would give a different fuel flow.
        assert perf.fuel_flow != pytest.approx(
            reference(fl, mass, 0.82).fuel_flow, abs=1e-4
        )


def test_min_and_max_mass_select_the_ends_of_the_cruise_mass_range(cruise_model):
    model = cruise_model(_schedule(mach=0.55))
    df = _cruise_df(model)
    fl = float(df.fl.min())
    for label, mass in (('min', df.mass.min()), ('max', df.mass.max())):
        assert _evaluate(
            model, SimpleFlightRules.CRUISE, fl, label
        ).fuel_flow == pytest.approx(
            _evaluate(model, SimpleFlightRules.CRUISE, fl, float(mass)).fuel_flow
        )


def test_cruise_without_a_cruise_speed_schedule_names_the_aircraft(cruise_model):
    model = cruise_model(None)
    with pytest.raises(ValueError, match='some_airplane.*speeds.cruise'):
        _evaluate(model, SimpleFlightRules.CRUISE, 150.0, 'min')


def test_a_speed_the_aircraft_cannot_sustain_is_an_error_naming_the_aircraft(
    cruise_model,
):
    """PIANO leaves out the speeds an aircraft cannot sustain at a given flight
    level and mass, so a cell can stop short of the Mach the schedule asks for.
    The nearest tabulated Mach is a different speed, so this must not be
    clipped."""
    model = cruise_model(_schedule(mach=0.55))
    df = _cruise_df(model)
    first = df.iloc[0]
    short = (df.fl == first.fl) & (df.mass == first.mass) & (df.mach > 0.5)

    data = model.model_dump()
    data['cruise_flight_performance']['data'] = df[~short].values.tolist()
    data['cruise_flight_performance']['cols'] = list(df.columns)
    damaged = PianoPerformanceModel.model_validate(data)

    with pytest.raises(
        ValueError, match=r'some_airplane.*no cruise data at Mach 0\.550'
    ):
        _evaluate(damaged, SimpleFlightRules.CRUISE, first.fl, first.mass)


def test_a_cruise_table_with_a_missing_cell_is_rejected_when_the_model_loads(
    cruise_model,
):
    model = cruise_model(_schedule())
    df = _cruise_df(model)
    first = df.iloc[0]
    missing = (df.fl == first.fl) & (df.mass == first.mass)

    data = model.model_dump()
    data['cruise_flight_performance']['data'] = df[~missing].values.tolist()
    data['cruise_flight_performance']['cols'] = list(df.columns)

    with pytest.raises(ValidationError, match=r'cruise.*every \(FL, mass\) pair'):
        PianoPerformanceModel.model_validate(data)
