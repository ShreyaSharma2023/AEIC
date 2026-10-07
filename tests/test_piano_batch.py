"""Tests for building PIANO performance models in batch from an airframe table.

The PIANO exports behind these tests are the dummy fixtures in
`tests/data/performance/piano`, and the EDB is the one-engine sample, so they
assert that models are built, named and wired to the right inputs, never that
the numbers are physically plausible.
"""

import csv
import dataclasses
import shutil
import sqlite3

import pytest
from click.testing import CliRunner

from AEIC.commands.make_piano_models import make_piano_models
from AEIC.config import config
from AEIC.parsers.piano_reader import PianoData
from AEIC.performance.edb import EDBEntry
from AEIC.performance.models import PerformanceModel
from AEIC.performance.piano_batch import (
    BatchReport,
    climb_masses_kg,
    crossover_altitude_ft,
    cruise_speeds,
    piano_overrides,
    read_airframes,
    read_model_keys,
    read_table_keys,
    run_batch,
    split_model_key,
)
from AEIC.performance.types import ThrustMode, ThrustModeValues
from AEIC.units import FEET_TO_METERS, KNOTS_TO_MPS, POUNDS_TO_KG
from AEIC.utils.standard_atmosphere import cas_to_tas, speed_of_sound_at_altitude

SAVE_AS = 'TEST_AIRFRAME'
EDB_UID = '01P11CM121'  # the only engine in the sample EDB

ROW = {
    'piano_plane_file': 'Test Airframe',
    'save_as': SAVE_AS,
    'n_engines': '2',
    'aircraft_class': 'narrow',
    'max_payload_kg': '21000',
    'oew_kg': '42555.1',
    'climb_start_masses_lb': '150,000 / 110,000 / 101,000 / 92,000',
    'op_cruise_mach': '0.789',
    'op_climb_mach': '0.780',
    'op_climb_cas_kts': '300',
    'op_descent_mach': '0.780',
    'op_descent_cas_kts': '290',
    'apu_name': 'APU 131-9',
}


@pytest.fixture
def manifest(tmp_path):
    path = tmp_path / 'airframes.csv'
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(ROW))
        writer.writeheader()
        writer.writerow(ROW)
    return path


@pytest.fixture
def piano_root(tmp_path, test_data_dir):
    """PIANO-database layout: data/performance/<stem>/<stem>_{climb,...}."""
    deck = tmp_path / 'piano' / 'data' / 'performance' / SAVE_AS
    deck.mkdir(parents=True)
    for phase in ('climb', 'cruise', 'descent'):
        shutil.copy(
            test_data_dir / f'performance/piano/{phase}.txt',
            deck / f'{SAVE_AS}_{phase}',
        )
    return tmp_path / 'piano'


def batch(keys, manifest, piano_root, tmp_path, **kwargs):
    return run_batch(
        keys,
        manifest,
        piano_root,
        config.default_data_file_location('engines/sample_edb.xlsx'),
        tmp_path / 'models',
        **kwargs,
    )


###########################################
######   Manifest row -> inputs      ######
###########################################


def test_climb_masses_are_converted_from_pounds_to_kilograms():
    """The airframe table gives masses in pounds with thousands separators. A
    missed conversion would shift every starting mass by a factor of 2.2."""
    assert climb_masses_kg('150,000 / 110,000') == pytest.approx(
        [150_000 * POUNDS_TO_KG, 110_000 * POUNDS_TO_KG]
    )


def test_the_climb_overrides_come_from_the_airframe_row():
    overrides = piano_overrides(ROW)

    assert overrides.climb_masses_kg == pytest.approx(
        [m * POUNDS_TO_KG for m in (150_000, 110_000, 101_000, 92_000)]
    )
    assert overrides.climb_cas_high_kts == 300.0
    assert overrides.climb_mach == 0.780
    # Below FL100 the airframe table gives no speed, so the usual 250 kt.
    assert overrides.climb_cas_low_kts == 250.0


