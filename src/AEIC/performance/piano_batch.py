"""Build PIANO performance models in batch, one per performance model key.

A performance model key names a PIANO airframe and one engine variant flown on
it, as ``<airframe>_<EDB UID>`` (``B787-10_560boe_v18_01P17GE211``). The
climb, cruise and descent tables belong to the airframe and are parsed once;
only the LTO block, which comes from the engine's Emissions Databank entry,
differs between the variants of an airframe.

The airframe table is a CSV with one row per PIANO airframe. Its columns are
documented with the aircraft performance database parser, which owns it.
"""

# TODO: Remove this when we move to Python 3.14+.
from __future__ import annotations

import csv
import math
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from AEIC.parsers.piano_reader import PianoData, PianoOverrides
from AEIC.parsers.piano_reader.plane_file import read_operating_empty_mass
from AEIC.performance.apu import lookup_apu
from AEIC.performance.edb import EDBEntry
from AEIC.performance.model_builder import build_piano_model, write_performance_model
from AEIC.performance.models.base import LTOPerformanceInput
from AEIC.performance.sn_overrides import (
    SNOverrides,
    apply_sn_override,
    read_sn_overrides,
)
from AEIC.performance.types import SpeedData, ThrustMode
from AEIC.units import FEET_TO_METERS, KNOTS_TO_MPS, POUNDS_TO_KG
from AEIC.utils.standard_atmosphere import cas_to_tas, speed_of_sound_at_altitude

REQUIRED_COLUMNS = (
    'save_as',
    'piano_plane_file',
    'n_engines',
    'aircraft_class',
    'max_payload_kg',
    'oew_kg',
    'climb_start_masses_lb',
    'op_cruise_mach',
    'op_climb_mach',
    'op_climb_cas_kts',
    'op_descent_mach',
    'op_descent_cas_kts',
    'apu_name',
)

LOW_CAS_KTS = 250.0
"""Calibrated airspeed below FL100 [knots]. The airframe table has no value for
it, and 250 kt is the usual limit there."""

THRUST_FRACTIONS = (0.07, 0.30, 0.85, 1.0)
"""ICAO LTO cycle thrust settings: idle, approach, climb-out, take-off."""


@dataclass
class BatchReport:
    """What a batch did, by performance model key."""

    built: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    """Key -> the reason it could not be built."""


###########################################
######   Airframe table              ######
###########################################


def read_airframes(path: str | Path) -> dict[str, dict[str, str]]:
    """Read the airframe table into rows keyed by ``save_as``."""
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f'{path}: missing columns {", ".join(missing)}')
        rows: dict[str, dict[str, str]] = {}
        for row in reader:
            save_as = row['save_as'].strip()
            first = rows.setdefault(save_as, row)
            # A table with one row per performance model key repeats the
            # airframe's columns on each of its rows.
            differing = [c for c in REQUIRED_COLUMNS if first[c] != row[c]]
            if differing:
                raise ValueError(
                    f'{path}: rows of airframe {save_as} disagree on '
                    f'{", ".join(differing)}'
                )
    return rows


def read_table_keys(path: str | Path) -> list[str]:
    """The performance model keys of a table with one row per key, sorted.

    Such a table has the columns ``performance_model_key`` and ``edb_uid``
    beside the airframe's. A row with no key (an airframe no engine is flown
    on) is skipped.

    Raises:
        ValueError: If a key is not its row's ``<save_as>_<edb_uid>``, or is
            listed twice. A flight finds its model by the key, so a key that
            does not name its own row's engine record would give flights the
            wrong engine.
    """
    keys: list[str] = []
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        needed = ('performance_model_key', 'edb_uid', 'save_as')
        missing = [c for c in needed if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f'{path}: missing columns {", ".join(missing)}')
        for row in reader:
            key = row['performance_model_key'].strip()
            if not key:
                continue
            expected = f'{row["save_as"].strip()}_{row["edb_uid"].strip()}'
            if key != expected:
                raise ValueError(
                    f'{path}: key {key} is not its airframe and engine UID, {expected}'
                )
            if key in keys:
                raise ValueError(f'{path}: key {key} is listed twice')
            keys.append(key)
    return sorted(keys)


def _number(row: dict[str, str], column: str) -> float:
    value = row[column].strip()
    if not value:
        raise ValueError(f'{row["save_as"]}: "{column}" is blank in the airframe table')
    return float(value)


def climb_masses_kg(value: str) -> list[float]:
    """Parse ``"128,000 / 141,000"`` (pounds) into kilograms."""
    return [
        float(mass.replace(',', '').strip()) * POUNDS_TO_KG for mass in value.split('/')
    ]


