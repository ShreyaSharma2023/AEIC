from __future__ import annotations

import logging
import math
import shutil
from pathlib import Path

import pytest

from AEIC.config import config
from AEIC.performance.model_builder import build_piano_model
from AEIC.performance.model_selector import SimplePerformanceModelSelector
from AEIC.performance.models import (
    LegacyPerformanceModel,
    PerformanceModel,
    PianoPerformanceModel,
)
from AEIC.performance.models.legacy import ROCDFilter
from AEIC.performance.types import AircraftState, SimpleFlightRules, ThrustMode
from AEIC.units import FL_TO_METERS


def test_performance_model_initialization():
    """PerformanceModel builds config, and performance tables."""

    model = PerformanceModel.load(
        config.file_location('performance/sample_performance_model.toml')
    )
    assert isinstance(model, LegacyPerformanceModel)

    assert model.lto_performance.ICAO_UID == '01P11CM121'
    # Positive content check on the converted LTO data — a regression that
    # left LTO un-loaded would surface as fuel_flow[IDLE] == 0.0, which the
    # `is not None` smoke this replaces could not have caught.
    assert model.lto.fuel_flow[ThrustMode.IDLE] > 0

    # Cruise (ROCDFilter.ZERO) is the phase exercised by the
    # SimpleFlightRules.CRUISE evaluate call below; pin its axes.
    table = model.performance_table(ROCDFilter.ZERO)
    assert table.fl
    assert table.mass

    # Pin the BADA-3 derived mass properties: empty_mass = min / 1.2 and
    # maximum_mass = max — aggregated across all three phase tables since
    # #139 split the legacy table into climb / cruise / descent.
    all_masses = [m for f in ROCDFilter for m in model.performance_table(f).mass]
    assert model.maximum_mass == max(all_masses)
    assert model.empty_mass == pytest.approx(min(all_masses) / 1.2)
    assert model.empty_mass < model.maximum_mass

    # End-to-end sanity check on the table-was-built-and-wired contract:
    # cruise evaluation at min mass should return finite, positive
    # airspeed and fuel flow.
    state = AircraftState(altitude=1828.8, aircraft_mass='min')  # FL 60
    perf = model.evaluate(state, SimpleFlightRules.CRUISE)
    assert math.isfinite(perf.true_airspeed) and perf.true_airspeed > 0
    assert math.isfinite(perf.fuel_flow) and perf.fuel_flow > 0


@pytest.fixture(params=['legacy', 'piano'])
def any_performance_model(request, performance_model, piano_data, lto):
    if request.param == 'legacy':
        return performance_model
    return build_piano_model(
        piano_data,
        lto,
        aircraft_class='narrow',
        number_of_engines=2,
        maximum_payload=18000,
        operating_empty_mass=37100,
    )


def _phase_column(model, phase: str, name: str) -> list[float]:
    """Read a phase table column straight from the model's tables, so the
    expected values below don't go through the properties under test."""
    if isinstance(model, PianoPerformanceModel):
        return getattr(model, f'{phase}_flight_performance').column(name)
    rocd_filter = {
        'climb': ROCDFilter.POSITIVE,
        'cruise': ROCDFilter.ZERO,
        'descent': ROCDFilter.NEGATIVE,
    }[phase]
    return list(getattr(model.performance_table(rocd_filter), name))


def test_performance_model_reports_its_speed_rocd_and_cruise_floor_envelope(
    any_performance_model,
):
    """The trajectory builders read these to seed a flight's first point and
    starting mass before the model has been evaluated, so every model type
    has to provide them."""
    model = any_performance_model
    phases = ('climb', 'cruise', 'descent')

    assert model.minimum_tas == min(
        v for p in phases for v in _phase_column(model, p, 'tas')
    )
    assert model.maximum_rocd == max(
        v for p in phases for v in _phase_column(model, p, 'rocd')
    )
    assert model.lowest_cruise_altitude == pytest.approx(
        min(_phase_column(model, 'cruise', 'fl')) * FL_TO_METERS
    )


def test_performance_model_selection(performance_model_selector, sample_missions):
    # Three branches of `SimplePerformanceModelSelector.__call__` are
    # exercised below — each row is annotated with which branch resolves it,
    # so a future schema change to `simple_selector/config.toml` (e.g.
    # adding an explicit synonym for 73H) lands as a clear diff. Mission
    # aircraft types are 738, 739, 73H, 738, 7M8, 319, 319, 739, 73J, 319.
    pms = [performance_model_selector(m).aircraft_name for m in sample_missions]
    assert pms == [
        'B738',  # 0  exact match (738.toml)
        'B738',  # 1  default fallback (739 not in config)
        'B738',  # 2  default fallback (73H not in config)
        'B738',  # 3  exact match (738.toml)
        'B738',  # 4  default fallback (7M8 not in config)
        'A380',  # 5  synonym (319 → 380)
        'A380',  # 6  synonym (319 → 380)
        'B738',  # 7  default fallback (739 not in config)
        'B738',  # 8  default fallback (73J not in config)
        'A380',  # 9  synonym (319 → 380)
    ]


def _real_default_pm() -> Path:
    """A real performance-model TOML usable as the `default` entry in
    selector-error tests that need a parseable default before the
    error-under-test fires."""
    return config.file_location('performance/simple_selector/738.toml')


def test_simple_selector_init_missing_directory(tmp_path):
    with pytest.raises(ValueError, match='directory does not exist'):
        SimplePerformanceModelSelector(tmp_path / 'does_not_exist')


def test_simple_selector_init_missing_config(tmp_path):
    with pytest.raises(ValueError, match='does not contain config.toml'):
        SimplePerformanceModelSelector(tmp_path)


def test_simple_selector_init_synonym_referencing_nonexistent_file(tmp_path, caplog):
    # On main this guard is a logger.warning, not a raise — selector
    # construction succeeds and records a warning the operator can act
    # on. Pin both: construction does not raise, and the warning carries
    # the offending file name.
    shutil.copy(_real_default_pm(), tmp_path / '738.toml')
    (tmp_path / 'config.toml').write_text('default = 738\n"319" = "NONEXISTENT"\n')
    with caplog.at_level(logging.WARNING, logger='AEIC.performance.model_selector'):
        SimplePerformanceModelSelector(tmp_path)
    assert any(
        'NONEXISTENT' in r.message and 'does not exist' in r.message
        for r in caplog.records
    )


def test_simple_selector_init_missing_default(tmp_path, caplog):
    # On main this guard is a logger.warning, not a raise — the selector
    # tolerates a missing default (returning None for unknown aircraft
    # types) but logs so the misconfiguration is visible.
    shutil.copy(_real_default_pm(), tmp_path / '738.toml')
    (tmp_path / 'config.toml').write_text('"319" = 738\n')  # no default
    with caplog.at_level(logging.WARNING, logger='AEIC.performance.model_selector'):
        SimplePerformanceModelSelector(tmp_path)
    assert any('"default" entry' in r.message for r in caplog.records)


def test_simple_selector_caches_repeated_lookups(
    performance_model_selector, sample_missions
):
    """Same-aircraft-type lookups must return the cached instance — the
    LRU cache hit path. Without this the selector re-parses the TOML on
    every dispatch.
    """
    same = [
        m
        for m in sample_missions
        if m.aircraft_type == sample_missions[0].aircraft_type
    ]
    assert len(same) >= 2
    assert performance_model_selector(same[0]) is performance_model_selector(same[1])
