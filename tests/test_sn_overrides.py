"""Replacing an engine's smoke numbers from a file.

Some engines have no smoke number at some or all modes. A file of final values
per engine UID (made elsewhere, with its provenance columns) stands in for them,
so the engine keeps its own fuel flow and gaseous emission indices and only the
smoke numbers, which SCOPE11 turns into nvPM, change.
"""

import dataclasses
import math

import pytest

from AEIC.config import config
from AEIC.emissions.ei.nvpm import scope11_profile_for_engine
from AEIC.performance.edb import EDBEntry
from AEIC.performance.sn_overrides import apply_sn_override, read_sn_overrides
from AEIC.performance.types import ThrustMode, ThrustModeValues

FRACTIONS = [0.07, 0.30, 0.85, 1.0]
HEADER = 'uid,sn_idle,sn_app,sn_co,sn_to,proxy_uid,method,confidence,note\n'


def overrides_file(tmp_path, *rows):
    path = tmp_path / 'overrides.csv'
    path.write_text(HEADER + ''.join(f'{r}\n' for r in rows))
    return path


ROW = '1PW039,1.0,3.0,10.5,6.0,4PW072,copy,high,"Rule 1, superseded"'


def test_each_engine_has_its_four_smoke_numbers_in_mode_order(tmp_path):
    overrides = read_sn_overrides(overrides_file(tmp_path, ROW))

    sn = overrides['1PW039']
    assert sn[ThrustMode.IDLE] == 1.0
    assert sn[ThrustMode.APPROACH] == 3.0
    assert sn[ThrustMode.CLIMB] == 10.5
    assert sn[ThrustMode.TAKEOFF] == 6.0


def test_zero_is_a_valid_smoke_number(tmp_path):
    overrides = read_sn_overrides(
        overrides_file(tmp_path, '4PW068,0.0,1.5,4.0,6.1,4PW070,copy,high,x')
    )

    assert overrides['4PW068'][ThrustMode.IDLE] == 0.0


@pytest.mark.parametrize(
    ('row', 'match'),
    [
        ('1PW039,-1.0,3.0,10.5,6.0,a,copy,high,x', r'1PW039.*sn_idle'),
        ('1PW039,1.0,,10.5,6.0,a,copy,high,x', r'1PW039.*sn_app'),
        ('1PW039,1.0,3.0,nan,6.0,a,copy,high,x', r'1PW039.*sn_co'),
        ('1PW039,1.0,3.0,10.5,abc,a,copy,high,x', r'1PW039.*sn_to'),
    ],
)
def test_a_value_that_is_not_a_finite_non_negative_number_is_an_error(
    tmp_path, row, match
):
    with pytest.raises(ValueError, match=match):
        read_sn_overrides(overrides_file(tmp_path, row))


def test_an_engine_listed_twice_is_an_error(tmp_path):
    with pytest.raises(ValueError, match='1PW039.*twice'):
        read_sn_overrides(overrides_file(tmp_path, ROW, ROW))


def test_a_missing_column_is_an_error_naming_it(tmp_path):
    path = tmp_path / 'bad.csv'
    path.write_text('uid,sn_idle,sn_app,sn_co\n1PW039,1,2,3\n')

    with pytest.raises(ValueError, match='sn_to'):
        read_sn_overrides(path)


def test_a_changed_file_is_read_again(tmp_path):
    path = overrides_file(tmp_path, ROW)
    first = read_sn_overrides(path)['1PW039'][ThrustMode.IDLE]

    path.write_text(HEADER + '1PW039,2.0,3.0,10.5,6.0,a,copy,high,a longer note\n')
    second = read_sn_overrides(path)['1PW039'][ThrustMode.IDLE]

    assert (first, second) == (1.0, 2.0)


def _blank_engine(sample_edb_entry):
    nan = float('nan')
    return dataclasses.replace(
        sample_edb_entry, uid='1PW039', SN_matrix=ThrustModeValues(nan, nan, nan, nan)
    )


@pytest.fixture
def sample_edb_entry():
    return EDBEntry.get_engine(
        config.default_data_file_location('engines/sample_edb.xlsx'), '01P11CM121'
    )


def test_an_override_replaces_only_the_smoke_numbers(tmp_path, sample_edb_entry):
    engine = _blank_engine(sample_edb_entry)
    overrides = read_sn_overrides(overrides_file(tmp_path, ROW))

    replaced = apply_sn_override(engine, overrides)

    assert replaced.SN_matrix == overrides['1PW039']
    for field in dataclasses.fields(engine):
        if field.name != 'SN_matrix':
            assert getattr(replaced, field.name) == getattr(engine, field.name)


def test_an_engine_not_in_the_file_is_left_alone(tmp_path, sample_edb_entry):
    overrides = read_sn_overrides(overrides_file(tmp_path, ROW))

    assert apply_sn_override(sample_edb_entry, overrides) is sample_edb_entry


def test_an_engine_with_blank_smoke_numbers_estimates_nvpm_once_overridden(
    tmp_path, sample_edb_entry
):
    """Without the override this engine has no usable smoke number at any mode."""
    engine = _blank_engine(sample_edb_entry)
    with pytest.raises(ValueError, match='No usable nvPM data'):
        scope11_profile_for_engine(engine.make_lto_performance(FRACTIONS))

    replaced = apply_sn_override(
        engine, read_sn_overrides(overrides_file(tmp_path, ROW))
    )
    profile = scope11_profile_for_engine(replaced.make_lto_performance(FRACTIONS))

    assert all(
        math.isfinite(profile.mass[m]) and profile.mass[m] > 0 for m in ThrustMode
    )


def test_using_an_override_is_logged_once_per_engine(
    tmp_path, sample_edb_entry, caplog
):
    engine = _blank_engine(sample_edb_entry)
    overrides = read_sn_overrides(overrides_file(tmp_path, ROW))

    with caplog.at_level('INFO'):
        for _ in range(3):
            apply_sn_override(engine, overrides)

    assert len([r for r in caplog.records if '1PW039' in r.message]) == 1
