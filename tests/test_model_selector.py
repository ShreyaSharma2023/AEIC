"""Tests for SimplePerformanceModelSelector with performance_model_key support."""

from pathlib import Path

import pandas as pd

from AEIC.missions.mission import Mission
from AEIC.performance.model_selector import SimplePerformanceModelSelector

# Path to an existing valid performance model TOML to copy into test directories.
_REAL_PERF_MODEL_TOML = (
    Path(__file__).parent
    / 'data'
    / 'verification'
    / 'legacy'
    / 'performance-model.toml'
)


def _make_mission(aircraft_type='B738', performance_model_key=None):
    """Helper to create a minimal Mission for testing."""
    return Mission(
        origin='JFK',
        destination='LAX',
        departure=pd.Timestamp('2019-01-15T10:00:00', tz='UTC'),
        arrival=pd.Timestamp('2019-01-15T16:00:00', tz='UTC'),
        aircraft_type=aircraft_type,
        load_factor=0.8,
        performance_model_key=performance_model_key,
    )


def _make_selector_dir(tmp_path: Path, config_content: str, model_names: list) -> Path:
    """Create a performance model selector directory with the given config and models.

    Each name in model_names gets a copy of the real performance model TOML.
    """
    import shutil

    perf_dir = tmp_path / 'perf'
    perf_dir.mkdir()

    (perf_dir / 'config.toml').write_text(config_content, encoding='utf-8')

    for name in model_names:
        shutil.copy(_REAL_PERF_MODEL_TOML, perf_dir / f'{name}.toml')

    return perf_dir


def test_selector_uses_performance_model_key(tmp_path):
    """When performance_model_key is set and a matching TOML exists, use it."""
    config_content = 'default = "generic"\n'
    perf_dir = _make_selector_dir(
        tmp_path,
        config_content,
        ['generic', 'B738_CFM56'],
    )

    selector = SimplePerformanceModelSelector(perf_dir)
    mission = _make_mission(aircraft_type='B738', performance_model_key='B738_CFM56')
    result = selector(mission)

    # The result should be the B738_CFM56 model (loaded from B738_CFM56.toml).
    # Verify it is NOT the generic (default) model by checking it is the cached
    # 'B738_CFM56' entry, not 'generic'.
    assert 'B738_CFM56' in selector._cache
    assert result is selector._cache['B738_CFM56']
    # The generic model was only loaded during __init__ for default_pm.
    # Ensure that B738_CFM56 is what was returned, not the default.
    assert result is not selector.default_pm


def test_selector_falls_back_to_aircraft_type(tmp_path):
    """When performance_model_key is None, fall back to aircraft_type lookup."""
    config_content = 'default = "generic"\n'
    perf_dir = _make_selector_dir(
        tmp_path,
        config_content,
        ['generic', 'B738_CFM56'],
    )

    selector = SimplePerformanceModelSelector(perf_dir)
    # No B738.toml exists, and no synonym for B738 — should use default.
    mission = _make_mission(aircraft_type='B738', performance_model_key=None)
    result = selector(mission)

    assert result is selector.default_pm


def test_selector_performance_model_key_synonym(tmp_path):
    """When performance_model_key has a synonym in config.toml, use it."""
    config_content = 'default = "generic"\nB738_V2500 = "B738_CFM56"\n'
    perf_dir = _make_selector_dir(
        tmp_path,
        config_content,
        ['generic', 'B738_CFM56'],
    )

    selector = SimplePerformanceModelSelector(perf_dir)
    # B738_V2500 is not a direct file, but is a synonym for B738_CFM56.
    mission = _make_mission(aircraft_type='B738', performance_model_key='B738_V2500')
    result = selector(mission)

    # Should load B738_CFM56 via the synonym.
    assert 'B738_CFM56' in selector._cache
    assert result is selector._cache['B738_CFM56']
    assert result is not selector.default_pm
