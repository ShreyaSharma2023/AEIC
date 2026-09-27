"""Reads operating empty mass out of a PIANO plane file.

A PIANO plane file is a flat text export of an aircraft's design parameters,
one `key    value    ;comment` line per parameter (plus a quoted, multi-line
free-text header comment). Operating empty mass is the `*freeze-oew*` line,
in kg; PIANO writes no unit comment on it.
"""

# TODO: Remove this when we move to Python 3.14+.
from __future__ import annotations

import re

_FREEZE_OEW_RE = re.compile(r'^\s*\*freeze-oew\*\s+(\S+)')


def read_operating_empty_mass(path: str) -> float:
    """Read operating empty mass [kg] from a PIANO plane file's `*freeze-oew*`
    line.

    Raises:
        FileNotFoundError: If `path` does not exist.
        ValueError: If the file has no `*freeze-oew*` line, or its value is
            not a number. Real PIANO plane files were checked directly: 586 of
            625 have this line; the 39 that don't are preliminary/concept
            aircraft with no other field mass can be derived from, and are
            expected to raise this - use the `oew_kg` manifest column to
            supply a value for them instead.
    """
    with open(path, encoding='utf-8') as f:
        for line in f:
            match = _FREEZE_OEW_RE.match(line)
            if match is None:
                continue
            try:
                return float(match.group(1))
            except ValueError:
                raise ValueError(
                    f'{path}: "*freeze-oew*" value {match.group(1)!r} is not a number.'
                ) from None

    raise ValueError(
        f'{path}: no "*freeze-oew*" line found. Supply operating empty mass '
        'for this aircraft via the manifest\'s "oew_kg" column instead.'
    )