def crossover_altitude_ft(cas_kts: float, mach: float) -> float:
    """Altitude [feet] at which a climb at `cas_kts` reaches `mach`, in the
    standard atmosphere: above it the climb flies the Mach, below it the CAS.

    Raises:
        ValueError: If the CAS is already faster than the Mach at sea level, so
            there is no crossover.
    """

    def mach_of_cas(altitude_m: float) -> float:
        return float(
            cas_to_tas(cas_kts * KNOTS_TO_MPS, altitude_m)
            / speed_of_sound_at_altitude(altitude_m)
        )

    low, high = 0.0, 25_000.0  # metres; a CAS Mach rises with altitude
    if mach_of_cas(low) >= mach or mach_of_cas(high) <= mach:
        raise ValueError(
            f'no crossover altitude for {cas_kts:g} kt CAS and Mach {mach:g}'
        )
    for _ in range(60):
        mid = 0.5 * (low + high)
        if mach_of_cas(mid) < mach:
            low = mid
        else:
            high = mid
    return 0.5 * (low + high) / FEET_TO_METERS


def _descent_overrides(row: dict[str, str]) -> dict[str, float]:
    """The descent schedule from an airframe row, all of it or none. Some PIANO
    descent exports state no schedule; where the table has none either, the
    file's own is used, and a file with none is then an error."""
    if not row['op_descent_cas_kts'].strip() or not row['op_descent_mach'].strip():
        return {}
    cas_high = _number(row, 'op_descent_cas_kts')
    mach = _number(row, 'op_descent_mach')
    return {
        'descent_cas_low_kts': LOW_CAS_KTS,
        'descent_cas_high_kts': cas_high,
        'descent_mach': mach,
        'descent_crossover_altitude_ft': crossover_altitude_ft(cas_high, mach),
    }


def piano_overrides(row: dict[str, str]) -> PianoOverrides:
    """The climb inputs PIANO's exports do not contain, from an airframe row."""
    cas_high = _number(row, 'op_climb_cas_kts')
    mach = _number(row, 'op_climb_mach')
    descent = _descent_overrides(row)
    return PianoOverrides(
        **descent,
        climb_masses_kg=climb_masses_kg(row['climb_start_masses_lb']),
        climb_cas_low_kts=LOW_CAS_KTS,
        climb_cas_high_kts=cas_high,
        climb_mach=mach,
        climb_crossover_altitude_ft=crossover_altitude_ft(cas_high, mach),
    )


def cruise_speeds(row: dict[str, str]) -> SpeedData:
    """Cruise speed schedule: the operating Mach, and the climb CAS (which the
    airframe table gives) above FL100."""
    return SpeedData(
        cas_low=LOW_CAS_KTS * KNOTS_TO_MPS,
        cas_high=_number(row, 'op_climb_cas_kts') * KNOTS_TO_MPS,
        mach=_number(row, 'op_cruise_mach'),
    )


def _apu_name(row: dict[str, str]) -> str:
    """The airframe's APU, which must be in the APU database.

    AEIC computes APU emissions only for a model that names one, and treats an
    unknown name as an APU with no emissions, so a blank or misspelt name would
    silently drop them. The database's "None" entry says an aircraft has none.

    Raises:
        ValueError: If the name is blank or not in the APU database.
    """
    name = row['apu_name'].strip()
    if not name:
        raise ValueError(
            f'{row["save_as"]}: "apu_name" is blank in the airframe table; use '
            '"None" for an aircraft with no APU'
        )
    if lookup_apu(name) is None:
        raise ValueError(f'{row["save_as"]}: APU "{name}" is not in the APU database')
    return name


def _operating_empty_mass(row: dict[str, str], plane_files_dir: Path | None) -> float:
    """The table's value, else the plane file's ``*freeze-oew*``."""
    if row['oew_kg'].strip():
        return float(row['oew_kg'])
    if plane_files_dir is None:
        raise ValueError(
            f'{row["save_as"]}: no oew_kg in the airframe table and no directory '
            'of PIANO plane files to read it from'
        )
    return read_operating_empty_mass(str(plane_files_dir / row['piano_plane_file']))


###########################################
######   Model keys                  ######
###########################################


def split_model_key(key: str, airframes: Iterable[str]) -> tuple[str, str]:
    """Split ``<airframe>_<EDB UID>`` into its parts. Airframe names contain
    underscores and EDB UIDs do not, so the split is at the last one."""
    airframe, _, uid = key.rpartition('_')
    if not airframe or airframe not in set(airframes):
        raise ValueError(f'{key}: no airframe "{airframe}" in the airframe table')
    return airframe, uid


