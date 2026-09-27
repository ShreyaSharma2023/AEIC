"""Tests for reading operating empty mass out of a PIANO plane file.

The fixtures in `tests/data/parsers/piano_reader/plane_files` are small
synthetic excerpts, not real PIANO exports, but copy its real quirks: CRLF
line endings, a quoted multi-line header comment, `;`-prefixed comments, and
the `*freeze-oew*` key's literal asterisks. Real PIANO plane files were
inspected directly: 586 of 625 have `*freeze-oew*`; the 39 that don't are
preliminary/concept aircraft that never reached production, and have no other
field OEW can be derived from (only `mto-mass`, which would need an assumed
OEW/MTOW ratio - the maintainer has already rejected that kind of shortcut
for PIANO data)."""

import pytest

from AEIC.parsers.piano_reader.plane_file import read_operating_empty_mass

PLANE_FILES_DIR = 'parsers/piano_reader/plane_files'


def test_reads_the_operating_empty_mass(test_data_dir):
    oew = read_operating_empty_mass(
        str(test_data_dir / PLANE_FILES_DIR / 'with_oew.txt')
    )
    assert oew == pytest.approx(35402.8556)


def test_raises_a_clear_error_when_freeze_oew_is_absent(test_data_dir):
    path = str(test_data_dir / PLANE_FILES_DIR / 'without_oew.txt')
    with pytest.raises(ValueError, match=r'oew_kg') as excinfo:
        read_operating_empty_mass(path)
    assert path in str(excinfo.value)


def test_raises_a_clear_error_when_freeze_oew_is_not_a_number(test_data_dir):
    path = str(test_data_dir / PLANE_FILES_DIR / 'malformed_oew.txt')
    with pytest.raises(ValueError, match=r'freeze-oew'):
        read_operating_empty_mass(path)


def test_raises_a_clear_error_when_the_file_does_not_exist(test_data_dir):
    path = str(test_data_dir / PLANE_FILES_DIR / 'does_not_exist.txt')
    with pytest.raises(FileNotFoundError, match='does_not_exist.txt'):
        read_operating_empty_mass(path)
