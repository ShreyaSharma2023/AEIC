"""Reader for the PIANO descent details export."""

# TODO: Remove this when we migrate to Python 3.14+.
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from AEIC.parsers.piano_reader.common import (
    _Block,
    _cross_check_fuel_burn,
    _cross_check_tas,
    _deltas,
    _find,
    _parse_schedule,
    _read_lines,
    _reject_malformed,
    _Schedule,
    _split_blocks,
    _tas_from_schedule,
)
from AEIC.performance.types import SpeedData, TableInput
from AEIC.units import (
    FEET_TO_METERS,
    FPM_TO_MPS,
    NAUTICAL_MILES_TO_METERS,
    POUNDS_FORCE_TO_NEWTONS,
    POUNDS_TO_KG,
)

if TYPE_CHECKING:
    from AEIC.parsers.piano_reader.piano_data import PianoOverrides

logger = logging.getLogger(__name__)

# See PIANO parser doc for expected schema
# Descent table columns
DESCENT_COLS = [
    'fl',
    'mass',
    'tas',
    'rocd',
    'fuel_flow',
    'time',
    'distance',
    'burn',
    'fn_per_engine',
]

# Descent idle thrust table columns
DESCENT_IDLE_THRUST_COLS = ['mass', 'idle_thrust_altitude']

# Alt., Time, Dist., Burn, R.o.D., FN/eng
_DESCENT_ROW_COLS = 6

_DESCENT_MASS_RE = re.compile(r'^\s*Mass\s+([\d.]+)')
_IDLE_THRUST_RE = re.compile(r'Idle thrust below\s+([\d.]+)feet')


# ---------------------------------------------------------------------------
# Descent
# ---------------------------------------------------------------------------


def _descent_schedule(blocks: list[_Block], overrides: PianoOverrides) -> _Schedule:
    """Resolve the descent airspeed schedule.

    The schedule is a property of the file: every block that states one must
    state the same. Values the caller supplies replace the file's. Some PIANO
    exports state none, and then the caller must supply all of it.

    Raises:
        ValueError: If two blocks state different schedules, or no block states
            one and the overrides do not supply a complete one.
    """
    stated: _Schedule | None = None
    for n, block in enumerate(blocks):
        block_schedule = _parse_schedule(block.header_lines, descending=True)
        if block_schedule is None:
            continue
        if stated is None:
            stated = block_schedule
        elif block_schedule != stated:
            raise ValueError(
                f'Descent block {n + 1} states the airspeed schedule '
                f'{block_schedule}, which differs from the first block\'s '
                f'{stated}; the airspeed schedule must be the same across the '
                'file'
            )

    supplied = {
        '--descent-cas-low-kts': overrides.descent_cas_low_kts,
        '--descent-cas-high-kts': overrides.descent_cas_high_kts,
        '--descent-mach': overrides.descent_mach,
        '--descent-crossover-altitude-ft': overrides.descent_crossover_altitude_ft,
    }
    if stated is None:
        missing = [name for name, value in supplied.items() if value is None]
        if missing:
            raise ValueError(
                'No descent block states an airspeed schedule, so it must be '
                f'supplied explicitly. Missing: {", ".join(missing)}'
            )
        return _Schedule(
            cas_low_kts=overrides.descent_cas_low_kts,
            cas_high_kts=overrides.descent_cas_high_kts,
            mach=overrides.descent_mach,
            crossover_ft=overrides.descent_crossover_altitude_ft,
        )

    def pick(value: float | None, from_file: float) -> float:
        return value if value is not None else from_file

    return _Schedule(
        cas_low_kts=pick(overrides.descent_cas_low_kts, stated.cas_low_kts),
        cas_high_kts=pick(overrides.descent_cas_high_kts, stated.cas_high_kts),
        mach=pick(overrides.descent_mach, stated.mach),
        crossover_ft=pick(overrides.descent_crossover_altitude_ft, stated.crossover_ft),
    )


def _parse_descent(
    path: str, overrides: PianoOverrides
) -> tuple[SpeedData, TableInput, TableInput]:
    """Parse a PIANO descent details export.

    Returns the descent speed schedule, the descent table and the idle thrust
    table.

    Raises:
        ValueError: If a block has no mass, if two blocks state different
            airspeed schedules, or if no block states one and the overrides do
            not supply it (see `_descent_schedule`).
    """
    lines = _read_lines(path)
    blocks = _split_blocks(lines, 'Descent details', _DESCENT_ROW_COLS)

    data = []
    idle_thrust = []
    schedule = _descent_schedule(blocks, overrides)

    for n, block in enumerate(blocks):
        label = f'Descent block {n + 1}'
        _reject_malformed(label, block, _DESCENT_ROW_COLS)

        mass_match = _find(_DESCENT_MASS_RE, block.header_lines)
        if mass_match is None:
            raise ValueError(f'{label} has no "Mass" header')
        mass = float(mass_match.group(1)) * POUNDS_TO_KG

        idle_match = _find(_IDLE_THRUST_RE, block.header_lines)
        if idle_match is None:
            logger.warning('%s has no "Idle thrust below" header', label)
        else:
            idle_thrust.append([mass, float(idle_match.group(1)) * FEET_TO_METERS])

        rows = block.rows
        if not rows:
            logger.warning('%s: no data rows', label)
            continue

        altitude_m = [row[0] * FEET_TO_METERS for row in rows]
        tas = [_tas_from_schedule(alt, schedule) for alt in altitude_m]
        # PIANO reports rate of descent as a positive number.
        rocd = [-row[4] * FPM_TO_MPS for row in rows]

        d_time = _deltas([row[1] for row in rows])
        d_distance = _deltas([row[2] * NAUTICAL_MILES_TO_METERS for row in rows])
        d_burn = _deltas([row[3] * POUNDS_TO_KG for row in rows])

        _cross_check_tas(label, tas, d_distance, d_time, rocd)
        _cross_check_fuel_burn(label, rows[-1][3] * POUNDS_TO_KG, block.header_lines)

        for i, row in enumerate(rows):
            if d_time[i] == 0:
                logger.warning(
                    '%s: dropping the row at %.0f feet, which spans no time '
                    'so has no defined fuel flow',
                    label,
                    row[0],
                )
                continue
            data.append(
                [
                    row[0] / 100,
                    mass,
                    tas[i],
                    rocd[i],
                    d_burn[i] / d_time[i],
                    row[1],
                    row[2] * NAUTICAL_MILES_TO_METERS,
                    row[3] * POUNDS_TO_KG,
                    row[5] * POUNDS_FORCE_TO_NEWTONS,
                ]
            )

    if not blocks:
        raise ValueError(f'No descent block found in {path}')

    return (
        schedule.to_speed_data(),
        TableInput(
            cols=DESCENT_COLS, data=sorted(data, key=lambda row: (row[1], row[0]))
        ),
        TableInput(
            cols=DESCENT_IDLE_THRUST_COLS,
            data=sorted(idle_thrust, key=lambda row: row[0]),
        ),
    )