@pytest.mark.parametrize('cas_kts, mach', [(300, 0.78), (280, 0.74), (250, 0.65)])
def test_the_crossover_altitude_is_where_the_cas_reaches_the_mach(cas_kts, mach):
    """The altitude at which flying the CAS and flying the Mach are the same
    speed, so the climb schedule has no jump there."""
    h = crossover_altitude_ft(cas_kts, mach) * FEET_TO_METERS

    mach_of_cas = cas_to_tas(cas_kts * KNOTS_TO_MPS, h) / speed_of_sound_at_altitude(h)
    assert mach_of_cas == pytest.approx(mach, abs=1e-4)


def test_a_higher_mach_crosses_over_higher():
    assert crossover_altitude_ft(300, 0.82) > crossover_altitude_ft(300, 0.74)


def test_a_climb_mach_below_the_sea_level_mach_of_the_cas_has_no_crossover():
    """A CAS that is already faster than the Mach at sea level never crosses."""
    with pytest.raises(ValueError, match='crossover'):
        crossover_altitude_ft(450, 0.5)


def test_the_climb_overrides_state_a_complete_schedule():
    """PIANO files that state no schedule need all four values, so the
    crossover altitude is derived from the CAS and Mach the table gives."""
    overrides = piano_overrides(ROW)

    assert overrides.climb_crossover_altitude_ft == pytest.approx(
        crossover_altitude_ft(300, 0.780)
    )


def test_the_descent_overrides_come_from_the_airframe_row():
    overrides = piano_overrides(ROW)

    assert overrides.descent_cas_low_kts == 250.0
    assert overrides.descent_cas_high_kts == 290.0
    assert overrides.descent_mach == 0.780
    assert overrides.descent_crossover_altitude_ft == pytest.approx(
        crossover_altitude_ft(290, 0.780)
    )


@pytest.mark.parametrize('blank', ['op_descent_mach', 'op_descent_cas_kts'])
def test_a_row_without_descent_speeds_leaves_the_descent_schedule_to_the_file(blank):
    """Only some airframes need the table's descent speeds, because their PIANO
    export states none; where the table has none either, the file's own
    schedule is used, and a file with none is then an error."""
    overrides = piano_overrides({**ROW, blank: ''})

    assert overrides.descent_cas_low_kts is None
    assert overrides.descent_cas_high_kts is None
    assert overrides.descent_mach is None
    assert overrides.descent_crossover_altitude_ft is None


def test_cruise_speeds_fly_the_operating_mach():
    speeds = cruise_speeds(ROW)

    assert speeds.mach == 0.789
    assert speeds.cas_high == pytest.approx(300 * KNOTS_TO_MPS)
    assert speeds.cas_low == pytest.approx(250 * KNOTS_TO_MPS)


def test_a_row_without_an_operating_speed_names_the_airframe():
    with pytest.raises(ValueError, match=SAVE_AS):
        cruise_speeds({**ROW, 'op_cruise_mach': ''})


def test_the_airframe_table_is_keyed_by_save_as(manifest):
    assert list(read_airframes(manifest)) == [SAVE_AS]


def test_an_airframe_table_missing_a_column_is_an_error(tmp_path):
    path = tmp_path / 'bad.csv'
    path.write_text('save_as,oew_kg\nX,1\n')

    with pytest.raises(ValueError, match='n_engines'):
        read_airframes(path)


###########################################
######   Model keys                  ######
###########################################


def test_a_model_key_splits_into_airframe_and_engine_uid():
    """Airframe names contain underscores; EDB UIDs do not."""
    known = {'B787-10_560boe_v18', 'A320-251N_77t_azu_v17'}

    assert split_model_key('B787-10_560boe_v18_01P17GE211', known) == (
        'B787-10_560boe_v18',
        '01P17GE211',
    )


def test_a_model_key_for_an_unknown_airframe_is_an_error():
    with pytest.raises(ValueError, match='NOPE_01P17GE211'):
        split_model_key('NOPE_01P17GE211', {'B787-10_560boe_v18'})


###########################################
######   Batch                       ######
###########################################