def read_model_keys(mission_db: str | Path) -> list[str]:
    """The distinct performance model keys in a mission database, sorted.
    Flights without one fall back to their aircraft type, so have no model."""
    con = sqlite3.connect(f'file:{mission_db}?mode=ro', uri=True)
    try:
        rows = con.execute(
            'SELECT DISTINCT performance_model_key FROM flights '
            'WHERE performance_model_key IS NOT NULL ORDER BY 1'
        ).fetchall()
    finally:
        con.close()
    return [key for (key,) in rows]


###########################################
######   Batch                       ######
###########################################


def _lto(
    engine_file: Path, uid: str, sn_overrides: SNOverrides | None
) -> LTOPerformanceInput:
    """LTO data for an EDB engine, with its smoke numbers replaced if the
    override file has the engine.

    A missing nvPM entry is not an error: the emissions calculation then
    estimates nvPM from the smoke numbers. The model stores those, so they
    must be usable here.

    Raises:
        ValueError: If the engine has neither nvPM measurements at all four
            modes nor a smoke number at each (0 is a valid smoke number; blank
            and -1 are not).
    """
    entry = EDBEntry.get_engine(engine_file, uid, strict=False)
    if sn_overrides is not None:
        entry = apply_sn_override(entry, sn_overrides)
    measured = all(
        entry.nvPM_mass_matrix[m] > 0.0 and entry.nvPM_num_matrix[m] > 0.0
        for m in ThrustMode
    )
    blank = [
        m.name
        for m in ThrustMode
        if math.isnan(entry.SN_matrix[m]) or entry.SN_matrix[m] == -1
    ]
    if not measured and blank:
        raise ValueError(
            f'engine {uid} ({entry.engine}) has no nvPM measurements and no '
            f'smoke number at {", ".join(blank)}; it needs a row in the smoke '
            'number override file'
        )
    return LTOPerformanceInput.from_internal(
        entry.make_lto_performance(THRUST_FRACTIONS)
    )


def run_batch(
    keys: Iterable[str],
    airframe_table: str | Path,
    piano_root: str | Path,
    engine_file: str | Path,
    output_dir: str | Path,
    *,
    force: bool = False,
    plane_files_dir: str | Path | None = None,
    sn_override_file: str | Path | None = None,
) -> BatchReport:
    """Build a performance model TOML for each key in `keys`.

    Args:
        keys: Performance model keys, ``<airframe>_<EDB UID>``.
        airframe_table: CSV with one row per PIANO airframe.
        piano_root: Root of the PIANO database, holding the exports at
            ``data/performance/<stem>/<stem>_{climb,cruise,descent}``.
        engine_file: ICAO Emissions Databank workbook.
        output_dir: Directory to write ``<key>.toml`` into.
        force: Rebuild models that already exist. By default they are skipped.
        plane_files_dir: PIANO plane files, to read an operating empty mass
            from when the airframe table has none.
        sn_override_file: CSV of smoke numbers by engine UID that replace the
            Emissions Databank's (see `AEIC.performance.sn_overrides`).

    A key that cannot be built is recorded in the report and does not stop the
    others.
    """
    airframes = read_airframes(airframe_table)
    piano_root = Path(piano_root)
    engine_file = Path(engine_file)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    plane_dir = Path(plane_files_dir) if plane_files_dir is not None else None

    sn_overrides = (
        read_sn_overrides(sn_override_file) if sn_override_file is not None else None
    )

    report = BatchReport()
    parsed: dict[str, PianoData | Exception] = {}

    def piano_data(save_as: str) -> PianoData:
        if save_as not in parsed:
            stem = piano_root / 'data' / 'performance' / save_as / save_as
            try:
                parsed[save_as] = PianoData.load(
                    f'{stem}_cruise',
                    f'{stem}_climb',
                    f'{stem}_descent',
                    overrides=piano_overrides(airframes[save_as]),
                )
            except (ValueError, OSError) as exc:
                parsed[save_as] = exc
        result = parsed[save_as]
        if isinstance(result, Exception):
            raise result
        return result

    for key in keys:
        target = output_dir / f'{key}.toml'
        if target.exists() and not force:
            report.skipped.append(key)
            continue
        try:
            save_as, uid = split_model_key(key, airframes)
            row = airframes[save_as]
            model = build_piano_model(
                piano_data(save_as),
                _lto(engine_file, uid, sn_overrides),
                aircraft_class=row['aircraft_class'],
                number_of_engines=int(row['n_engines']),
                maximum_payload=int(float(row['max_payload_kg'])),
                operating_empty_mass=_operating_empty_mass(row, plane_dir),
                cruise_speeds=cruise_speeds(row),
                apu_name=_apu_name(row),
            )
            write_performance_model(target, model)
        except (ValueError, OSError) as exc:
            report.failed[key] = str(exc)
            continue
        report.built.append(key)

    return report
