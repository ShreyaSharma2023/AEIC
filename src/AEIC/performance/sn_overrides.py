"""Replace an engine's smoke numbers from a file.

The ICAO Emissions Databank leaves the smoke number (SN) blank at some modes
for some engines, and SCOPE11 needs one at every mode to estimate nvPM. A CSV
of final values per engine UID (``uid``, ``sn_idle``, ``sn_app``, ``sn_co``,
``sn_to``, in the four ICAO modes) stands in for them. The engine keeps its own
fuel flow and gaseous emission indices; only the smoke numbers change. Any
further columns, such as where a value came from, are ignored.

The file is made outside AEIC, which only applies it: choosing a proxy engine
and scaling its values is not done here.
"""

# TODO: Remove this when we move to Python 3.14+.
from __future__ import annotations

import csv
import dataclasses
import logging
import math
from functools import lru_cache
from pathlib import Path

from .edb import EDBEntry
from .types import ThrustModeValues

logger = logging.getLogger(__name__)

COLUMNS = ('sn_idle', 'sn_app', 'sn_co', 'sn_to')
"""The smoke number columns, in ICAO mode order: idle, approach, climb-out,
take-off."""


class SNOverrides(dict[str, ThrustModeValues]):
    """Smoke numbers by engine UID, remembering which engines have been
    reported as using them."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.reported: set[str] = set()


@lru_cache(maxsize=4)
def _read(path: str, modified_ns: int, size: int) -> SNOverrides:
    """Read the file. The modification time and size are arguments only so that
    a file changed on disk is read again."""
    overrides = SNOverrides()
    with open(path, newline='', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        missing = [c for c in ('uid', *COLUMNS) if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f'{path}: missing columns {", ".join(missing)}')
        for row in reader:
            uid = row['uid'].strip()
            if uid in overrides:
                raise ValueError(f'{path}: engine {uid} is listed twice')
            values = []
            for column in COLUMNS:
                try:
                    value = float(row[column])
                except (TypeError, ValueError):
                    value = math.nan
                if not math.isfinite(value) or value < 0.0:
                    raise ValueError(
                        f'{path}: engine {uid}: {column} {row[column]!r} is not a '
                        'finite number of at least 0'
                    )
                values.append(value)
            overrides[uid] = ThrustModeValues(*values)
    return overrides


def read_sn_overrides(path: str | Path) -> SNOverrides:
    """The smoke numbers to use, by engine UID.

    Raises:
        ValueError: If a column is missing, an engine is listed twice, or a
            value is not a finite number of at least 0. Zero is valid: it is a
            measurement below what could be recorded.
    """
    stat = Path(path).stat()
    return _read(str(path), stat.st_mtime_ns, stat.st_size)


def apply_sn_override(entry: EDBEntry, overrides: SNOverrides) -> EDBEntry:
    """`entry` with its smoke numbers replaced, if the file has its engine. The
    first use for an engine is logged."""
    sn = overrides.get(entry.uid)
    if sn is None:
        return entry
    if entry.uid not in overrides.reported:
        overrides.reported.add(entry.uid)
        logger.info(
            f'using the smoke numbers of the override file for engine {entry.uid} '
            f'({entry.engine}), not the EDB\'s'
        )
    return dataclasses.replace(entry, SN_matrix=sn)