def test_a_model_is_built_for_each_key_and_named_after_it(
    manifest, piano_root, tmp_path
):
    key = f'{SAVE_AS}_{EDB_UID}'

    report = batch([key], manifest, piano_root, tmp_path)

    assert isinstance(report, BatchReport)
    assert report.built == [key]
    assert report.failed == {}
    model = PerformanceModel.load(tmp_path / 'models' / f'{key}.toml')
    assert model.lto_performance.ICAO_UID == EDB_UID
    assert model.operating_empty_mass_kg == pytest.approx(42555.1)
    assert model.number_of_engines == 2
    assert model.speeds.cruise.mach == 0.789


def test_one_failing_key_does_not_stop_the_others(manifest, piano_root, tmp_path):
    good = f'{SAVE_AS}_{EDB_UID}'
    bad = f'{SAVE_AS}_NOTANENGINE'

    report = batch([bad, good], manifest, piano_root, tmp_path)

    assert report.built == [good]
    assert list(report.failed) == [bad]
    assert 'NOTANENGINE' in report.failed[bad]
    assert not (tmp_path / 'models' / f'{bad}.toml').exists()


def test_missing_piano_exports_are_reported_not_raised(manifest, tmp_path):
    key = f'{SAVE_AS}_{EDB_UID}'

    report = batch([key], manifest, tmp_path / 'no-such-piano', tmp_path)

    assert report.built == []
    assert SAVE_AS in report.failed[key]


def test_existing_models_are_skipped_unless_forced(manifest, piano_root, tmp_path):
    key = f'{SAVE_AS}_{EDB_UID}'
    batch([key], manifest, piano_root, tmp_path)

    again = batch([key], manifest, piano_root, tmp_path)
    forced = batch([key], manifest, piano_root, tmp_path, force=True)

    assert again.skipped == [key]
    assert again.built == []
    assert forced.built == [key]


def test_an_airframes_exports_are_parsed_once_for_all_its_engines(
    manifest, piano_root, tmp_path, monkeypatch
):
    """The climb, cruise and descent tables do not depend on the engine, so
    only the LTO block differs between the variants of one airframe."""
    calls = []
    real_load = PianoData.load.__func__

    def counting_load(cls, *args, **kwargs):
        calls.append(args)
        return real_load(cls, *args, **kwargs)

    monkeypatch.setattr(PianoData, 'load', classmethod(counting_load))

    batch(
        [f'{SAVE_AS}_{EDB_UID}', f'{SAVE_AS}_NOTANENGINE'],
        manifest,
        piano_root,
        tmp_path,
    )

    assert len(calls) == 1


###########################################
######   Keys from a mission database ######
###########################################


def test_model_keys_are_the_distinct_keys_in_a_mission_database(tmp_path):
    db = tmp_path / 'missions.sqlite'
    con = sqlite3.connect(db)
    con.execute('CREATE TABLE flights (id INTEGER, performance_model_key TEXT)')
    con.executemany(
        'INSERT INTO flights VALUES (?, ?)',
        [(1, 'B_2'), (2, 'A_1'), (3, 'A_1'), (4, None)],
    )
    con.commit()
    con.close()

    # Flights with no key are the ones that fall back to the aircraft type;
    # there is no model to build for them.
    assert read_model_keys(db) == ['A_1', 'B_2']


def test_the_command_is_registered_and_documented():
    """Smoke test only: the behaviour is covered above."""
    result = CliRunner().invoke(make_piano_models, ['--help'])

    assert result.exit_code == 0
    assert '--mission-db-file' in result.output
    assert '--sn-override-file' in result.output
    assert '--keys-from-table' in result.output


def test_an_export_with_no_descent_schedule_uses_the_airframe_tables_speeds(
    manifest, piano_root, tmp_path
):
    """Two real PIANO descent exports state no airspeed schedule."""
    descent = piano_root / 'data' / 'performance' / SAVE_AS / f'{SAVE_AS}_descent'
    lines = descent.read_text().splitlines(keepends=True)
    descent.write_text(
        ''.join(line for line in lines if 'Airspeed schedule' not in line)
    )
    key = f'{SAVE_AS}_{EDB_UID}'

    report = batch([key], manifest, piano_root, tmp_path)

    assert report.failed == {}
    model = PerformanceModel.load(tmp_path / 'models' / f'{key}.toml')
    assert model.speeds.descent.mach == 0.780


