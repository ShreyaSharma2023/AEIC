"""Tests for building PIANO performance models in batch from an airframe table.

The PIANO exports behind these tests are the dummy fixtures in
`tests/data/performance/piano`, and the EDB is the one-engine sample, so they
assert that models are built, named and wired to the right inputs, never that
the numbers are physically plausible.
"""

import csv
import shutil

import pytest

from AEIC.config import config
from AEIC.parsers.piano_reader import PianoData
from AEIC.performance.models import PerformanceModel
from AEIC.performance.piano_batch import (
    BatchReport,
    climb_masses_kg,
    cruise_speeds,
    piano_overrides,
    read_airframes,
    run_batch,
    split_model_key,
)
from AEIC.units import KNOTS_TO_MPS, POUNDS_TO_KG

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
