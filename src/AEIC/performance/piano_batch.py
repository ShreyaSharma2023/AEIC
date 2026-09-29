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
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from AEIC.parsers.piano_reader import PianoData, PianoOverrides
from AEIC.parsers.piano_reader.plane_file import read_operating_empty_mass
from AEIC.performance.edb import EDBEntry
from AEIC.performance.model_builder import build_piano_model, write_performance_model
from AEIC.performance.models.base import LTOPerformanceInput
from AEIC.performance.types import SpeedData
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
        rows = {row['save_as'].strip(): row for row in reader}
    return rows


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


def piano_overrides(row: dict[str, str]) -> PianoOverrides:
    """The climb inputs PIANO's exports do not contain, from an airframe row."""
    cas_high = _number(row, 'op_climb_cas_kts')
    mach = _number(row, 'op_climb_mach')
    return PianoOverrides(
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


def _lto(engine_file: Path, uid: str) -> LTOPerformanceInput:
    """LTO data for an EDB engine. A missing nvPM entry is not an error: the
    emissions calculation then estimates nvPM from the smoke number."""
    entry = EDBEntry.get_engine(engine_file, uid, strict=False)
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

    A key that cannot be built is recorded in the report and does not stop the
    others.
    """
    airframes = read_airframes(airframe_table)
    piano_root = Path(piano_root)
    engine_file = Path(engine_file)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    plane_dir = Path(plane_files_dir) if plane_files_dir is not None else None

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
                _lto(engine_file, uid),
                aircraft_class=row['aircraft_class'],
                number_of_engines=int(row['n_engines']),
                maximum_payload=int(float(row['max_payload_kg'])),
                operating_empty_mass=_operating_empty_mass(row, plane_dir),
                cruise_speeds=cruise_speeds(row),
            )
            write_performance_model(target, model)
        except (ValueError, OSError) as exc:
            report.failed[key] = str(exc)
            continue
        report.built.append(key)

    return report