###########################################
######   Smoke numbers at build time  ######
###########################################

SN_HEADER = 'uid,sn_idle,sn_app,sn_co,sn_to,proxy_uid,method\n'


def _engine_without_nvpm(monkeypatch, sn):
    """Make the sample engine look like an old one: no nvPM measurements, and
    the given smoke numbers (idle, approach, climb-out, take-off)."""
    real = EDBEntry.get_engine.__func__

    def get_engine(cls, excel_file, uid, strict=True):
        entry = real(cls, excel_file, uid, strict=strict)
        return dataclasses.replace(
            entry,
            SN_matrix=ThrustModeValues(*sn),
            nvPM_mass_matrix=ThrustModeValues(0.0),
            nvPM_num_matrix=ThrustModeValues(0.0),
        )

    monkeypatch.setattr(EDBEntry, 'get_engine', classmethod(get_engine))


def test_an_engine_with_no_nvpm_data_and_a_blank_smoke_number_is_not_built(
    manifest, piano_root, tmp_path, monkeypatch
):
    """The model stores the engine's smoke numbers, so one with a blank could
    only fail flight by flight during a run. It fails here instead, by key."""
    _engine_without_nvpm(monkeypatch, (float('nan'), 2.0, 5.0, 7.0))
    key = f'{SAVE_AS}_{EDB_UID}'

    report = batch([key], manifest, piano_root, tmp_path)

    assert report.built == []
    assert EDB_UID in report.failed[key] and 'smoke number' in report.failed[key]
    assert not (tmp_path / 'models' / f'{key}.toml').exists()


def test_a_zero_smoke_number_is_enough_to_build(
    manifest, piano_root, tmp_path, monkeypatch
):
    _engine_without_nvpm(monkeypatch, (0.0, 2.0, 5.0, 7.0))
    key = f'{SAVE_AS}_{EDB_UID}'

    assert batch([key], manifest, piano_root, tmp_path).built == [key]


def test_smoke_numbers_from_the_override_file_are_written_into_the_model(
    manifest, piano_root, tmp_path, monkeypatch
):
    _engine_without_nvpm(monkeypatch, (float('nan'),) * 4)
    overrides = tmp_path / 'overrides.csv'
    overrides.write_text(SN_HEADER + f'{EDB_UID},1.5,3.5,7.5,9.5,XXX,copy\n')
    key = f'{SAVE_AS}_{EDB_UID}'

    report = batch([key], manifest, piano_root, tmp_path, sn_override_file=overrides)

    assert report.built == [key]
    model = PerformanceModel.load(tmp_path / 'models' / f'{key}.toml')
    sn = model.lto.SN_matrix
    assert [sn[m] for m in ThrustMode] == [1.5, 3.5, 7.5, 9.5]


def test_an_engine_with_measured_nvpm_is_built_whatever_its_smoke_numbers(
    manifest, piano_root, tmp_path, monkeypatch
):
    """Direct nvPM measurements are used in preference to smoke numbers, so a
    blank smoke number does not matter then."""
    real = EDBEntry.get_engine.__func__

    def get_engine(cls, excel_file, uid, strict=True):
        entry = real(cls, excel_file, uid, strict=strict)
        return dataclasses.replace(
            entry, SN_matrix=ThrustModeValues(*(float('nan'),) * 4)
        )

    monkeypatch.setattr(EDBEntry, 'get_engine', classmethod(get_engine))
    key = f'{SAVE_AS}_{EDB_UID}'

    assert batch([key], manifest, piano_root, tmp_path).built == [key]


###########################################
######   Keys from the per-key table  ######
###########################################


def _key_table(tmp_path, *rows):
    path = tmp_path / 'keys.csv'
    fields = ['performance_model_key', 'edb_uid', *ROW]
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({**ROW, **row})
    return path


