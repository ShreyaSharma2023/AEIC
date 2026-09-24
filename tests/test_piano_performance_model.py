"""Tests for `build_piano_model`.

The fixtures in `tests/data/performance/piano` are dummy data, so
these tests assert that the built model round-trips through the performance
model loader, but do not check that its numbers are physically plausible.
"""

from pathlib import Path

import pytest
from pydantic import ValidationError

from AEIC.parsers.piano_reader.climb_reader import CLIMB_COLS
from AEIC.parsers.piano_reader.cruise_reader import (
    CRUISE_COLS,
    CRUISE_REFERENCE_MACH_COLS,
)
from AEIC.parsers.piano_reader.descent_reader import (
    DESCENT_COLS,
    DESCENT_IDLE_THRUST_COLS,
)
from AEIC.performance.model_builder import build_piano_model, write_performance_model
from AEIC.performance.models import PerformanceModel, PianoPerformanceModel
from AEIC.performance.types import SpeedData

OPERATING_EMPTY_MASS = 37100


@pytest.fixture
def write_model(tmp_path, piano_data, lto):
    """Write a PIANO model file and return the file it was written to."""

    def _write(name: str = 'piano.toml', **overrides) -> Path:
        overrides.setdefault('operating_empty_mass', OPERATING_EMPTY_MASS)
        model = build_piano_model(
            piano_data,
            lto,
            aircraft_class='narrow',
            number_of_engines=2,
            maximum_payload=18000,
            apu_name='APU 131-9',
            **overrides,
        )
        out_file = tmp_path / name
        write_performance_model(out_file, model)
        return out_file

    return _write


@pytest.fixture
def build(write_model):
    """Build a PIANO model and round-trip it through the model loader."""

    def _build(name: str = 'piano.toml', **overrides) -> PianoPerformanceModel:
        return PerformanceModel.load(write_model(name, **overrides))

    return _build


def test_written_file_loads_as_a_piano_model(build):
    model = build()
    assert isinstance(model, PianoPerformanceModel)
    assert model.aircraft_name == 'some_airplane, some_engine'
    assert model.aircraft_class == 'narrow'
    assert model.maximum_altitude_ft == 41100
    assert model.number_of_engines == 2
    assert model.apu is not None
    assert model.apu.name == 'APU 131-9'


def test_every_emitted_column_survives_the_round_trip(build):
    model = build()
    assert model.climb_flight_performance.cols == CLIMB_COLS
    assert model.cruise_flight_performance.cols == CRUISE_COLS
    assert model.descent_flight_performance.cols == DESCENT_COLS
    assert model.cruise_reference_mach.cols == CRUISE_REFERENCE_MACH_COLS
    assert model.descent_idle_thrust.cols == DESCENT_IDLE_THRUST_COLS


def test_operating_empty_mass_survives_the_round_trip(build):
    """PIANO exports do not contain an operating empty mass, so the builder
    takes one and every model file states it."""
    model = build()
    assert model.operating_empty_mass_kg == OPERATING_EMPTY_MASS
    assert model.empty_mass == OPERATING_EMPTY_MASS


def test_loading_fails_without_an_operating_empty_mass(write_model):
    """A file that states no operating empty mass is refused, rather than
    loading a model whose empty mass is undefined."""
    out_file = write_model()
    out_file.write_text(
        ''.join(
            line
            for line in out_file.read_text().splitlines(keepends=True)
            if not line.startswith('operating_empty_mass_kg')
        )
    )

    with pytest.raises(ValidationError, match='PIANO exports do not contain'):
        PerformanceModel.load(out_file)


def test_no_cruise_speeds_are_written_unless_supplied(build):
    """The exports sweep many cruise Mach numbers and name no operating
    speed, so the model states none unless the caller supplies one."""
    model = build()
    assert model.speeds.cruise is None
    assert model.speeds.climb.mach == pytest.approx(0.750)
    assert model.speeds.descent.mach == pytest.approx(0.750)


def test_supplied_cruise_speeds_survive_the_round_trip(build):
    model = build(
        cruise_speeds=SpeedData(cas_low=128.611, cas_high=154.3332, mach=0.82)
    )
    assert model.speeds.cruise.mach == pytest.approx(0.82)
    assert model.speeds.cruise.cas_low == pytest.approx(128.611)
    assert model.speeds.cruise.cas_high == pytest.approx(154.3332)
    # Supplying cruise speeds must not disturb the phases read from the files.
    assert model.speeds.climb.mach == pytest.approx(0.750)
    assert model.speeds.descent.mach == pytest.approx(0.750)


def test_name_and_altitude_overrides(build):
    model = build(aircraft_name='other_airplane', maximum_altitude_ft=40000)
    assert model.aircraft_name == 'other_airplane'
    assert model.maximum_altitude_ft == 40000


def test_isa_offset_comes_from_the_exports(build, piano_data):
    model = build()
    assert model.isa_offset == piano_data.isa_offset