def test_keys_are_read_from_a_table_with_one_row_per_key(tmp_path):
    table = _key_table(
        tmp_path,
        {'performance_model_key': f'{SAVE_AS}_B2', 'edb_uid': 'B2'},
        {'performance_model_key': f'{SAVE_AS}_A1', 'edb_uid': 'A1'},
        {'performance_model_key': '', 'edb_uid': ''},  # a deck no engine flies on
    )

    assert read_table_keys(table) == [f'{SAVE_AS}_A1', f'{SAVE_AS}_B2']


def test_a_key_that_is_not_its_airframe_and_engine_uid_is_an_error(tmp_path):
    """The key is how a flight finds its model, so a row whose key does not
    name its own airframe and engine record would map flights to the wrong
    engine without any other sign."""
    table = _key_table(
        tmp_path, {'performance_model_key': f'{SAVE_AS}_A1', 'edb_uid': 'B2'}
    )

    with pytest.raises(ValueError, match=f'{SAVE_AS}_A1.*B2'):
        read_table_keys(table)


def test_a_key_listed_twice_is_an_error(tmp_path):
    row = {'performance_model_key': f'{SAVE_AS}_A1', 'edb_uid': 'A1'}

    with pytest.raises(ValueError, match='twice'):
        read_table_keys(_key_table(tmp_path, row, row))


def test_rows_of_one_airframe_that_disagree_are_an_error(tmp_path):
    """Airframe columns repeat on every key's row; the batch reads one of them."""
    table = _key_table(
        tmp_path,
        {'performance_model_key': f'{SAVE_AS}_A1', 'edb_uid': 'A1'},
        {'performance_model_key': f'{SAVE_AS}_B2', 'edb_uid': 'B2', 'oew_kg': '1'},
    )

    with pytest.raises(ValueError, match=f'{SAVE_AS}.*oew_kg'):
        read_airframes(table)


###########################################
######   APU                         ######
###########################################


def test_the_model_names_the_apu_of_the_airframe_row(manifest, piano_root, tmp_path):
    key = f'{SAVE_AS}_{EDB_UID}'

    batch([key], manifest, piano_root, tmp_path)

    model = PerformanceModel.load(tmp_path / 'models' / f'{key}.toml')
    assert model.apu_name == 'APU 131-9'
    assert model.apu is not None and model.apu.fuel_kg_per_s > 0


def _with_apu(tmp_path, apu_name):
    path = tmp_path / 'airframes.csv'
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(ROW))
        writer.writeheader()
        writer.writerow({**ROW, 'apu_name': apu_name})
    return path


@pytest.mark.parametrize('apu_name', ['', '   '])
def test_a_blank_apu_is_a_build_failure_naming_the_airframe(
    piano_root, tmp_path, apu_name
):
    """No APU emissions would be computed for the model, silently."""
    key = f'{SAVE_AS}_{EDB_UID}'

    report = batch([key], _with_apu(tmp_path, apu_name), piano_root, tmp_path)

    assert report.built == []
    assert SAVE_AS in report.failed[key] and 'apu_name' in report.failed[key]


def test_an_apu_that_is_not_in_the_apu_database_is_a_build_failure(
    piano_root, tmp_path
):
    """AEIC would otherwise use a zero-emission APU with only a warning."""
    key = f'{SAVE_AS}_{EDB_UID}'

    report = batch([key], _with_apu(tmp_path, 'APU 999-X'), piano_root, tmp_path)

    assert report.built == []
    assert 'APU 999-X' in report.failed[key]


def test_the_explicit_no_apu_entry_is_accepted(piano_root, tmp_path):
    """The APU database has a "None" APU for an aircraft that has none."""
    key = f'{SAVE_AS}_{EDB_UID}'

    report = batch([key], _with_apu(tmp_path, 'None'), piano_root, tmp_path)

    assert report.built == [key]


def test_an_airframe_table_without_an_apu_column_is_an_error(tmp_path):
    path = tmp_path / 'bad.csv'
    fields = [c for c in ROW if c != 'apu_name']
    path.write_text(','.join(fields) + '\n' + ','.join(ROW[c] for c in fields) + '\n')

    with pytest.raises(ValueError, match='apu_name'):
        read_airframes(path)
